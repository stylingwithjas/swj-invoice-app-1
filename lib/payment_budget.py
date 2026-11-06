"""Read paid receipts and attach a private, budget-only month."""
import datetime as dt
import re

from .cash_ledger import receipt_events


def month_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise ValueError('Select a valid budget month.')
    dt.date.fromisoformat(value + '-01')
    return value


def can_assign(event):
    return not event['event_id'].endswith(('::PAY?', '::EXT?'))


def payment_method(event, invoice):
    data = invoice.get('data') or {}
    suffix = event['event_id'].split('::', 1)[-1]
    if event['kind'] == 'split_payment':
        data = next((p for p in data.get('payments') or [] if isinstance(p, dict) and str(p.get('id')) == suffix), {})
    elif event['kind'] == 'extension':
        data = next((e for e in data.get('extensions') or []
                     if isinstance(e, dict) and str(e.get('invnum') or '').rsplit('-', 1)[-1] == suffix), {})
        return 'Stripe (Card)' if data.get('payment_id') else 'Other/Manual'
    return 'Stripe (Card)' if (data.get('payment_id') or data.get('stripe_payment_id')
                               or data.get('manual_method') == 'stripe') else 'Other/Manual'


def for_period(db, period):
    month_key(period)
    assignments = db.rows('payment_budget_months', order='event_id.asc')
    invoices = db.rows('invoices', select='invnum,client,data', order='invnum.asc')
    by_invoice = {row['invnum']: row for row in invoices}
    by_id = {row['event_id']: row for row in assignments}
    events, issues = receipt_events(invoices)
    visible = []
    received = budgeted = moved_in = moved_out = 0
    for event in events:
        received_month = event['date'][:7]
        budget_month = received_month
        assignment = by_id.get(event['event_id'])
        if (assignment and can_assign(event) and assignment['received_date'] == event['date']
                and assignment['amount_cents'] == event['amount_cents']):
            budget_month = assignment['budget_month'][:7]
        amount = event['amount_cents']
        if received_month == period:
            received += amount
            if budget_month != period:
                moved_out += amount
        if budget_month == period:
            budgeted += amount
            if received_month != period:
                moved_in += amount
        if period in (received_month, budget_month):
            visible.append({**event, 'budget_month': budget_month, 'can_assign': can_assign(event),
                            'method': payment_method(event, by_invoice.get(event['invnum'], {}))})
    visible.sort(key=lambda event: (event['date'], event['event_id']), reverse=True)
    return {'period': period, 'received_cents': received, 'budgeted_cents': budgeted,
            'moved_in_cents': moved_in, 'moved_out_cents': moved_out, 'events': visible,
            'issues': issues}


def save_assignment(db, payload):
    period = month_key(payload.get('budget_month'))
    invnum = payload.get('invnum')
    event_id = payload.get('event_id')
    if not isinstance(invnum, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', invnum):
        raise ValueError('Select a recorded invoice payment.')
    if not isinstance(event_id, str) or len(event_id) > 160:
        raise ValueError('Select a recorded invoice payment.')
    invoices = db.rows('invoices', {'invnum': 'eq.' + invnum}, select='invnum,client,data', order='invnum.asc')
    events, issues = receipt_events(invoices)
    event = next((row for row in events if row['event_id'] == event_id), None)
    if not event or not can_assign(event) or any('identifier repeats' in issue for issue in issues):
        raise ValueError('This payment has no unique recorded ID to assign.')
    amount = payload.get('amount_cents')
    if type(amount) is not int or event['date'] != payload.get('received_date') or event['amount_cents'] != amount:
        raise ValueError('This payment changed. Reload Finance and try again.')
    if period < event['date'][:7]:
        raise ValueError('Choose the received month or a later month.')
    db.put('payment_budget_months', {'event_id': event_id, 'received_date': event['date'],
                                    'amount_cents': event['amount_cents'], 'budget_month': period + '-01'}, 'event_id')
    return {'budget_month': period}
