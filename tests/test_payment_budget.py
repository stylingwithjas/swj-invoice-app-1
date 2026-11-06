import unittest
from unittest.mock import Mock

from lib.payment_budget import for_period, save_assignment


def paid(invnum, date, amount):
    return {'invnum': invnum, 'client': 'Fixture', 'data': {
        'paid': True, 'paid_date': date, 'amount_paid': amount,
    }}


class PaymentBudgetTests(unittest.TestCase):
    def setUp(self):
        self.rows = [paid('2020110', '2026-10-01', 3527.28),
                     paid('2020111', '2026-10-15', 3978),
                     paid('2020129', '2026-10-31', 4111.60),
                     paid('2020130', '2026-11-05', 2770.58)]
        self.assignments = {}
        self.db = Mock()
        self.db.rows.side_effect = lambda table, filters=None, **kw: (
            list(self.assignments.values()) if table == 'payment_budget_months' else
            [row for row in self.rows if not filters or filters['invnum'] == 'eq.' + row['invnum']]
        )
        self.db.put.side_effect = lambda table, row, key: self.assignments.update({row[key]: row})
        self.request = {'invnum': '2020129', 'event_id': '2020129::',
                        'received_date': '2026-10-31', 'amount_cents': 411160,
                        'budget_month': '2026-11'}

    def test_budget_month_moves_one_payment_and_does_not_change_received_totals(self):
        october = for_period(self.db, '2026-10')
        self.assertEqual(october['received_cents'], october['budgeted_cents'])
        save_assignment(self.db, self.request)
        save_assignment(self.db, self.request)
        october = for_period(self.db, '2026-10')
        november = for_period(self.db, '2026-11')
        self.assertEqual((october['received_cents'], october['budgeted_cents']), (1161688, 750528))
        self.assertEqual((november['received_cents'], november['budgeted_cents']), (277058, 688218))
        self.assertEqual(october['moved_out_cents'], november['moved_in_cents'])
        self.assertEqual(next(e for e in november['events'] if e['event_id'] == '2020129::')['date'], '2026-10-31')
        self.assertEqual(len(self.assignments), 1)
        self.assertTrue(all(call.args[0] == 'payment_budget_months' for call in self.db.put.call_args_list))

        save_assignment(self.db, {**self.request, 'budget_month': '2026-10'})
        self.assertEqual(for_period(self.db, '2026-10')['budgeted_cents'], 1161688)
        self.assertEqual(for_period(self.db, '2026-11')['budgeted_cents'], 277058)

    def test_rejects_stale_evidence_and_defaults_replaced_receipt_to_received_month(self):
        for overrides in [{'amount_cents': 1}, {'budget_month': '2026-09'},
                          {'received_date': '2026-10-30'}, {'event_id': '2020130::'}]:
            with self.assertRaises(ValueError):
                save_assignment(self.db, {**self.request, **overrides})
        self.db.put.assert_not_called()
        save_assignment(self.db, self.request)
        self.rows[2]['data']['amount_paid'] = 4000
        self.assertEqual(for_period(self.db, '2026-11')['budgeted_cents'], 277058)

    def test_split_and_extension_can_move_independently(self):
        self.rows.append({'invnum': '2020140', 'data': {'payments': [
            {'id': 'PAY1', 'status': 'paid', 'paid_date': '2026-10-28', 'amount': 100, 'payment_id': 'fixture'},
            {'id': 'PAY2', 'status': 'paid', 'paid_date': '2026-11-02', 'amount': 120},
        ], 'extensions': [{'invnum': '2020140-EXT1', 'paid': True, 'paid_date': '2026-10-29', 'amount': 50}]}})
        for suffix, date, cents in [('PAY1', '2026-10-28', 10000), ('EXT1', '2026-10-29', 5000)]:
            save_assignment(self.db, {**self.request, 'invnum': '2020140', 'event_id': '2020140::'+suffix,
                                      'received_date': date, 'amount_cents': cents})
        november = for_period(self.db, '2026-11')
        self.assertEqual(november['moved_in_cents'], 15000)
        self.assertEqual(november['received_cents'], 289058)
        self.assertEqual(next(e for e in november['events'] if e['event_id'] == '2020140::PAY1')['method'], 'Stripe (Card)')
