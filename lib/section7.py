"""Read-only historical invoice diagnostics; never repairs source figures.

Reconstructs existing api/generate.py readers. Comparisons use printed cents,
not a proposed new pricing policy. A comparison figure is NOT a corrected bill.
"""
import csv
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation, localcontext

ZERO = Decimal(0)
CENT = Decimal('0.01')
SYSTEM = {'PRICING_DEFAULTS', 'TIER_PRICING', 'FINANCE_CONFIG', 'FINANCE_OVERRIDES'}
OVERRIDES = ('subtotal_override', 'tax_override', 'total_override')
RULES = {
    'lines_subtotal': 'Visible lines = subtotal',
    'tax_base': 'Tax = rate × claimed taxable base',
    'tax_full_lines': 'Tax = rate × full visible lines (comparison only, not a taxability claim)',
    'subtotal_tax_total': 'Subtotal + tax = grand total',
    'extension_rate': 'Extension rate = pretax subtotal / 3',
}


def label(value):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))
    return re.sub(r'\d[\d -]{7,}\d', '[masked]', value)[:180]


def number(value, name):
    if isinstance(value, bool) or value in (None, ''):
        raise ValueError('Missing or invalid ' + name)
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError('Missing or invalid ' + name) from None
    if not result.is_finite() or abs(result) > Decimal('1000000000000'):
        raise ValueError('Missing or invalid ' + name)
    return result


def money(value):
    # Match the existing PDF's float formatting, including half-cent boundaries.
    return None if value is None else format(float(value), '.2f')


def printed(value):
    return Decimal(money(value))


def date(value):
    text = str(value or '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return None
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def real_invoice(row):
    key = str(row.get('invnum') or '')
    return key not in SYSTEM and not key.startswith(('DRAFT-', 'GS', 'TAX_REVIEW_'))


def finding(rule, actual=None, comparison=None, detail='', kind='discrepancy', scope='invoice'):
    return {'rule': rule, 'kind': kind, 'actual': money(actual), 'comparison': money(comparison),
            'difference': money(printed(actual) - printed(comparison)) if actual is not None and comparison is not None else None,
            'detail': detail, 'scope': scope}


def compare(rule, actual, comparison, detail='', scope='invoice'):
    if printed(actual) != printed(comparison):
        return finding(rule, actual, comparison, detail, scope=scope)
    return None


def payment_context(d, period, grand=None):
    """Dates describe recorded receipts, not invoice dates or filing eligibility. `grand` is
    this invoice's own independently reconstructed grand total (from read_totals), used only
    as the last-resort fallback for a legacy flat-paid record's amount — mirrors payroll.html's
    finPaymentEvents() exactly (amount_paid, then stripe_amount, then the computed grand), so
    the stored paid amount shown here never silently diverges from the live app's own figure."""
    notes, live_dates, live_paid, test_paid = [], [], False, False
    if d.get('paid') is not None and type(d.get('paid')) is not bool: notes.append('Invalid paid-status flag.')
    payments, extensions = d.get('payments'), d.get('extensions')
    if payments not in (None, []) and not isinstance(payments, list):
        notes.append('Payment list has an invalid shape; receipt timing cannot be fully verified.')
        payments = []
    payments = payments or []
    if extensions not in (None, []) and not isinstance(extensions, list):
        notes.append('Extension list has an invalid shape.')
        extensions = []
    extensions = extensions or []
    events = []
    if payments:
        for p in payments:
            if not isinstance(p, dict):
                notes.append('Invalid payment entry.'); continue
            if p.get('status') == 'paid':
                events.append((p.get('paid_date'), p.get('is_test') is True or d.get('is_test') is True, p.get('amount')))
    elif d.get('paid') is True:
        amount = None
        for key in ('amount_paid', 'stripe_amount'):
            if d.get(key) not in (None, ''):
                amount = d.get(key); break
        if amount is None:
            amount = grand
        events.append((d.get('paid_date'), d.get('is_test') is True, amount))
    for e in extensions:
        if isinstance(e, dict) and e.get('paid') is True:
            events.append((e.get('paid_date'), e.get('is_test') is True or d.get('is_test') is True, e.get('amount')))
    undated, paid_amount = False, ZERO
    for raw_date, is_test, amount in events:
        if is_test:
            test_paid = True; continue
        live_paid = True
        parsed = date(raw_date)
        if parsed:
            live_dates.append(parsed)
        else:
            undated = True
        try:
            paid_amount += number(amount, 'paid amount')
        except ValueError:
            notes.append('A recorded payment is missing a usable amount; the stored paid total shown may be incomplete.')
    # A positive legacy receipt amount without a paid flag is evidence of a
    # possible payment, not permission to guess its date or alter the record.
    for key in ('amount_paid', 'stripe_amount'):
        if d.get(key) not in (None, ''):
            try:
                if number(d[key], key) > 0 and not events:
                    notes.append('Positive recorded receipt without a usable paid event; review payment status and date.')
            except ValueError:
                notes.append('Invalid recorded receipt amount; review payment status.')
    return {'live_paid': live_paid, 'test_paid': test_paid, 'paid_date_unknown': undated,
            'paid_in_review_period': any(day.startswith(period) for day in live_dates),
            'receipt_dates': sorted(set(live_dates)), 'payment_notes': notes,
            'stored_paid_amount': money(paid_amount) if live_paid else None}


def read_totals(d, issues):
    """Independent checks continue even when another input is missing."""
    def get(key, name=None):
        try:
            return number(d.get(key), name or key)
        except ValueError as error:
            issues.append(finding('missing_input', detail=str(error), kind='unverifiable'))
            return None
    base, rate = get('base_price'), get('taxrate')
    if rate is not None and not ZERO <= rate <= 100:
        issues.append(finding('missing_input', detail='Tax rate is outside 0–100%.', kind='unverifiable')); rate = None
    addons = d.get('addons')
    if d.get('base_split') is not None and not isinstance(d.get('base_split'), dict):
        issues.append(finding('missing_input', detail='Invalid base-line split.', kind='unverifiable'))
    if not isinstance(addons, list):
        issues.append(finding('missing_input', detail='Missing or invalid addons list.', kind='unverifiable')); addons = None
    amounts = []
    if addons is not None:
        for index, a in enumerate(addons):
            try:
                if not isinstance(a, dict):
                    raise ValueError('Invalid addon entry ' + str(index + 1))
                amounts.append(number(a.get('price'), 'addon price ' + str(index + 1)))
                if a.get('split') is not None and not isinstance(a.get('split'), dict):
                    issues.append(finding('missing_input', detail='Invalid addon split (line ' + str(index + 1) + ').', kind='unverifiable'))
            except ValueError as error:
                issues.append(finding('missing_input', detail=str(error), kind='unverifiable')); amounts.append(None)
    lines = base + sum(amounts, ZERO) if base is not None and addons is not None and all(a is not None for a in amounts) else None
    visible_lines = printed(base) + sum((printed(a) for a in amounts), ZERO) if lines is not None else None
    overrides = []
    for key in OVERRIDES:
        if d.get(key) in (None, ''):
            overrides.append(None)
        else:
            overrides.append(get(key))
    has_override = any(d.get(k) not in (None, '') for k in OVERRIDES)
    sub, tax, grand = overrides
    taxable, taxable_source = None, None
    mode = 'full_or_overridden'
    if has_override:
        if sub is None and d.get('subtotal_override') in (None, ''):
            sub = lines
        if tax is None and d.get('tax_override') in (None, '') and sub is not None and rate is not None:
            tax = sub * rate / 100
        if grand is None and d.get('total_override') in (None, '') and sub is not None and tax is not None:
            grand = sub + tax
    elif d.get('price_led_tax'):
        mode = 'legacy_price_led'
        if lines is not None and rate is not None:
            try:
                # Mirror the PDF reader, including its existing 0 -> 0.5 fallback.
                rsb = number(d.get('rental_share_base', '0.5') or '0.5', 'rental_share_base')
                if not ZERO <= rsb <= 1:
                    raise ValueError('Invalid legacy rental share')
                rental = base * rsb + sum((amount for a, amount in zip(addons, amounts) if a.get('type', 'rental') != 'service'), ZERO)
                share = rental / lines if lines > Decimal('0.009') else Decimal(1)
                grand = lines * (1 + rate / 100)
                sub = grand / (1 + rate / 100 * share) if rate > 0 else grand
                tax = grand - sub
                taxable = sub * share
                taxable_source = 'Existing legacy price-led effective base; not a new per-line split.'
            except (ValueError, InvalidOperation, ZeroDivisionError) as error:
                issues.append(finding('missing_input', detail=str(error), kind='unverifiable'))
    else:
        sub = lines
        if sub is not None and rate is not None:
            tax = sub * rate / 100; grand = sub + tax

    if d.get('rental_service_split'):
        mode = 'per_line_split'
        taxable = get('rental_total')
        service = get('service_total')
        taxable_source = 'Stored rental_total (no reclassification).'
        if taxable is not None and service is not None and visible_lines is not None:
            mismatch = compare('split_totals', printed(taxable) + printed(service), visible_lines, 'Stored rental + service totals versus visible line prices.')
            if mismatch: issues.append(mismatch)
    elif d.get('pricing_mode') == 'rate_card':
        mode = 'rate_card'
        if addons is not None and all(a is not None for a in amounts) and all(a.get('type') in ('rental', 'service') for a in addons):
            taxable = sum((a for item, a in zip(addons, amounts) if item['type'] == 'rental'), ZERO)
            taxable_source = 'Stored rate-card rental line types.'
        else:
            taxable = None
            issues.append(finding('missing_input', detail='Cannot verify rate-card taxable base without stored line types.', kind='unverifiable'))
    elif taxable is None:
        taxable = sub
        taxable_source = 'Full/override mode labels the whole subtotal as taxable.'

    # Saved split details can claim a rental base even when a manual override
    # incorrectly hid the split totals. Report that contradiction, do not guess
    # that their absence authorized full-subtotal taxation.
    has_visible_split = isinstance(d.get('base_split'), dict) or bool(addons and any(isinstance(a, dict) and isinstance(a.get('split'), dict) for a in addons))
    split_rental, split_service, complete = ZERO, ZERO, lines is not None
    if has_visible_split and addons is not None:
        for index, (amount, split, kind) in enumerate([(base, d.get('base_split'), 'rental')] + [(a, item.get('split') if isinstance(item, dict) else None, item.get('type', 'rental') if isinstance(item, dict) else 'rental') for item, a in zip(addons, amounts)]):
            if amount is None:
                complete = False; continue
            if split is None:
                if index == 0:
                    complete = False
                elif kind == 'service':
                    split_service += printed(amount)
                else:
                    split_rental += printed(amount)
                continue
            try:
                if not isinstance(split, dict): raise ValueError('Invalid line split')
                rent, svc = number(split.get('rental'), 'line rental'), number(split.get('service'), 'line service')
                mismatch = compare('line_split', printed(rent) + printed(svc), printed(amount), 'Stored line split versus its price (line ' + str(index + 1) + ').')
                if mismatch: issues.append(mismatch)
                split_rental += printed(rent); split_service += printed(svc)
            except ValueError as error:
                complete = False
                issues.append(finding('missing_input', detail=str(error), kind='unverifiable'))
        if not d.get('rental_service_split'):
            issues.append(finding('split_presentation', detail='Saved per-line split detail is present while the bottom split-total flag is off.'))
            if complete:
                taxable, taxable_source = split_rental, 'Sum of saved visible rental splits; bottom split flag is off.'
        if complete and d.get('rental_service_split'):
            for key, calculated in (('rental_total', split_rental), ('service_total', split_service)):
                try:
                    mismatch = compare('split_lines_vs_totals', number(d.get(key), key), calculated, 'Saved ' + key + ' versus per-line detail.')
                    if mismatch: issues.append(mismatch)
                except ValueError:
                    pass

    if visible_lines is not None and sub is not None:
        mismatch = compare('lines_subtotal', sub, visible_lines)
        if mismatch: issues.append(mismatch)
    if taxable is not None and rate is not None and tax is not None:
        mismatch = compare('tax_base', tax, taxable * rate / 100,
                           'Manual fields are included in this read-only audit; this does not authorize a correction.')
        if mismatch: issues.append(mismatch)
    if mode == 'legacy_price_led' and visible_lines is not None and rate is not None and tax is not None:
        # Owner decision (Nov 2, 2026): an additional, independent comparison against the
        # full visible-lines amount — NOT a claim that the full amount is taxable (the
        # rental/service split above remains the primary measure). Scoped to legacy
        # price-led invoices only: there, the claimed taxable base above is itself a
        # reconstruction from the same backward formula that produced the tax figure, so it
        # can pass the tax_base check by construction without ever surfacing how far that
        # claimed base has drifted from the true lines total. A per-line-split or rate-card
        # invoice's taxable base is an explicit, trusted stored value instead — comparing
        # those against full lines would misfire on every correctly-split invoice, since a
        # real rental/service split is expected to differ from full-lines tax by design.
        mismatch = compare('tax_full_lines', tax, visible_lines * rate / 100,
                           'Comparison only, not a correction — a large gap here may mean the claimed taxable base itself needs review, not that the full amount is taxable.')
        if mismatch: issues.append(mismatch)
    if sub is not None and tax is not None and grand is not None:
        mismatch = compare('subtotal_tax_total', grand, printed(sub) + printed(tax))
        if mismatch: issues.append(mismatch)
    if visible_lines is not None:
        # Owner decision (Nov 2, 2026): compare against the true visible-lines total, not the
        # stored/printed `sub` — for a legacy price-led invoice, `sub` is itself a
        # reconstruction that can differ from the lines total (that gap is exactly what the
        # lines_subtotal check above exists to catch); using it here would silently inherit
        # that same drift into the extension-rate comparison instead of surfacing it cleanly.
        try:
            ext = number(d.get('extrate'), 'extrate')
            mismatch = compare('extension_rate', ext, printed(visible_lines) / 3,
                'Rate-card mode uses its stored monthly rental rate; a visible-lines / 3 difference in that mode requires contextual review rather than repricing.' if mode == 'rate_card' else '')
            if mismatch: issues.append(mismatch)
        except ValueError as error:
            issues.append(finding('extension_rate', detail=str(error), kind='unverifiable'))
    values = {k: money(v) for k, v in [('visible_lines', visible_lines), ('subtotal', sub), ('tax', tax), ('grand', grand), ('taxable_base', taxable)]}
    values['taxrate_percent'] = str(rate) if rate is not None else None
    values['taxable_base_source'] = taxable_source
    values['subtotal_source'] = 'stored override' if d.get('subtotal_override') not in (None, '') else 'reconstructed existing PDF reader'
    values['tax_source'] = 'stored override' if d.get('tax_override') not in (None, '') else 'reconstructed existing PDF reader'
    values['grand_source'] = 'stored override' if d.get('total_override') not in (None, '') else 'reconstructed existing PDF reader'
    return mode, values


def check_extensions(d, issues):
    """Audit nested charged extensions without rewriting old inclusive fee fields."""
    extensions = d.get('extensions') or []
    if not isinstance(extensions, list):
        issues.append(finding('extension_breakdown', detail='Invalid extension list.', kind='unverifiable')); return
    for index, e in enumerate(extensions):
        scope = 'extension ' + str(index + 1)
        if not isinstance(e, dict):
            issues.append(finding('extension_breakdown', detail='Invalid extension record.', kind='unverifiable', scope=scope)); continue
        try:
            amount = number(e.get('amount'), 'extension amount')
            tax = number(e.get('tax'), 'extension tax')
            fee = number(e.get('fee'), 'extension fee')
            rate = number(e.get('taxrate', d.get('taxrate')), 'extension tax rate')
            # Before Phase 0, inclusive fee was the gross amount (Board's field
            # name was misleading). Retain that fact and compare its implied net.
            if e.get('tax_inclusive') is True and printed(fee) == printed(amount):
                fee = amount - tax
            for rule, actual, expected in [('extension_tax', tax, fee * rate / 100), ('extension_total', amount, printed(fee) + printed(tax))]:
                mismatch = compare(rule, actual, expected, 'Existing inclusive/add-on amounts; no source values changed.', scope)
                if mismatch: issues.append(mismatch)
        except ValueError as error:
            issues.append(finding('extension_breakdown', detail=str(error), kind='unverifiable', scope=scope))


def check_invoice(row, period):
    result = {'invnum': label(row.get('invnum')), 'client': label(row.get('client')),
              'invdate': date(row.get('invdate')), 'pricing_mode': 'unknown',
              'override_fields': [], 'override_intent': 'none', 'issues': [], 'values': {}}
    d = row.get('data')
    if not isinstance(d, dict):
        result.update(payment_context({}, period))
        result['issues'].append(finding('missing_input', detail='Invoice data is not an object.', kind='unverifiable'))
        return result
    result['override_fields'] = [k for k in OVERRIDES if d.get(k) not in (None, '')]
    result['override_intent'] = 'not provable from stored total fields' if result['override_fields'] else 'none'
    if isinstance(d.get('manual_totals_fields'), list):
        result['override_intent'] = 'explicit field provenance recorded; unverified legacy fields remain review items'
    if d.get('extension') or d.get('balance_update'):
        result['issues'].append(finding('document_type', detail='Separate extension/balance document; base-package rules cannot be applied without its parent context.', kind='unverifiable'))
    else:
        with localcontext() as context:
            context.prec = 40
            result['pricing_mode'], result['values'] = read_totals(d, result['issues'])
    # grand feeds payment_context()'s legacy-flat-paid amount fallback only — the same
    # reconstructed figure finComputeGrandForEvents()/finInvoiceSplit().grand uses in
    # payroll.html, so a stored paid amount here never silently diverges from the app's own.
    grand = None
    grand_value = result['values'].get('grand') if result['values'] else None
    if grand_value is not None:
        try: grand = number(grand_value, 'grand')
        except ValueError: pass
    result.update(payment_context(d, period, grand))
    check_extensions(d, result['issues'])
    if result['paid_date_unknown']:
        result['issues'].append(finding('payment_timing', detail='A recorded paid event has no valid receipt date.', kind='unverifiable'))
    for note in result['payment_notes']:
        result['issues'].append(finding('payment_context', detail=note, kind='unverifiable'))
    return result


def build_report(rows, period):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or not 1900 <= int(period[:4]) <= 9999:
        raise ValueError('Invalid review period')
    checked, excluded = [], 0
    for row in rows:
        if real_invoice(row):
            checked.append(check_invoice(row, period))
        else:
            excluded += 1
    flagged = [r for r in checked if r['issues']]
    for r in flagged:
        r['has_discrepancy'] = any(i['kind'] == 'discrepancy' for i in r['issues'])
        r['has_unverifiable'] = any(i['kind'] == 'unverifiable' for i in r['issues'])
    return {'schema_version': 1, 'review_period': period, 'data_source': 'stored_database_records',
            'comparison_basis': 'Printed cents; a $0.01 mismatch is reported. Original issued PDFs are not verified.',
            'records_read': len(rows), 'invoices_checked': len(checked), 'excluded_records': excluded,
            'flagged_count': len(flagged), 'discrepancy_count': sum(r['has_discrepancy'] for r in flagged),
            'unverifiable_count': sum(r['has_unverifiable'] for r in flagged),
            'paid_period_discrepancy_count': sum(r['has_discrepancy'] and r['paid_in_review_period'] for r in flagged),
            'paid_date_unknown_count': sum(r['paid_date_unknown'] for r in checked),
            # Owner decision (Nov 2, 2026): name these records, not just count them — the
            # count alone can't confirm the review-period paid set is complete, since any one
            # of these might actually belong to the period but can't be placed without a date.
            'paid_date_unknown_records': [{'invnum': r['invnum'], 'client': r['client']} for r in checked if r['paid_date_unknown']],
            'paid_period_unverifiable_count': sum(r['has_unverifiable'] and r['paid_in_review_period'] for r in flagged),
            'results': flagged,
            'limitations': [
                'This list cannot detect invoices missing entirely from the database (including an unrecorded job such as Deji).',
                'No original issued or signed PDF is inspected; reconstructed values are labeled and are not replacement figures.',
                'Manual overrides and rate-card extension differences require context; a finding does not authorize repricing.',
                'Paid findings are owner/CPA review questions. No invoices are restated, regenerated, charged, emailed or updated.',
                'Drafts, GS Photography and known settings/review rows are excluded; no staging-status or paid-status filter hides real invoices.',
            ]}


def filtered_rows(report, view):
    if view == 'all': return report['results']
    if view == 'paid_period': return [r for r in report['results'] if r['paid_in_review_period']]
    if view == 'paid': return [r for r in report['results'] if r['live_paid']]
    if view == 'unverifiable': return [r for r in report['results'] if r['has_unverifiable']]
    raise ValueError('Invalid report view')


def csv_report(report, view='all'):
    def text(value):
        value = str(value or '')
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Report fingerprint', 'Review period', 'Invoice', 'Client', 'Invoice date', 'Live paid',
        'Stored paid amount', 'Receipt in review period', 'Paid date unknown', 'Recorded receipt dates', 'Mode',
        'Override fields', 'Finding type', 'Rule', 'Scope', 'Existing/reconstructed value',
        'Comparison value (not a correction)', 'Difference', 'Detail', 'Subtotal source', 'Tax source',
        'Grand source', 'Taxable base source'])
    for row in filtered_rows(report, view):
        for f in row['issues']:
            writer.writerow([text(v) for v in [report.get('fingerprint'), report['review_period'], row['invnum'], row['client'],
                row['invdate'], str(row['live_paid'])]]
                + [row['stored_paid_amount']]
                + [text(v) for v in [str(row['paid_in_review_period']), str(row['paid_date_unknown']),
                ', '.join(row['receipt_dates']), row['pricing_mode'], ', '.join(row['override_fields']), f['kind'], f['rule'], f['scope']]]
                + [f['actual'], f['comparison'], f['difference']]
                + [text(v) for v in [f['detail'], row['values'].get('subtotal_source'), row['values'].get('tax_source'), row['values'].get('grand_source'), row['values'].get('taxable_base_source')]])
    return output.getvalue()
