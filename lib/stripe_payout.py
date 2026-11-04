"""Read-only "Money landing soon" snapshot (Jasmine's request, Nov 3, 2026) -- pure computation,
no network. Answers one question: when will money from Stripe actually arrive in the bank?
Paid-by-the-client is not landed-in-the-bank; this module is the difference between the two.

Reuses lib/stripe_recon.py's metadata parsing (parse_metadata_invoice) so a charge's client/
invoice label is derived the exact same way the Stripe reconciliation report already does --
never a second, silently-diverging definition of "which invoice does this charge belong to."

The date rule (the whole point of this feature):
  - A payout that already exists has an authoritative `arrival_date` from Stripe -- use it as-is,
    never recomputed.
  - Money with no payout yet gets an ESTIMATE only, always labeled Expected, built from its
    `available_on` date plus the account's own payout schedule (read from Stripe, never typed in
    by hand, never assumed daily). T+2 is a typical settlement window, never a hardcoded promise.
  - A missing date is shown as missing. A wrong date is worse than none.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation

from .stripe_recon import parse_metadata_invoice

CENTS = Decimal(100)


def _cents_to_dollars(value):
    try:
        return Decimal(value) / CENTS
    except (InvalidOperation, TypeError):
        return Decimal(0)


def _ts_to_date(ts):
    """Unix timestamp -> ISO date string, or None for anything that isn't one. Never guesses a
    date from a missing/malformed timestamp."""
    if not isinstance(ts, (int, float)):
        return None
    try:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def mask_destination(raw_destination):
    """Stripe's payout.destination is either a bare id (ba_..., card_...) or, when expanded, an
    object with last4/bank_name. Only ever returns a masked label -- never a full account or
    routing number, never the raw object or id itself."""
    if isinstance(raw_destination, dict):
        last4 = raw_destination.get('last4')
        bank = raw_destination.get('bank_name') or raw_destination.get('brand') or 'Bank account'
        if last4:
            return f'{bank} …{last4}'
        return bank
    return 'Bank account on file'


def normalize_payout(raw):
    """One Stripe payout object -> the fields this feature actually shows. Returns None for a
    malformed object rather than guessing a figure."""
    if not isinstance(raw, dict) or not raw.get('id'):
        return None
    status = raw.get('status')
    if status not in ('paid', 'pending', 'in_transit', 'canceled', 'failed'):
        status = raw.get('status')  # keep whatever Stripe actually said -- never silently relabel
    return {
        'id': raw.get('id'),
        'amount': _cents_to_dollars(raw.get('amount', 0)),
        'currency': (raw.get('currency') or '').lower(),
        'status': status,
        'arrival_date': _ts_to_date(raw.get('arrival_date')),
        'created': raw.get('created'),
        'method': raw.get('method'),
        'destination_label': mask_destination(raw.get('destination')),
    }


def normalize_balance(raw):
    """GET /v1/balance -> {available: Decimal, pending: Decimal} in USD only (this business has
    no other currency). Missing/malformed entries contribute 0, never crash the snapshot."""
    def usd_total(rows):
        total = Decimal(0)
        for row in (rows or []):
            if isinstance(row, dict) and (row.get('currency') or '').lower() == 'usd':
                total += _cents_to_dollars(row.get('amount', 0))
        return total
    if not isinstance(raw, dict):
        return {'available': Decimal(0), 'pending': Decimal(0)}
    return {'available': usd_total(raw.get('available')), 'pending': usd_total(raw.get('pending'))}


def charge_label(charge, invoice_lookup):
    """Client/invoice label for one (possibly expanded) Stripe charge, joined to the app's own
    invoice ledger the SAME way the Stripe reconciliation report already parses metadata.invoice
    -- never a bare Stripe charge id shown to Jasmine. Falls back to the billing name Stripe
    itself recorded at charge time, and only as a last resort to a generic label."""
    if not isinstance(charge, dict):
        return 'Unidentified payment'
    metadata = charge.get('metadata') if isinstance(charge.get('metadata'), dict) else {}
    parsed = parse_metadata_invoice(metadata.get('invoice'))
    if parsed:
        inv = (invoice_lookup or {}).get(parsed['base_invnum'])
        if inv and inv.get('client'):
            suffix = f" ({parsed['suffix']})" if parsed.get('suffix') else ''
            return f"{inv['client']} #{parsed['base_invnum']}{suffix}"
        # Metadata names an invoice number the app doesn't currently have on file -- still better
        # than a bare charge id, and never silently dropped.
        return f"#{parsed['base_invnum']}"
    billing = charge.get('billing_details') if isinstance(charge.get('billing_details'), dict) else {}
    name = (billing.get('name') or charge.get('description') or '').strip()
    return name or 'Unidentified payment'


def normalize_balance_transaction(raw, invoice_lookup):
    """One Stripe balance_transaction (type='charge', `source` expanded to the full Charge) ->
    the fields the release timeline needs. `net` (not `amount`) is used for the dollar figure --
    Stripe's balance_transaction.amount is gross; .net is already net of Stripe's own fee, which
    is what actually lands in the balance. Returns None for a refund/fee/adjustment/payout row or
    a malformed object -- this module only ever times client PAYMENTS landing, nothing else."""
    if not isinstance(raw, dict) or raw.get('type') not in ('charge', 'payment'):
        return None
    net = _cents_to_dollars(raw.get('net', 0))
    if net <= 0:
        return None  # a negative/zero net here is a refund-shaped row, not an incoming payment
    available_on = _ts_to_date(raw.get('available_on'))
    if not available_on:
        return None
    charge = raw.get('source') if isinstance(raw.get('source'), dict) else {}
    return {
        'id': raw.get('id'),
        'net': net,
        'status': raw.get('status'),
        'available_on': available_on,
        'label': charge_label(charge, invoice_lookup),
    }


def estimate_landing_date(available_on, schedule):
    """Expected (never guaranteed) bank-arrival date once funds with this available_on date get
    swept into a payout, using the Stripe account's OWN payout schedule -- never a hardcoded
    T+2. Returns (date_iso, note) on success, or (None, reason) when the schedule can't support
    an estimate (unknown/manual) -- never a fabricated date.

    `schedule` is Stripe's own account.settings.payouts.schedule shape:
      {'interval': 'daily'|'weekly'|'monthly'|'manual', 'delay_days': int,
       'weekly_anchor': 'monday'..'sunday', 'monthly_anchor': 1..31}
    """
    if not isinstance(schedule, dict) or not schedule.get('interval'):
        return None, 'Payout schedule unknown — check the Stripe Dashboard.'
    interval = schedule['interval']
    try:
        base = dt.date.fromisoformat(available_on)
    except (TypeError, ValueError):
        return None, 'No available-on date to estimate from.'

    if interval == 'manual':
        return None, 'This account pays out manually — no automatic payout is scheduled.'

    if interval == 'daily':
        candidate = base + dt.timedelta(days=1)
    elif interval == 'weekly':
        anchors = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        anchor = (schedule.get('weekly_anchor') or 'friday').lower()
        target_wd = anchors.index(anchor) if anchor in anchors else 4
        days_ahead = (target_wd - base.weekday()) % 7
        candidate = base + dt.timedelta(days=days_ahead or 7)
    elif interval == 'monthly':
        anchor_day = schedule.get('monthly_anchor') or 1
        year, month = base.year, base.month
        if base.day >= anchor_day:
            month += 1
            if month > 12:
                month, year = 1, year + 1
        last_day_of_month = (dt.date(year, month % 12 + 1, 1) - dt.timedelta(days=1)).day if month < 12 else 31
        candidate = dt.date(year, month, min(anchor_day, last_day_of_month))
    else:
        return None, f'Unrecognized payout schedule interval ({interval}).'

    # Weekend/holiday handling: Stripe defines "next business day" precisely (federal holidays
    # included); this estimate only has weekday information, so it rolls a weekend forward and
    # says so explicitly rather than quietly claiming Saturday/Sunday delivery.
    while candidate.weekday() >= 5:
        candidate += dt.timedelta(days=1)
    return candidate.isoformat(), 'Expected — Stripe\'s actual payout schedule may move this by a day or two; bank posting can lag another day or two beyond that.'


def group_timeline(transactions, payouts, schedule):
    """Groups balance transactions by their RELEASE date (available_on) -- "released on the same
    date" is Jasmine's own definition of "coming at the same time" (spec Nov 3, 2026, item 4).
    Each group's landing date uses a real payout's arrival_date when one already exists for that
    release date, otherwise an Expected estimate from the account's payout schedule. Sorted by
    release date ascending (soonest first)."""
    today = dt.date.today().isoformat()
    upcoming_payouts = sorted(
        [p for p in payouts if p.get('status') in ('pending', 'in_transit') and p.get('arrival_date')],
        key=lambda p: p['arrival_date'])

    by_date = {}
    for tx in transactions:
        by_date.setdefault(tx['available_on'], []).append(tx)

    rows = []
    for release_date in sorted(by_date.keys()):
        items = by_date[release_date]
        released = release_date <= today and all(t['status'] == 'available' for t in items)
        landing_date, landing_is_estimate, landing_note = None, True, None
        # A real payout already covers this release date if its arrival_date is on/after the
        # release date -- the earliest such payout is the one these funds actually ride in.
        covering = next((p for p in upcoming_payouts if p['arrival_date'] >= release_date), None)
        if covering:
            landing_date, landing_is_estimate, landing_note = covering['arrival_date'], False, None
        else:
            landing_date, landing_note = estimate_landing_date(release_date, schedule)
            landing_is_estimate = True
        rows.append({
            'released_date': release_date,
            'released': released,
            'landing_date': landing_date,
            'landing_is_estimate': landing_is_estimate,
            'landing_note': landing_note,
            'total_amount': sum((t['net'] for t in items), Decimal(0)),
            'items': [{'label': t['label'], 'amount': t['net']} for t in items],
        })
    return rows


def build_snapshot(balance_raw, payouts_raw, balance_transactions_raw, schedule_raw, invoice_lookup):
    """Orchestrates the whole "Money landing soon" payload from already-fetched Stripe objects.
    Never makes a network call itself -- see api/webhook.py's stripe_payout_snapshot() for the
    fetch side. Dollar amounts come back as Decimal; the caller formats/serializes them.

    Owner revision (Nov 3, 2026, after a production bug report): the headline is FOUR tiles,
    always shown together, never a single "either/or" card -- Available Now and Pending (the
    Stripe Balance, exactly as before) PLUS In Transit (money that already left the Balance in a
    real payout, Stripe's own arrival_date) and Next Expected (the next payout still queued, or
    -- when nothing is queued separately -- the in-transit payout's own info repeated so the
    tile is never blank, or, only when no payout exists at all, a schedule estimate). A real
    payout's arrival_date is NEVER replaced by an estimate; an estimate only ever fills a tile
    that would otherwise have nothing real to show."""
    balance = normalize_balance(balance_raw)
    payouts = [p for p in (normalize_payout(p) for p in (payouts_raw or [])) if p]
    transactions = [t for t in (normalize_balance_transaction(t, invoice_lookup) for t in (balance_transactions_raw or [])) if t]

    schedule = schedule_raw if isinstance(schedule_raw, dict) else None
    schedule_known = schedule is not None and bool(schedule.get('interval'))

    in_transit_payouts = sorted((p for p in payouts if p['status'] == 'in_transit' and p['arrival_date']), key=lambda p: p['arrival_date'])
    pending_payouts = sorted((p for p in payouts if p['status'] == 'pending' and p['arrival_date']), key=lambda p: p['arrival_date'])

    in_transit = None
    if in_transit_payouts:
        in_transit = {
            'amount': sum((p['amount'] for p in in_transit_payouts), Decimal(0)),
            'arrival_date': in_transit_payouts[0]['arrival_date'],
        }

    if pending_payouts:
        soonest = pending_payouts[0]
        next_expected = {'amount': soonest['amount'], 'arrival_date': soonest['arrival_date'], 'is_estimate': False}
    elif in_transit:
        # Nothing separately queued after the in-transit payout -- repeat its own info so this
        # tile is never blank (owner rule, Nov 3: "never '—' while an in-transit payout exists").
        next_expected = {'amount': in_transit['amount'], 'arrival_date': in_transit['arrival_date'], 'is_estimate': False}
    else:
        next_expected = None
        pending_tx = [t for t in transactions if t['status'] == 'pending']
        earliest_available_on = min((t['available_on'] for t in pending_tx), default=None)
        if earliest_available_on and schedule_known:
            est_date, _ = estimate_landing_date(earliest_available_on, schedule)
            if est_date:
                same_day_total = sum((t['net'] for t in pending_tx if t['available_on'] == earliest_available_on), Decimal(0))
                next_expected = {'amount': same_day_total, 'arrival_date': est_date, 'is_estimate': True}

    headline = {
        'available': balance['available'],
        'pending': balance['pending'],
        'in_transit': in_transit,
        'next_expected': next_expected,
    }

    timeline = group_timeline(transactions, payouts, schedule)

    return {'headline': headline, 'timeline': timeline, 'schedule_known': schedule_known}
