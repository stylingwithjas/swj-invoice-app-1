"""Read-only Stripe <-> invoice reconciliation (Phase 1c). Never writes to Stripe or the
database. Independent of Section 7 (lib/section7.py): that report reconciles invoices against
themselves; this one is the only report that compares the app's own records against what
Stripe actually shows as charged, livemode only.

Reuses lib/section7.py's money/date/label primitives and read_totals() pricing reconstruction
directly rather than re-deriving them a second time -- the original Section 0 bug was exactly
this kind of duplicated, silently-diverging math, and these two reports must never disagree
about what a given invoice's grand total or paid status is.
"""
import datetime as dt
import re
from decimal import Decimal, InvalidOperation, localcontext

from .section7 import number, money, printed, date, label, real_invoice, read_totals

ZERO = Decimal(0)
SPLIT_PAY_RE = re.compile(r'^(.+)-PAY(\d+)$')  # mirrors api/webhook.py's SPLIT_PAY_RE exactly
EXT_PAY_RE = re.compile(r'^(.+)-EXT(\d+)$')    # mirrors api/webhook.py's EXT_PAY_RE exactly
DATE_WINDOW_DAYS = 7


def normalize_name(value):
    """Lowercase, punctuation-stripped comparison only -- never used as a sole match signal."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower())).strip()


def parse_metadata_invoice(raw):
    """Parses Stripe metadata.invoice into the same shapes the live payment-link flow already
    produces ("202092-PAY1", "202092-EXT1", or a bare invoice number). Returns None for empty
    metadata -- empty metadata is itself a finding (surfaced verbatim), never treated as a
    bare-invoice match."""
    raw = (raw or '').strip()
    if not raw:
        return None
    m = EXT_PAY_RE.match(raw)
    if m:
        return {'base_invnum': m.group(1), 'kind': 'extension', 'suffix': 'EXT' + m.group(2)}
    m = SPLIT_PAY_RE.match(raw)
    if m:
        return {'base_invnum': m.group(1), 'kind': 'split_payment', 'suffix': 'PAY' + m.group(2)}
    return {'base_invnum': raw, 'kind': 'invoice', 'suffix': None}


def _reconstructed_grand(d):
    try:
        with localcontext() as context:
            context.prec = 40
            _, values = read_totals(d, [])
    except Exception:
        return None
    raw = values.get('grand') if values else None
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def payable_items(rows):
    """One row per thing that could have a Stripe charge behind it: a legacy flat-paid
    invoice, each live payments[] entry, each paid extension. Mirrors lib/section7.py's
    live/test classification and stored-paid-amount fallback chain (amount_paid -> stripe_amount
    -> reconstructed grand) so the two reports never silently disagree about what counts as a
    live receipt or its amount. GS Photography and drafts/system rows are excluded via the
    same real_invoice() filter Section 7 uses."""
    items = []
    for row in rows:
        if not real_invoice(row):
            continue
        d = row.get('data')
        if not isinstance(d, dict):
            continue
        invnum = label(row.get('invnum'))
        client = label(row.get('client'))
        grand = _reconstructed_grand(d)

        payments = d.get('payments')
        if isinstance(payments, list) and payments:
            for p in payments:
                if not isinstance(p, dict) or p.get('status') != 'paid':
                    continue
                try:
                    amount = number(p.get('amount'), 'amount')
                except ValueError:
                    amount = None
                pay_id = str(p.get('id') or '')
                items.append({
                    'kind': 'split_payment', 'id': invnum + '::' + (pay_id or 'PAY?'),
                    'suffix': pay_id, 'invnum': invnum, 'client': client, 'amount': amount,
                    'paid_date': date(p.get('paid_date')),
                    'is_test': p.get('is_test') is True or d.get('is_test') is True,
                    'grand': grand, 'display_label': invnum + (' (' + pay_id + ')' if pay_id else ' (split)'),
                })
        elif d.get('paid') is True:
            amount = None
            for key in ('amount_paid', 'stripe_amount'):
                if d.get(key) not in (None, ''):
                    try:
                        amount = number(d.get(key), key); break
                    except ValueError:
                        pass
            amount_is_grand_fallback = amount is None
            if amount is None:
                amount = grand
            items.append({
                'kind': 'invoice', 'id': invnum + '::', 'suffix': None, 'invnum': invnum,
                'client': client, 'amount': amount, 'paid_date': date(d.get('paid_date')),
                'is_test': d.get('is_test') is True, 'grand': grand,
                'amount_is_grand_fallback': amount_is_grand_fallback, 'display_label': invnum,
            })

        extensions = d.get('extensions')
        if isinstance(extensions, list):
            for e in extensions:
                if not isinstance(e, dict) or e.get('paid') is not True:
                    continue
                try:
                    amount = number(e.get('amount'), 'amount')
                except ValueError:
                    amount = None
                m = EXT_PAY_RE.match(str(e.get('invnum') or ''))
                suffix = ('EXT' + m.group(2)) if m else None
                items.append({
                    'kind': 'extension', 'id': invnum + '::' + (suffix or 'EXT?'),
                    'suffix': suffix, 'invnum': invnum, 'client': client, 'amount': amount,
                    'paid_date': date(e.get('paid_date')),
                    'is_test': e.get('is_test') is True or d.get('is_test') is True,
                    'grand': amount, 'display_label': label(e.get('invnum') or (invnum + '-EXT')),
                })
    return items


def normalize_charge(raw):
    """Normalizes one Stripe charge object (cents -> dollars, unix timestamp -> ISO date).
    Returns None for a malformed object rather than guessing a figure."""
    if not isinstance(raw, dict) or raw.get('id') is None:
        return None
    try:
        amount = Decimal(raw.get('amount', 0)) / 100
        refunded = Decimal(raw.get('amount_refunded', 0)) / 100
    except (InvalidOperation, TypeError):
        return None
    created = raw.get('created')
    charge_date = None
    if isinstance(created, (int, float)):
        try:
            charge_date = dt.datetime.fromtimestamp(created, dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            charge_date = None
    metadata = raw.get('metadata') if isinstance(raw.get('metadata'), dict) else {}
    billing = raw.get('billing_details') if isinstance(raw.get('billing_details'), dict) else {}
    name = billing.get('name') or raw.get('description') or ''
    return {
        'id': raw.get('id'), 'amount': amount, 'refunded_amount': refunded,
        'net_amount': amount - refunded, 'date': charge_date,
        'livemode': raw.get('livemode') is True, 'livemode_confirmed': isinstance(raw.get('livemode'), bool),
        'metadata_raw': str(metadata.get('invoice') or ''), 'metadata': dict(metadata),
        'client_name': label(name), 'status': raw.get('status'),
        'payment_intent': raw.get('payment_intent'),
    }


def _within_window(charge_date, paid_date):
    if not charge_date or not paid_date:
        return False
    try:
        return abs((dt.date.fromisoformat(charge_date) - dt.date.fromisoformat(paid_date)).days) <= DATE_WINDOW_DAYS
    except ValueError:
        return False


def _row_from_item(item):
    # Keyed distinctly from _row_from_charge's fields below (stored_amount, not amount) --
    # a matched/mismatch row merges both dicts, and a shared key name would silently let one
    # side's figure clobber the other's instead of showing both.
    return {'kind': item['kind'], 'invnum': item['invnum'], 'client': item['client'],
            'display_label': item['display_label'], 'stored_amount': money(item['amount']),
            'paid_date': item['paid_date'],
            'amount_is_grand_fallback': item.get('amount_is_grand_fallback', False)}


def _row_from_charge(charge):
    return {'charge_id': charge['id'], 'date': charge['date'], 'charge_amount': money(charge['amount']),
            'refunded_amount': money(charge['refunded_amount']) if charge['refunded_amount'] > ZERO else None,
            'net_amount': money(charge['net_amount']) if charge['refunded_amount'] > ZERO else None,
            'client_name': charge['client_name'], 'metadata': charge['metadata'],
            'metadata_invoice': charge['metadata_raw'], 'status': charge['status']}


def build_report(invoice_rows, stripe_charges_raw, period):
    """Pure computation over already-fetched rows -- no network calls. `period` is YYYY-MM,
    used only to compute per-month totals; every item/charge is still matched regardless of
    month (a charge from one month can legitimately pay an invoice issued another month)."""
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or not 1900 <= int(period[:4]) <= 9999:
        raise ValueError('Invalid review period')

    items = payable_items(invoice_rows)
    live_items = [it for it in items if not it['is_test']]
    test_items = [it for it in items if it['is_test']]

    all_charges = [c for c in (normalize_charge(r) for r in stripe_charges_raw) if c]
    unconfirmed_livemode = [c for c in all_charges if not c['livemode_confirmed']]
    live_charges, test_charges = [], []
    for c in all_charges:
        if not c['livemode_confirmed']:
            continue  # never guessed into either bucket -- see unconfirmed_livemode above
        (live_charges if c['livemode'] else test_charges).append(c)

    # GS Photography charges are identified the same way GS invoices are: metadata pointing to
    # a GS-prefixed invoice number. Excluded from every SWJ list entirely, never mixed in.
    gs_charges, swj_charges = [], []
    for c in live_charges:
        parsed = parse_metadata_invoice(c['metadata_raw'])
        (gs_charges if parsed and parsed['base_invnum'].startswith('GS') else swj_charges).append(c)

    matched_item_ids, matched_charge_ids = set(), set()
    matched, ambiguous_charges, mismatches = [], [], []

    # Tier 1: metadata match. Identity established by metadata; amount must still agree to the
    # cent or this is a mismatch (list 4), never a silent pass-through and never "no match".
    for charge in swj_charges:
        parsed = parse_metadata_invoice(charge['metadata_raw'])
        if not parsed:
            continue
        candidate = None
        for it in live_items:
            if it['id'] in matched_item_ids:
                continue
            if it['invnum'] != parsed['base_invnum']:
                continue
            if parsed['kind'] == 'invoice' and it['kind'] == 'invoice':
                candidate = it; break
            if parsed['kind'] in ('extension', 'split_payment') and it['kind'] == parsed['kind'] and it['suffix'] == parsed['suffix']:
                candidate = it; break
        if candidate is None:
            continue
        matched_charge_ids.add(charge['id'])
        if candidate['amount'] is not None and printed(candidate['amount']) == printed(charge['amount']):
            matched.append({**_row_from_item(candidate), **_row_from_charge(charge),
                             'match_basis': 'metadata', 'status': 'paid_in_full'})
            matched_item_ids.add(candidate['id'])
        else:
            mismatches.append({**_row_from_item(candidate), **_row_from_charge(charge),
                                'match_basis': 'metadata',
                                'difference': money(printed(charge['amount']) - printed(candidate['amount'])) if candidate['amount'] is not None else None,
                                'detail': 'Metadata identifies this pair; the amounts do not agree to the cent.'})
            matched_item_ids.add(candidate['id'])

    # Tier 2: amount + date + client, ALL three required. A charge with more than one
    # equally-qualifying candidate is never auto-picked -- it goes to the review queue
    # (ambiguous_charges) with every candidate named, exactly the Hill $1,280.54 case.
    for charge in swj_charges:
        if charge['id'] in matched_charge_ids:
            continue
        charge_norm_name = normalize_name(charge['client_name'])
        candidates = []
        for it in live_items:
            if it['id'] in matched_item_ids:
                continue
            if it['amount'] is None or printed(it['amount']) != printed(charge['amount']):
                continue
            if not _within_window(charge['date'], it['paid_date']):
                continue
            if not charge_norm_name or normalize_name(it['client']) != charge_norm_name:
                continue
            candidates.append(it)
        if len(candidates) == 1:
            it = candidates[0]
            matched.append({**_row_from_item(it), **_row_from_charge(charge),
                             'match_basis': 'amount_date_client', 'status': 'paid_in_full'})
            matched_item_ids.add(it['id']); matched_charge_ids.add(charge['id'])
        elif len(candidates) > 1:
            ambiguous_charges.append({**_row_from_charge(charge),
                'candidates': [it['display_label'] for it in candidates],
                'detail': str(len(candidates)) + ' stored records match this charge equally (amount, date window, client) -- not auto-matched.'})
            matched_charge_ids.add(charge['id'])

    unmatched_charges = [dict(_row_from_charge(c), ambiguous_candidates=None) for c in swj_charges if c['id'] not in matched_charge_ids] + ambiguous_charges
    unmatched_items = [_row_from_item(it) for it in live_items if it['id'] not in matched_item_ids]

    def month_of(value):
        return (value or '')[:7]

    return {
        'schema_version': 1, 'review_period': period, 'data_source': 'stripe_and_database_records',
        'comparison_basis': 'Gross amounts compared to the cent; Stripe fees are a separate cost, never netted into the match.',
        'invoices_considered': len(items), 'live_charges_considered': len(swj_charges),
        'matched_count': len(matched), 'unmatched_charge_count': len(unmatched_charges),
        'unmatched_item_count': len(unmatched_items), 'mismatch_count': len(mismatches),
        'test_charge_count': len(test_charges), 'test_item_count': len(test_items),
        'gs_charge_count': len(gs_charges), 'unconfirmed_livemode_count': len(unconfirmed_livemode),
        'matched_period_count': sum(1 for r in matched if month_of(r.get('paid_date') or r.get('date')) == period),
        'unmatched_charge_period_count': sum(1 for r in unmatched_charges if month_of(r.get('date')) == period),
        'unmatched_item_period_count': sum(1 for r in unmatched_items if month_of(r.get('paid_date')) == period),
        'mismatch_period_count': sum(1 for r in mismatches if month_of(r.get('paid_date') or r.get('date')) == period),
        'matched': matched, 'unmatched_charges': unmatched_charges, 'unmatched_items': unmatched_items,
        'mismatches': mismatches,
        'limitations': [
            'Matches only the three tiers in order: metadata+amount, then amount+date (+-7 days)+client, then unmatched. Never auto-matches on one signal alone.',
            'Gross amounts only; Stripe processing fees are not reconciled here and never explain away a mismatch.',
            'A missing field (date, amount, metadata) is shown as missing, never defaulted to $0 or guessed.',
            'Paid findings are owner/CPA review questions. No invoice, extension, charge, or refund is created, restated, or changed by this report.',
            'GS Photography invoices and charges are excluded from every list above.',
            'Test-mode (non-livemode) charges are excluded; a charge whose livemode flag could not be confirmed is counted separately, never silently included or excluded.',
        ]}


def csv_report(report, view='all'):
    import csv, io

    def text(value):
        value = str(value if value is not None else '')
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value

    rows_by_view = {
        'matched': [('Matched', r) for r in report['matched']],
        'unmatched_charges': [('Stripe charge, no invoice', r) for r in report['unmatched_charges']],
        'unmatched_items': [('Paid invoice, no Stripe charge', r) for r in report['unmatched_items']],
        'mismatches': [('Amount/status mismatch', r) for r in report['mismatches']],
    }
    rows_by_view['all'] = rows_by_view['matched'] + rows_by_view['unmatched_charges'] + rows_by_view['unmatched_items'] + rows_by_view['mismatches']
    if view not in rows_by_view:
        raise ValueError('Invalid report view')

    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['List', 'Kind', 'Invoice', 'Client (stored)', 'Display label', 'Paid date (stored)',
        'Stored amount', 'Amount is grand-total fallback', 'Stripe charge ID', 'Charge date', 'Charge amount',
        'Refunded amount', 'Net amount', 'Client name (Stripe)', 'Metadata (invoice field)', 'Match basis',
        'Difference', 'Detail'])
    for list_name, r in rows_by_view[view]:
        writer.writerow([text(v) for v in [
            list_name, r.get('kind', ''), r.get('invnum', ''), r.get('client', ''), r.get('display_label', ''),
            r.get('paid_date', ''), r.get('stored_amount', ''), str(r.get('amount_is_grand_fallback', '')),
            r.get('charge_id', ''), r.get('date', ''), r.get('charge_amount', ''), r.get('refunded_amount', ''),
            r.get('net_amount', ''), r.get('client_name', ''), r.get('metadata_invoice', ''),
            r.get('match_basis', ''), r.get('difference', ''), r.get('detail', '')]])
    return output.getvalue()
