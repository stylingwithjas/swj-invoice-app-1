import datetime as dt
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.stripe_payout import (
    build_snapshot, estimate_landing_date, charge_label, normalize_balance_transaction,
    normalize_payout, normalize_balance, mask_destination, group_timeline,
)


def ts(iso_date, hour=12):
    return int(dt.datetime.combine(dt.date.fromisoformat(iso_date), dt.time(hour, 0), tzinfo=dt.timezone.utc).timestamp())


class EstimateLandingDateTests(unittest.TestCase):
    """The date rule is the whole feature -- never a hardcoded T+2, always schedule-driven,
    always labeled Expected, weekends/holidays rolled forward, manual schedules refuse to guess."""

    def test_daily_rolls_to_next_business_day_across_a_weekend(self):
        # Friday available_on -> daily cadence would land Saturday -> rolled to Monday.
        date, note = estimate_landing_date('2026-11-06', {'interval': 'daily'})  # a Friday
        self.assertEqual(date, '2026-11-09')  # the following Monday
        self.assertIn('Expected', note)

    def test_weekly_uses_the_accounts_own_anchor_not_a_fixed_day(self):
        friday_anchor, _ = estimate_landing_date('2026-11-02', {'interval': 'weekly', 'weekly_anchor': 'friday'})
        wednesday_anchor, _ = estimate_landing_date('2026-11-02', {'interval': 'weekly', 'weekly_anchor': 'wednesday'})
        self.assertNotEqual(friday_anchor, wednesday_anchor)
        self.assertEqual(dt.date.fromisoformat(friday_anchor).weekday(), 4)
        self.assertEqual(dt.date.fromisoformat(wednesday_anchor).weekday(), 2)

    def test_monthly_uses_next_occurrence_of_anchor_day(self):
        # Already past the 15th this month -> rolls to next month's 15th.
        date, _ = estimate_landing_date('2026-11-20', {'interval': 'monthly', 'monthly_anchor': 15})
        self.assertEqual(dt.date.fromisoformat(date).month, 12)
        self.assertEqual(dt.date.fromisoformat(date).day, 15)

    def test_manual_schedule_never_guesses_a_date(self):
        date, note = estimate_landing_date('2026-11-06', {'interval': 'manual'})
        self.assertIsNone(date)
        self.assertIn('manual', note.lower())

    def test_unknown_schedule_never_guesses_a_date(self):
        date, note = estimate_landing_date('2026-11-06', None)
        self.assertIsNone(date)
        self.assertIn('unknown', note.lower())

    def test_never_hardcodes_t_plus_2(self):
        # Same available_on, two different schedules -> two different estimates. A hardcoded
        # T+2 would give the same answer regardless of the account's actual schedule.
        daily, _ = estimate_landing_date('2026-11-02', {'interval': 'daily'})
        weekly, _ = estimate_landing_date('2026-11-02', {'interval': 'weekly', 'weekly_anchor': 'friday'})
        t_plus_2 = (dt.date.fromisoformat('2026-11-02') + dt.timedelta(days=2)).isoformat()
        self.assertNotEqual(daily, t_plus_2)
        self.assertNotEqual(weekly, t_plus_2)


class LabelingTests(unittest.TestCase):
    def test_charge_label_joins_through_app_invoice_ledger(self):
        charge = {'metadata': {'invoice': '2020128-PAY1'}, 'billing_details': {'name': 'Someone Else'}}
        label = charge_label(charge, {'2020128': {'client': 'Baker Staging'}})
        self.assertEqual(label, 'Baker Staging #2020128 (PAY1)')

    def test_charge_label_never_shows_a_bare_charge_id(self):
        charge = {'id': 'ch_abcdef123456', 'metadata': {}, 'billing_details': {}}
        label = charge_label(charge, {})
        self.assertNotIn('ch_abcdef123456', label)

    def test_charge_label_falls_back_to_billing_name_without_metadata(self):
        charge = {'metadata': {}, 'billing_details': {'name': 'Cruson Client'}}
        self.assertEqual(charge_label(charge, {}), 'Cruson Client')

    def test_charge_label_names_invoice_even_when_app_has_no_record_of_it(self):
        charge = {'metadata': {'invoice': '999999'}, 'billing_details': {}}
        label = charge_label(charge, {})
        self.assertEqual(label, '#999999')

    def test_mask_destination_never_shows_full_account_or_routing_number(self):
        label = mask_destination({'bank_name': 'Chase', 'last4': '4821', 'routing_number': '021000021'})
        self.assertIn('4821', label)
        self.assertNotIn('021000021', label)


class GroupingTests(unittest.TestCase):
    """Acceptance: two payments on different available_on dates -> two separate rows; two
    payments on the same date -> one grouped row with the combined total. Never merge different
    dates into one total; never split a single date into multiple rows."""

    def test_different_release_dates_produce_separate_rows(self):
        tx = [
            {'net': Decimal('100'), 'status': 'pending', 'available_on': '2026-11-10', 'label': 'A'},
            {'net': Decimal('50'), 'status': 'pending', 'available_on': '2026-11-12', 'label': 'B'},
        ]
        rows = group_timeline(tx, [], {'interval': 'daily'})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['released_date'], '2026-11-10')
        self.assertEqual(rows[1]['released_date'], '2026-11-12')
        self.assertEqual(rows[0]['total_amount'], Decimal('100'))
        self.assertEqual(rows[1]['total_amount'], Decimal('50'))

    def test_same_release_date_produces_one_grouped_row_with_combined_total(self):
        tx = [
            {'net': Decimal('100'), 'status': 'pending', 'available_on': '2026-11-10', 'label': 'A'},
            {'net': Decimal('50'), 'status': 'pending', 'available_on': '2026-11-10', 'label': 'B'},
        ]
        rows = group_timeline(tx, [], {'interval': 'daily'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['total_amount'], Decimal('150'))
        self.assertEqual(len(rows[0]['items']), 2)

    def test_rows_sorted_by_release_date_ascending(self):
        tx = [
            {'net': Decimal('10'), 'status': 'pending', 'available_on': '2026-12-01', 'label': 'late'},
            {'net': Decimal('10'), 'status': 'pending', 'available_on': '2026-11-05', 'label': 'early'},
        ]
        rows = group_timeline(tx, [], {'interval': 'daily'})
        self.assertEqual([r['released_date'] for r in rows], ['2026-11-05', '2026-12-01'])

    def test_real_payout_arrival_date_used_instead_of_estimate_when_one_already_covers_it(self):
        tx = [{'net': Decimal('150'), 'status': 'available', 'available_on': '2026-11-08', 'label': 'Released'}]
        payouts = [normalize_payout({
            'id': 'po_1', 'status': 'pending', 'arrival_date': ts('2026-11-12'), 'amount': 15000,
            'currency': 'usd', 'method': 'standard', 'destination': {'bank_name': 'Chase', 'last4': '4821'},
        })]
        rows = group_timeline(tx, payouts, {'interval': 'weekly', 'weekly_anchor': 'friday'})
        self.assertEqual(rows[0]['landing_date'], '2026-11-12')
        self.assertFalse(rows[0]['landing_is_estimate'])

    def test_estimate_used_when_no_payout_covers_the_release_date_yet(self):
        tx = [{'net': Decimal('150'), 'status': 'pending', 'available_on': '2026-11-20', 'label': 'Later'}]
        rows = group_timeline(tx, [], {'interval': 'daily'})
        self.assertIsNotNone(rows[0]['landing_date'])
        self.assertTrue(rows[0]['landing_is_estimate'])


class NormalizeTests(unittest.TestCase):
    def test_normalize_balance_sums_usd_only(self):
        raw = {'available': [{'amount': 10000, 'currency': 'usd'}, {'amount': 5000, 'currency': 'eur'}],
               'pending': [{'amount': 2000, 'currency': 'usd'}]}
        bal = normalize_balance(raw)
        self.assertEqual(bal['available'], Decimal(100))
        self.assertEqual(bal['pending'], Decimal(20))

    def test_normalize_balance_transaction_excludes_refunds(self):
        # A refund-shaped balance transaction nets negative -- never shown as an incoming payment.
        row = normalize_balance_transaction(
            {'type': 'charge', 'net': -500, 'status': 'available', 'available_on': ts('2026-11-05'), 'source': {}}, {})
        self.assertIsNone(row)

    def test_normalize_balance_transaction_excludes_non_charge_types(self):
        row = normalize_balance_transaction(
            {'type': 'payout', 'net': 10000, 'status': 'available', 'available_on': ts('2026-11-05'), 'source': {}}, {})
        self.assertIsNone(row)

    def test_normalize_balance_transaction_uses_net_not_gross(self):
        # Gross amount 100, Stripe fee 3.20, net 96.80 -- the figure shown must be net (what
        # actually lands), not gross.
        row = normalize_balance_transaction(
            {'type': 'charge', 'amount': 10000, 'fee': 320, 'net': 9680, 'status': 'pending',
             'available_on': ts('2026-11-05'), 'source': {'metadata': {}, 'billing_details': {'name': 'X'}}}, {})
        self.assertEqual(row['net'], Decimal('96.80'))

    def test_normalize_payout_arrival_date_is_stripes_own_never_recomputed(self):
        p = normalize_payout({'id': 'po_1', 'status': 'paid', 'arrival_date': ts('2026-11-05'),
                               'amount': 50000, 'currency': 'usd', 'destination': {'last4': '1234'}})
        self.assertEqual(p['arrival_date'], '2026-11-05')


class BuildSnapshotAcceptanceTests(unittest.TestCase):
    """Mirrors the owner note's own acceptance list almost verbatim."""

    def test_known_pending_payout_fills_next_expected_tile_never_an_estimate(self):
        payouts_raw = [{'id': 'po_1', 'status': 'pending', 'arrival_date': ts('2026-11-10'),
                        'amount': 123456, 'currency': 'usd', 'method': 'standard',
                        'destination': {'bank_name': 'Chase', 'last4': '4821'}}]
        snap = build_snapshot({'available': [], 'pending': []}, payouts_raw, [], {'interval': 'daily'}, {})
        self.assertIsNone(snap['headline']['in_transit'])
        self.assertEqual(snap['headline']['next_expected']['amount'], Decimal('1234.56'))
        self.assertEqual(snap['headline']['next_expected']['arrival_date'], '2026-11-10')
        self.assertFalse(snap['headline']['next_expected']['is_estimate'])

    def test_no_payout_shows_available_pending_and_expected_never_fabricated(self):
        balance_raw = {'available': [{'amount': 50000, 'currency': 'usd'}], 'pending': [{'amount': 32000, 'currency': 'usd'}]}
        btx_raw = [{'type': 'charge', 'net': 32000, 'status': 'pending', 'available_on': ts('2026-11-06'),
                    'source': {'metadata': {}, 'billing_details': {'name': 'Someone'}}}]
        snap = build_snapshot(balance_raw, [], btx_raw, {'interval': 'daily'}, {})
        self.assertIsNone(snap['headline']['in_transit'])
        self.assertEqual(snap['headline']['available'], Decimal(500))
        self.assertEqual(snap['headline']['pending'], Decimal(320))
        self.assertIsNotNone(snap['headline']['next_expected'])
        self.assertTrue(snap['headline']['next_expected']['is_estimate'])

    def test_no_payout_and_unknown_schedule_never_fabricates_next_expected(self):
        balance_raw = {'available': [], 'pending': [{'amount': 10000, 'currency': 'usd'}]}
        btx_raw = [{'type': 'charge', 'net': 10000, 'status': 'pending', 'available_on': ts('2026-11-06'),
                    'source': {'metadata': {}, 'billing_details': {}}}]
        snap = build_snapshot(balance_raw, [], btx_raw, None, {})
        self.assertIsNone(snap['headline']['next_expected'])
        self.assertFalse(snap['schedule_known'])

    def test_in_transit_payout_fills_its_own_tile_and_repeats_into_next_expected(self):
        # Reproduces the Nov 3, 2026 production bug report almost exactly: a single in-transit
        # payout, nothing separately queued after it -- In Transit AND Next Expected must both
        # show it (never blank, never an estimate, while a real payout exists).
        payouts_raw = [
            {'id': 'po_1', 'status': 'in_transit', 'arrival_date': ts('2026-11-05'), 'amount': 20000,
             'currency': 'usd', 'destination': {'last4': '1111'}},
        ]
        snap = build_snapshot({'available': [], 'pending': []}, payouts_raw, [], {'interval': 'daily'}, {})
        self.assertEqual(snap['headline']['in_transit']['amount'], Decimal(200))
        self.assertEqual(snap['headline']['in_transit']['arrival_date'], '2026-11-05')
        self.assertEqual(snap['headline']['next_expected']['amount'], Decimal(200))
        self.assertEqual(snap['headline']['next_expected']['arrival_date'], '2026-11-05')
        self.assertFalse(snap['headline']['next_expected']['is_estimate'])

    def test_in_transit_and_a_separately_queued_pending_payout_both_fill_their_own_tiles(self):
        payouts_raw = [
            {'id': 'po_1', 'status': 'in_transit', 'arrival_date': ts('2026-11-05'), 'amount': 20000,
             'currency': 'usd', 'destination': {'last4': '1111'}},
            {'id': 'po_2', 'status': 'pending', 'arrival_date': ts('2026-11-12'), 'amount': 30000,
             'currency': 'usd', 'destination': {'last4': '2222'}},
        ]
        snap = build_snapshot({'available': [], 'pending': []}, payouts_raw, [], {'interval': 'daily'}, {})
        self.assertEqual(snap['headline']['in_transit']['amount'], Decimal(200))
        self.assertEqual(snap['headline']['in_transit']['arrival_date'], '2026-11-05')
        # Next Expected is the SEPARATELY queued pending payout, not a repeat of in-transit.
        self.assertEqual(snap['headline']['next_expected']['amount'], Decimal(300))
        self.assertEqual(snap['headline']['next_expected']['arrival_date'], '2026-11-12')


if __name__ == '__main__':
    unittest.main()
