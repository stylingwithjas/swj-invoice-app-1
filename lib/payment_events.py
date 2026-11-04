"""Option A recorded receipts for server reports. No I/O or source-data mutation."""
from decimal import Decimal, ROUND_HALF_UP
from .section7 import date, number


def event(raw, amount):
    received = date(raw.get('paid_date'))
    try:
        value = number(amount, 'recorded receipt').quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    except ValueError:
        return None
    if not received or value <= 0:
        return None
    return {'date': received, 'amount': float(value),
            'method': 'Stripe (Card)' if raw.get('payment_id') or raw.get('stripe_payment_id') or raw.get('manual_method') == 'stripe' else 'Other/Manual'}


def base(d, grand):
    if not isinstance(d, dict) or d.get('is_test'):
        return []
    splits = d.get('payments')
    if isinstance(splits, list) and splits:
        return [e for p in splits if isinstance(p, dict) and p.get('status') == 'paid' and not p.get('is_test')
                if (e := event(p, p.get('amount'))) is not None]
    if d.get('paid') is not True:
        return []
    amount = None
    for field in ('amount_paid', 'stripe_amount'):
        try:
            amount = number(d.get(field), field)
            break
        except ValueError:
            pass
    if amount is None:
        amount = grand(d)
    receipt = event(d, amount)
    return [receipt] if receipt else []


def extensions(d):
    if not isinstance(d, dict) or d.get('is_test'):
        return []
    rows = d.get('extensions')
    if not isinstance(rows, list):
        return []
    return [{**raw, **e, 'paid_date': e['date']} for raw in rows
            if isinstance(raw, dict) and raw.get('paid') is True and not raw.get('is_test')
            if (e := event(raw, raw.get('amount'))) is not None]
