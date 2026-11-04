"""Pure cash/reconciliation rules. Integer cents; bank settlements never create receipts.
No I/O and no invoice mutation. Incomplete sources never imply a safe spending balance.
"""
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .stripe_recon import payable_items, parse_metadata_invoice
from .section7 import read_totals

KINDS = {'stripe_settlement', 'card_payment', 'venmo_funding', 'mover_payment',
         'tax_payment', 'owner_draw', 'owner_contribution', 'bank_fee', 'other_reviewed'}


def cents(value):
    if value is None or isinstance(value, bool):
        raise ValueError('A monetary amount is unavailable.')
    try:
        n = Decimal(str(value))
        if not n.is_finite() or abs(n) > Decimal('1000000000'):
            raise ValueError()
        return int((n * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('A valid monetary amount is required.') from None


def dollars(value):
    return format(Decimal(value) / 100, '.2f')


def iso_date(value):
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        raise ValueError('A valid calendar date is required.') from None


def safe_label(value, limit=120):
    # Never echo full account/card numbers from imported bank descriptions.
    return re.sub(r'\d{5,}', '••••', str(value or ''))[:limit]


def fingerprint(row):
    text = '|'.join([str(row['transaction_id']), str(row['account_id']), iso_date(row['date']), str(cents(row['amount']))])
    return hashlib.md5(text.encode()).hexdigest()


def receipt_events(invoices):
    """Use the existing reconciliation receipt classifier, including partials/extensions.
    Parent paid/signature flags cannot override paid split events. No bank or payout input.
    """
    events, issues, seen = [], [], set()
    for item in payable_items(invoices):
        if item['is_test']:
            continue
        try:
            date = iso_date(item['paid_date'])
            amount = cents(item['amount'])
            if amount <= 0:
                raise ValueError()
        except ValueError:
            issues.append('A recorded customer payment has no verifiable date or amount.')
            continue
        event_id = item['id']
        if event_id in seen:
            issues.append('A customer payment identifier repeats; review before relying on totals.')
            continue
        seen.add(event_id)
        events.append({'event_id': event_id, 'date': date, 'amount_cents': amount,
                       'invnum': item['invnum'], 'kind': item['kind'], 'client': safe_label(item['client'])})
        if item.get('amount_is_grand_fallback'):
            issues.append('A legacy paid record uses its stored invoice total; reconcile its receipt evidence.')
    return events, sorted(set(issues))


def review_valid(tx, review, card_rows, mover_rows, payouts=None):
    if not review or tx.get('pending') or tx.get('removed') or review.get('fingerprint') != fingerprint(tx):
        return False
    kind, refs, amount = review.get('kind'), review.get('source_refs') or [], cents(tx['amount'])
    if kind not in KINDS or not isinstance(refs, list) or any(not isinstance(r,str) for r in refs) or len(refs)!=len(set(refs)):
        return False
    if kind == 'card_payment':
        candidates = [card_rows.get(ref.removeprefix('amex:')) for ref in refs if ref.startswith('amex:')]
        return amount > 0 and bool(candidates) and len(candidates) == len(refs) and all(
            c and not c.get('pending') and cents(c['amount']) < 0 and abs((dt.date.fromisoformat(iso_date(c['date']))-dt.date.fromisoformat(iso_date(tx['date']))).days) <= 7
            for c in candidates) and -sum(cents(c['amount']) for c in candidates) == amount
    if kind == 'mover_payment':
        candidates = [mover_rows.get(ref.removeprefix('mover:')) for ref in refs if ref.startswith('mover:')]
        return amount > 0 and bool(candidates) and len(candidates) == len(refs) and all(candidates) and all(
            abs((dt.date.fromisoformat(iso_date(c['paid_date']))-dt.date.fromisoformat(iso_date(tx['date']))).days) <= 7 for c in candidates
        ) and sum(cents(c['amount']) for c in candidates) == amount
    if kind == 'stripe_settlement':
        # Initial confirmation validates against Stripe; persisted evidence is exclusive.
        return amount < 0 and len(refs) == 1 and refs[0].startswith('stripe:po_')
    if kind in {'owner_contribution'}:
        return amount < 0 and not refs
    if kind in {'venmo_funding', 'tax_payment', 'owner_draw', 'bank_fee'}:
        return amount > 0 and not refs
    return kind == 'other_reviewed' and not refs


def validate_review(tx, kind, refs, note, card_rows, mover_rows, payout=None):
    if kind not in KINDS or not isinstance(refs, list) or any(not isinstance(r,str) for r in refs) or len(refs) != len(set(refs)) or len(refs) > 100:
        raise ValueError('Choose a valid review type and distinct source references.')
    if not str(note or '').strip() or len(str(note)) > 500:
        raise ValueError('Add a short confirmation note (maximum 500 characters).')
    candidate = {'kind': kind, 'source_refs': refs, 'fingerprint': fingerprint(tx)}
    if not review_valid(tx, candidate, card_rows, mover_rows):
        raise ValueError('Posted source evidence must match this bank amount and date. Nothing was changed.')
    if kind == 'stripe_settlement':
        if not payout or payout.get('id') != refs[0].removeprefix('stripe:') or payout.get('currency') != 'usd' or payout.get('status') != 'paid' or int(payout.get('amount', 0)) != -cents(tx['amount']):
            raise ValueError('Stripe payout evidence does not match this posted bank deposit.')
    return candidate


def mover_record(raw, valid_jobs, time_entries):
    event_id = str(raw.get('event_id') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', event_id):
        raise ValueError('A unique payment reference is required (letters, digits, dash, colon or underscore).')
    amount = cents(raw.get('amount'))
    if amount <= 0 or Decimal(str(raw['amount'])) * 100 != amount:
        raise ValueError('Payment amounts must be positive and have at most two decimal places.')
    job = str(raw.get('job_invnum') or '')
    if job not in valid_jobs:
        raise ValueError('Assign the mover payment to an existing SWJ job.')
    name = safe_label(raw.get('mover_name'))
    if not name.strip():
        raise ValueError('A mover name is required.')
    date = iso_date(raw.get('paid_date'))
    if date > dt.date.today().isoformat():
        raise ValueError('Only completed payments may be recorded; future payments remain commitments.')
    links = [str(x) for x in raw.get('time_entry_ids', [])]
    if len(set(links)) != len(links) or any(x not in time_entries for x in links):
        raise ValueError('Choose distinct existing time entries.')
    if links and (sum(cents(time_entries[x]['amount']) for x in links) != amount or any(str(time_entries[x].get('invnum')) != job for x in links)):
        raise ValueError('Linked time entries must match the payment amount and job exactly.')
    row = {'event_id': event_id, 'paid_date': date, 'mover_name': name, 'job_invnum': job,
           'amount': dollars(amount), 'source': 'reviewed_csv' if raw.get('source') == 'reviewed_csv' else 'manual_venmo',
           'time_entry_ids': sorted(links)}
    row['fingerprint'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return row


def fresh(stamp, now, hours=24):
    try:
        value = dt.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return dt.timedelta(0) <= now-value <= dt.timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


def snapshot(invoices, cards, card_transactions, bank_accounts, bank_transactions, reviews,
             settings, movers, time_entries, time_links, stripe_rows, stripe_complete, period, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
        raise ValueError('Choose a valid reporting month.')
    receipts, issues = receipt_events(invoices)
    month = lambda date: str(date or '').startswith(period)
    by_card = {r['transaction_id']: r for r in card_transactions}
    by_mover = {r['event_id']: r for r in movers}
    by_review = {r['transaction_id']: r for r in reviews}
    active_bank = [t for t in bank_transactions if not t.get('removed')]
    valid_reviews = {t['transaction_id']: by_review[t['transaction_id']] for t in active_bank
                     if review_valid(t, by_review.get(t['transaction_id']), by_card, by_mover)}
    matched_card_ids = {ref[5:] for r in valid_reviews.values() for ref in r.get('source_refs', []) if ref.startswith('amex:')}
    # Existing card purchases are the only card expense source. Repayment credits cannot
    # become refunds just because they have a minus sign. Unknown credits are surfaced.
    purchases = [r for r in card_transactions if not r.get('pending') and cents(r['amount']) > 0]
    unreviewed_credits = [r for r in card_transactions if not r.get('pending') and cents(r['amount']) < 0 and r['transaction_id'] not in matched_card_ids]
    if unreviewed_credits:
        issues.append('Card credits/refunds not matched to payments need review; recorded costs may be overstated.')
    if any(not r.get('category') for r in purchases if month(r['date'])):
        issues.append('Some card purchases still need categories.')
    collected = sum(r['amount_cents'] for r in receipts if month(r['date']))
    tax = 0
    tax_known = True
    invoice_map = {str(r['invnum']): r for r in invoices}
    for event in receipts:
        if not month(event['date']): continue
        d = invoice_map[event['invnum']].get('data') or {}
        if event['kind'] == 'extension':
            rate = Decimal(str(d.get('taxrate') or 0))/100
            fraction = Decimal(0) if d.get('exempt_reason') else rate/(1+rate)
        else:
            try:
                _, values = read_totals(d, [])
                grand, sales_tax = Decimal(values['grand']), Decimal(values['tax'])
                fraction = Decimal(0) if d.get('exempt_reason') else sales_tax/grand if grand else Decimal(0)
            except (KeyError, TypeError, InvalidOperation, ZeroDivisionError):
                tax_known = False
                issues.append('A receipt tax allocation is unavailable; margin is not final.')
                fraction = Decimal(0)
        tax += int((Decimal(event['amount_cents'])*fraction).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    card_spend = sum(cents(r['amount']) for r in purchases if month(r['date']))
    mover_spend = sum(cents(r['amount']) for r in movers if month(r['paid_date']))
    bank_fees = sum(cents(t['amount']) for t in active_bank if month(t['date']) and valid_reviews.get(t['transaction_id'], {}).get('kind') == 'bank_fee')
    fee_rows = [r for r in stripe_rows if month(r['date'])]
    stripe_fees = sum(cents(r['fee']) + (-cents(r['amount']) if r['type'] == 'stripe_fee' else 0) for r in fee_rows)
    if not stripe_complete:
        issues.append('Actual Stripe fees have not been fully synced for this month.')
    if any(r['type'] in {'refund','payment_refund'} for r in fee_rows):
        issues.append('Stripe refunds require reconciliation with customer payment/tax records; no historical receipt was restated.')
    if any(not r.get('invoice_ref') and r['type'] != 'stripe_fee' for r in fee_rows if cents(r['fee']) != 0):
        issues.append('Some Stripe fees have no job match; check business ownership before relying on margin.')
    total_cost = card_spend + mover_spend + bank_fees + stripe_fees
    revenue = collected-tax
    result = revenue-total_cost
    linked_time = {str(r['time_entry_id']) for r in time_links}
    unpaid = [r for r in time_entries if r.get('approved') and not r.get('paid') and str(r.get('id')) not in linked_time]
    if any(r.get('paid') and str(r.get('id')) not in linked_time for r in time_entries):
        issues.append('Some hours marked paid have no recorded mover payment evidence; labor costs may be incomplete.')
    commitments = sum(cents(r['amount']) for r in unpaid)
    cash_blockers = []
    selected = next((a for a in bank_accounts if a['account_id'] == (settings or {}).get('account_id')), None)
    bank_cash = None
    if selected:
        if selected.get('currency') != 'USD': cash_blockers.append('Select a USD business checking account.')
        try:
            current = cents(selected.get('current_balance'))
            available = cents(selected['available_balance']) if selected.get('available_balance') is not None else current
            bank_cash = min(current, available)
        except ValueError: cash_blockers.append('Checking balance is unavailable.')
        if not fresh(selected.get('balance_synced_at'), now): cash_blockers.append('Refresh the checking balance (last fetched more than 24 hours ago).')
    else: cash_blockers.append('Connect and select the business checking account.')
    if not settings or not settings.get('review_from'): cash_blockers.append('Confirm the checking review start date and reserves.')
    review_from = (settings or {}).get('review_from') or '0001-01-01'
    relevant = [t for t in active_bank if selected and t['account_id']==selected['account_id'] and t['date']>=review_from]
    unresolved = [t for t in relevant if t['transaction_id'] not in valid_reviews]
    if unresolved: cash_blockers.append('Review unmatched or pending checking activity before estimating available cash.')
    if any(t.get('pending') for t in relevant): cash_blockers.append('Pending bank movements may overlap card balances; wait for posting.')
    if selected and (not fresh(selected.get('transactions_synced_at'),now) or selected.get('sync_pending')): cash_blockers.append('Refresh checking transactions; a complete recent sync is required.')
    card_reserve = 0
    if not cards: cash_blockers.append('No card balances are available.')
    for card in cards:
        try:
            card_reserve += max(0, cents(card.get('current_balance')))
        except ValueError: cash_blockers.append('A current card balance is missing.')
        if not fresh(card.get('balance_synced_at'), now): cash_blockers.append('Refresh the card balances before relying on cash after commitments.')
    tax_reserve, buffer = None, None
    try:
        tax_reserve = cents((settings or {}).get('tax_reserve'))
        buffer = cents((settings or {}).get('operating_buffer'))
    except ValueError: cash_blockers.append('Confirm remaining tax reserves and the operating cushion, including explicit zero values.')
    confirmed = str((settings or {}).get('confirmed_at') or '')
    if any(str(valid_reviews.get(t['transaction_id'], {}).get('confirmed_at') or '')>confirmed and valid_reviews.get(t['transaction_id'], {}).get('kind')=='tax_payment' for t in relevant):
        cash_blockers.append('Reconfirm the remaining tax reserve after the recorded tax payment.')
    cash_blockers = sorted(set(cash_blockers))
    after = None if cash_blockers or bank_cash is None else bank_cash-card_reserve-commitments-tax_reserve-buffer
    jobs = {}
    for inv in invoices:
        if str(inv.get('invnum')) not in {r['invnum'] for r in receipts} and not any(str(t.get('job_invnum'))==str(inv.get('invnum')) for t in purchases+movers): continue
        key = str(inv['invnum']); gross = sum(e['amount_cents'] for e in receipts if e['invnum']==key)
        costs = sum(cents(t['amount']) for t in purchases+movers if str(t.get('job_invnum'))==key)
        fees = sum(cents(t['fee']) for t in stripe_rows if t.get('invoice_ref')==key)
        jobs[key] = {'invnum':key,'client':safe_label(inv.get('client')), 'receipts_cents':gross,'recorded_cost_cents':costs+fees,
                     'contribution_cents':gross-costs-fees, 'note':'Lifetime receipts less recorded job costs; includes sales tax and excludes shared overhead. Not net profit.'}
    return {'period':period, 'revenue_source':'Recorded customer payments by received date, regardless of signature; deposits are settlement only.',
            'monthly':{'collected_cents':collected,'sales_tax_cents':tax if tax_known else None,'revenue_ex_tax_cents':revenue if tax_known else None,'card_spend_cents':card_spend,
                       'mover_spend_cents':mover_spend,'stripe_fees_cents':stripe_fees,'bank_fees_cents':bank_fees,'costs_cents':total_cost,
                       'result_cents':result if tax_known else None,'margin_percent':round(result/revenue*100,2) if revenue>0 and tax_known else None,'issues':sorted(set(issues))},
            'cash':{'bank_cents':bank_cash,'card_reserve_cents':card_reserve,'mover_commitments_cents':commitments,
                    'tax_reserve_cents':tax_reserve,'buffer_cents':buffer,'after_commitments_cents':after,'blockers':cash_blockers,
                    'note':'Includes cash carried over from earlier months. Expected Stripe payouts are not added to bank cash. Card current balances are reserved once, not again as expenses.'},
            'jobs':list(jobs.values()),'receipts':receipts,'unresolved_bank_count':len(unresolved),
            'card_credit_review_count':len(unreviewed_credits)}
