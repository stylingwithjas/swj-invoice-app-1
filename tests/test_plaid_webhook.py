import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


webhook = module('plaid_webhook', 'api/webhook.py')


class PlaidConfigTests(unittest.TestCase):
    def test_unconfigured_plaid_fails_gracefully_everywhere(self):
        with patch.dict(os.environ, {'PLAID_CLIENT_ID': '', 'PLAID_SECRET': ''}):
            self.assertEqual(webhook.plaid_create_link_token(), {'error': 'Plaid is not configured yet — PLAID_CLIENT_ID/PLAID_SECRET are not set.'})
            self.assertEqual(webhook.plaid_refresh_balance(), {'error': 'Plaid is not configured yet.'})
            self.assertEqual(webhook.plaid_sync_transactions(), {'error': 'Plaid is not configured yet.'})

    def test_no_connected_account_fails_gracefully(self):
        with patch.dict(os.environ, {'PLAID_CLIENT_ID': 'x', 'PLAID_SECRET': 'y'}), \
             patch.object(webhook, '_plaid_get_items', return_value=[]):
            self.assertEqual(webhook.plaid_refresh_balance(), {'error': 'No Amex account connected yet.'})
            result = webhook.plaid_sync_transactions()
            self.assertIn('No Amex account connected yet', result['error'])


class MultiAccountBalanceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'PLAID_CLIENT_ID': 'x', 'PLAID_SECRET': 'y', 'SUPABASE_KEY': 'z'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_refresh_balance_covers_every_card_across_every_item(self):
        items = [
            {'item_id': 'item1', 'access_token': 'tok1', 'institution_name': 'Amex'},
            {'item_id': 'item2', 'access_token': 'tok2', 'institution_name': 'Amex 2'},
        ]
        liabilities = {
            'tok1': {
                'accounts': [
                    {'account_id': 'acc1', 'name': 'Business Platinum', 'mask': '1001', 'balances': {'current': 1200.5}},
                    {'account_id': 'acc2', 'name': 'Business Gold', 'mask': '1002', 'balances': {'current': 300.0}},
                ],
                'liabilities': {'credit': [
                    {'account_id': 'acc1', 'last_statement_balance': 1200.5, 'minimum_payment_amount': 50, 'next_payment_due_date': '2026-11-20', 'last_statement_issue_date': '2026-10-25'},
                    {'account_id': 'acc2', 'last_statement_balance': 300.0, 'minimum_payment_amount': 25, 'next_payment_due_date': '2026-11-15', 'last_statement_issue_date': '2026-10-20'},
                ]},
            },
            'tok2': {
                'accounts': [{'account_id': 'acc3', 'name': 'Solo Card', 'mask': '2001', 'balances': {'current': 50.0}}],
                'liabilities': {'credit': [{'account_id': 'acc3', 'last_statement_balance': 50.0, 'minimum_payment_amount': 25, 'next_payment_due_date': '2026-11-10', 'last_statement_issue_date': '2026-10-15'}]},
            },
        }
        stored = {}

        def fake_request(path, payload):
            self.assertEqual(path, '/liabilities/get')
            return liabilities[payload['access_token']]

        def fake_store(item_id, summaries):
            for s in summaries:
                stored[s['account_id']] = {**s, 'item_id': item_id}
            return '2026-11-02T00:00:00Z'

        with patch.object(webhook, '_plaid_get_items', return_value=items), \
             patch.object(webhook, '_plaid_request', side_effect=fake_request), \
             patch.object(webhook, '_plaid_store_balances', side_effect=fake_store):
            result = webhook.plaid_refresh_balance()

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['accounts']), 3)
        self.assertEqual(len(stored), 3)
        self.assertEqual(stored['acc1']['item_id'], 'item1')
        self.assertEqual(stored['acc3']['item_id'], 'item2')
        self.assertIsNone(result['errors'])

    def test_refresh_balance_reports_partial_failure_without_losing_working_cards(self):
        items = [
            {'item_id': 'item1', 'access_token': 'tok1', 'institution_name': 'Good Amex'},
            {'item_id': 'item2', 'access_token': 'tok2', 'institution_name': 'Broken Amex'},
        ]

        def fake_request(path, payload):
            if payload['access_token'] == 'tok1':
                return {'accounts': [{'account_id': 'acc1', 'name': 'Card', 'mask': '1001', 'balances': {'current': 10}}],
                        'liabilities': {'credit': [{'account_id': 'acc1', 'last_statement_balance': 10, 'minimum_payment_amount': 5, 'next_payment_due_date': '2026-11-10', 'last_statement_issue_date': '2026-10-10'}]}}
            raise Exception('simulated Plaid outage')

        with patch.object(webhook, '_plaid_get_items', return_value=items), \
             patch.object(webhook, '_plaid_request', side_effect=fake_request), \
             patch.object(webhook, '_plaid_store_balances', return_value='2026-11-02T00:00:00Z'):
            result = webhook.plaid_refresh_balance()

        self.assertTrue(result['ok'])
        self.assertEqual(len(result['accounts']), 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('Broken Amex', result['errors'][0])


class MultiItemSyncTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'PLAID_CLIENT_ID': 'x', 'PLAID_SECRET': 'y', 'SUPABASE_KEY': 'z'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_sync_categorizes_new_rows_never_touches_modified_rows_category(self):
        items = [{'item_id': 'item1', 'access_token': 'tok1', 'institution_name': 'Amex'}]
        sync_resp = {
            'added': [
                {'transaction_id': 'tx1', 'account_id': 'acc1', 'date': '2026-11-01', 'merchant_name': 'U-HAUL #1', 'name': 'U-HAUL', 'amount': 85.0, 'pending': False, 'category': ['Transportation']},
                {'transaction_id': 'tx2', 'account_id': 'acc1', 'date': '2026-11-02', 'merchant_name': 'Unknown Vendor LLC', 'name': 'Unknown Vendor', 'amount': 42.0, 'pending': False, 'category': []},
            ],
            'modified': [
                {'transaction_id': 'tx0', 'account_id': 'acc1', 'date': '2026-10-28', 'merchant_name': 'U-HAUL #0', 'name': 'U-HAUL', 'amount': 90.0, 'pending': False, 'category': ['Transportation']},
            ],
            'removed': [], 'next_cursor': 'cursor1', 'has_more': False,
        }

        def fake_request(path, payload):
            if path == '/transactions/sync':
                return sync_resp
            if path == '/liabilities/get':
                return {'accounts': [], 'liabilities': {'credit': []}}
            raise AssertionError('unexpected path ' + path)

        posted_batches = []

        def fake_urlopen(req, timeout=None):
            if req.full_url.startswith(webhook.SUPABASE_URL + '/rest/v1/plaid_transactions') and req.get_method() == 'POST':
                posted_batches.append(json.loads(req.data))
            import io

            class R:
                def read(self_inner): return b'[]'
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): pass
            return R()

        with patch.object(webhook, '_plaid_get_items', return_value=items), \
             patch.object(webhook, '_plaid_request', side_effect=fake_request), \
             patch('urllib.request.urlopen', side_effect=fake_urlopen):
            result = webhook.plaid_sync_transactions()

        self.assertEqual(result, {'ok': True, 'added': 2, 'modified': 1, 'removed': 0, 'errors': None})
        self.assertEqual(len(posted_batches), 1)
        by_id = {r['transaction_id']: r for r in posted_batches[0]}
        self.assertEqual(by_id['tx1']['category'], 'Truck & Gas')
        self.assertEqual(by_id['tx1']['category_source'], 'rule')
        self.assertNotIn('category', by_id['tx2'])  # unmatched -- no key sent at all
        self.assertNotIn('category', by_id['tx0'])  # modified row -- never touches category
        self.assertNotIn('category_source', by_id['tx0'])

    def test_sync_reports_per_item_errors_without_losing_other_items_progress(self):
        items = [
            {'item_id': 'good', 'access_token': 'tok_good', 'institution_name': 'Good'},
            {'item_id': 'bad', 'access_token': 'tok_bad', 'institution_name': 'Bad'},
        ]

        def fake_request(path, payload):
            if payload['access_token'] == 'tok_bad':
                raise Exception('simulated failure')
            if path == '/transactions/sync':
                return {'added': [{'transaction_id': 'tx1', 'account_id': 'a', 'date': '2026-11-01', 'merchant_name': 'Staples', 'amount': 10, 'category': []}],
                        'modified': [], 'removed': [], 'next_cursor': 'c1', 'has_more': False}
            if path == '/liabilities/get':
                return {'accounts': [], 'liabilities': {'credit': []}}
            raise AssertionError(path)

        def fake_urlopen(req, timeout=None):
            class R:
                def read(self_inner): return b'[]'
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): pass
            return R()

        with patch.object(webhook, '_plaid_get_items', return_value=items), \
             patch.object(webhook, '_plaid_request', side_effect=fake_request), \
             patch('urllib.request.urlopen', side_effect=fake_urlopen):
            result = webhook.plaid_sync_transactions()

        self.assertTrue(result['ok'])
        self.assertEqual(result['added'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('Bad', result['errors'][0])


if __name__ == '__main__':
    unittest.main()
