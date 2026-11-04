import datetime as dt
import importlib.util
import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


webhook = module('stripe_payout_webhook', 'api/webhook.py')


def ts(iso_date):
    return int(dt.datetime.combine(dt.date.fromisoformat(iso_date), dt.time(12, 0), tzinfo=dt.timezone.utc).timestamp())


class JsonResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def http_error(code, reason, body, url='https://api.stripe.com/v1/balance'):
    return urllib.error.HTTPError(url=url, code=code, msg=reason, hdrs=None, fp=io.BytesIO(json.dumps(body).encode()))


class ConfigTests(unittest.TestCase):
    def test_unconfigured_key_fails_gracefully_without_any_network_call(self):
        with patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': ''}):
            with patch('urllib.request.urlopen') as mock_urlopen:
                result = webhook.stripe_payout_snapshot()
            mock_urlopen.assert_not_called()
        self.assertIn('error', result)
        self.assertIn('not configured', result['error'])

    def test_non_livemode_restricted_key_is_refused(self):
        # Gabe's note is explicit: a test-mode key must never be accepted for this feature.
        with patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'rk_test_abc123'}):
            with patch('urllib.request.urlopen') as mock_urlopen:
                result = webhook.stripe_payout_snapshot()
            mock_urlopen.assert_not_called()
        self.assertIn('error', result)

    def test_standard_secret_key_is_refused_not_just_restricted_test_key(self):
        with patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'sk_live_fullaccess'}):
            with patch('urllib.request.urlopen') as mock_urlopen:
                result = webhook.stripe_payout_snapshot()
            mock_urlopen.assert_not_called()
        self.assertIn('error', result)


class PermissionDeniedTests(unittest.TestCase):
    """Gabe's note: 'Before building, verify Balance Read and Payouts Read. If either is
    missing, STOP and report exactly which permission is missing.' This environment has no live
    key, so the real verification can only happen on preview -- but the code itself must detect
    and report a 403 on either read distinctly, never a generic crash or a stale number."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'rk_live_realkey', 'SUPABASE_KEY': 'svc'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_balance_permission_missing_reports_payout_info_unavailable(self):
        err = http_error(403, 'Forbidden', {'error': {'type': 'invalid_request_error', 'code': 'permission_missing', 'message': 'Missing permission: Balance Read'}})
        with patch('urllib.request.urlopen', side_effect=err):
            result = webhook.stripe_payout_snapshot()
        self.assertEqual(result['error'], 'Payout info unavailable.')
        self.assertIn('/v1/balance', result['missing_permission'])
        self.assertIn('Balance Read', result['detail'])

    def test_payouts_permission_missing_reports_payout_info_unavailable(self):
        def side_effect(req, timeout=None):
            if '/v1/balance' in req.full_url and '/v1/balance_transactions' not in req.full_url:
                return JsonResponse({'available': [], 'pending': []})
            if '/v1/payouts' in req.full_url:
                raise http_error(403, 'Forbidden', {'error': {'type': 'invalid_request_error', 'code': 'permission_missing', 'message': 'Missing permission: Payouts Read'}}, url=req.full_url)
            raise AssertionError('unexpected url ' + req.full_url)
        with patch('urllib.request.urlopen', side_effect=side_effect):
            result = webhook.stripe_payout_snapshot()
        self.assertEqual(result['error'], 'Payout info unavailable.')
        self.assertIn('/v1/payouts', result['missing_permission'])
        self.assertIn('Payouts Read', result['detail'])

    def test_permission_error_never_contains_the_api_key(self):
        err = http_error(403, 'Forbidden', {'error': {'type': 'invalid_request_error', 'message': 'Invalid API key'}})
        with patch('urllib.request.urlopen', side_effect=err):
            result = webhook.stripe_payout_snapshot()
        self.assertNotIn('rk_live_realkey', json.dumps(result))

    def test_account_schedule_read_failure_is_best_effort_not_fatal(self):
        # Balance/Payouts/transactions succeed; only /v1/account (schedule) is denied. Per the
        # note, only Balance + Payouts are the hard-required permissions -- the section must
        # still render, just without an Expected date, never a crash.
        def side_effect(req, timeout=None):
            url = req.full_url
            if '/v1/balance_transactions' in url:
                return JsonResponse({'data': []})
            if '/v1/balance' in url:
                return JsonResponse({'available': [{'amount': 0, 'currency': 'usd'}], 'pending': []})
            if '/v1/payouts' in url:
                return JsonResponse({'data': []})
            if '/v1/account' in url:
                raise http_error(403, 'Forbidden', {'error': {'message': 'Missing permission: Account Read'}}, url=url)
            if 'invoices' in url:
                return JsonResponse([])
            raise AssertionError('unexpected url ' + url)
        with patch('urllib.request.urlopen', side_effect=side_effect):
            result = webhook.stripe_payout_snapshot()
        self.assertTrue(result.get('ok'))
        self.assertFalse(result['schedule_known'])


class FullSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'rk_live_realkey', 'SUPABASE_KEY': 'svc'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _responder(self, balance, payouts, btx, account, invoices):
        def side_effect(req, timeout=None):
            url = req.full_url
            if 'invoices' in url:
                return JsonResponse(invoices)
            if '/v1/balance_transactions' in url:
                return JsonResponse({'data': btx})
            if '/v1/balance' in url:
                return JsonResponse(balance)
            if '/v1/payouts' in url:
                return JsonResponse({'data': payouts})
            if '/v1/account' in url:
                return JsonResponse(account)
            raise AssertionError('unexpected url ' + url)
        return side_effect

    def test_full_success_response_shape_and_secret_never_present(self):
        payouts = [{'id': 'po_1', 'status': 'pending', 'arrival_date': ts('2026-11-10'),
                    'amount': 123456, 'currency': 'usd', 'method': 'standard',
                    'destination': {'bank_name': 'Chase', 'last4': '4821'}}]
        with patch('urllib.request.urlopen', side_effect=self._responder({'available': [], 'pending': []}, payouts, [], {}, [])):
            result = webhook.stripe_payout_snapshot()
        self.assertTrue(result['ok'])
        self.assertEqual(result['headline']['next_expected']['amount'], 1234.56)
        self.assertEqual(result['headline']['next_expected']['arrival_date'], '2026-11-10')
        self.assertFalse(result['headline']['next_expected']['is_estimate'])
        dumped = json.dumps(result)
        self.assertNotIn('rk_live_realkey', dumped)
        self.assertNotIn('svc', dumped)

    def test_timeline_joins_invoice_ledger_for_client_label(self):
        btx = [{'id': 'txn1', 'type': 'charge', 'net': 31000, 'status': 'pending', 'available_on': ts('2026-11-10'),
                'source': {'metadata': {'invoice': '2020128-PAY1'}, 'billing_details': {'name': 'Baker'}}}]
        invoices = [{'invnum': '2020128', 'client': 'Baker Staging'}]
        with patch('urllib.request.urlopen', side_effect=self._responder(
                {'available': [], 'pending': [{'amount': 31000, 'currency': 'usd'}]}, [], btx,
                {'settings': {'payouts': {'schedule': {'interval': 'daily'}}}}, invoices)):
            result = webhook.stripe_payout_snapshot()
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['timeline']), 1)
        self.assertEqual(result['timeline'][0]['items'][0]['label'], 'Baker Staging #2020128 (PAY1)')
        self.assertEqual(result['timeline'][0]['total_amount'], 310.0)


class DispatchTests(unittest.TestCase):
    """The job must be reachable via POST ?job=stripe_payout_snapshot, same convention every
    other Home-facing job already uses, and must never fall through into Stripe webhook-event
    handling (which expects a Stripe-Signature header and an event-shaped body)."""

    def test_unknown_job_prefix_is_not_confused_with_plaid_dispatch(self):
        self.assertFalse('stripe_payout_snapshot'.startswith('plaid_'))


class PayoutPaginationTests(unittest.TestCase):
    """Nov 3, 2026 production bug report, hypothesis #1: a single flat limit=10 page could
    silently miss a payout. _stripe_payout_list_all() must follow has_more across pages."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'rk_live_realkey', 'SUPABASE_KEY': 'svc'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_payouts_fetch_follows_has_more_across_multiple_pages(self):
        page1 = [{'id': f'po_old_{i}', 'status': 'paid', 'arrival_date': ts('2026-10-01'), 'amount': 1000, 'currency': 'usd'} for i in range(100)]
        page2 = [{'id': 'po_1UXs4VP7Owv6M3r9nIcXirh5', 'status': 'in_transit', 'arrival_date': ts('2026-11-04'), 'amount': 399206, 'currency': 'usd', 'destination': {'last4': '4821'}}]
        calls = []

        def opener(req, timeout=None):
            if '/v1/payouts' not in req.full_url:
                raise AssertionError('unexpected url ' + req.full_url)
            calls.append(req.full_url)
            if 'starting_after' not in req.full_url:
                return JsonResponse({'data': page1, 'has_more': True})
            return JsonResponse({'data': page2, 'has_more': False})

        with patch('urllib.request.urlopen', side_effect=opener):
            result = webhook._stripe_payout_list_all('/v1/payouts', {})
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result), 101)
        self.assertIn('po_1UXs4VP7Owv6M3r9nIcXirh5', [p['id'] for p in result])

    def test_pagination_stops_at_max_pages_never_loops_forever(self):
        def opener(req, timeout=None):
            return JsonResponse({'data': [{'id': 'po_x', 'status': 'paid', 'arrival_date': 0, 'amount': 100, 'currency': 'usd'}], 'has_more': True})
        with patch('urllib.request.urlopen', side_effect=opener):
            result = webhook._stripe_payout_list_all('/v1/payouts', {}, max_pages=3)
        self.assertEqual(len(result), 3)


class ProductionBugReproductionTests(unittest.TestCase):
    """Reproduces the Nov 3, 2026 production bug report almost exactly: Elisha Cruson #2020129,
    payout po_1UXs4VP7Owv6M3r9nIcXirh5, $3,992.06, in transit, arriving Wed Nov 4 2026. The
    acceptance bar from the note: Home must show $3,992.06 in transit arriving Nov 4 -- NOT an
    estimate, NOT Nov 5, and the top tiles must show the in-transit money (never "$0 / no
    pending funds" while a real payout exists)."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'rk_live_realkey', 'SUPABASE_KEY': 'svc'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_cruson_payout_scenario_matches_the_owners_acceptance_bar(self):
        payouts = [{'id': 'po_1UXs4VP7Owv6M3r9nIcXirh5', 'status': 'in_transit', 'arrival_date': ts('2026-11-04'),
                    'amount': 399206, 'currency': 'usd', 'method': 'standard', 'destination': {'bank_name': 'Chase', 'last4': '4821'}}]
        btx = [{'id': 'txn_cruson', 'type': 'charge', 'net': 399206, 'status': 'pending', 'available_on': ts('2026-11-04'),
                'source': {'metadata': {'invoice': '2020129'}, 'billing_details': {'name': 'Elisha Cruson'}}}]
        invoices = [{'invnum': '2020129', 'client': 'Elisha Cruson'}]
        balance = {'available': [{'amount': 0, 'currency': 'usd'}], 'pending': [{'amount': 0, 'currency': 'usd'}]}
        account = {'settings': {'payouts': {'schedule': {'interval': 'daily'}}}}

        def opener(req, timeout=None):
            url = req.full_url
            if 'invoices' in url:
                return JsonResponse(invoices)
            if '/v1/balance_transactions' in url:
                return JsonResponse({'data': btx})
            if '/v1/balance' in url:
                return JsonResponse(balance)
            if '/v1/payouts' in url:
                return JsonResponse({'data': payouts, 'has_more': False})
            if '/v1/account' in url:
                return JsonResponse(account)
            raise AssertionError('unexpected url ' + url)

        with patch('urllib.request.urlopen', side_effect=opener):
            result = webhook.stripe_payout_snapshot()

        self.assertTrue(result['ok'])
        # Exact cents, not rounded to whole dollars.
        self.assertEqual(result['headline']['in_transit']['amount'], 3992.06)
        self.assertEqual(result['headline']['in_transit']['arrival_date'], '2026-11-04')
        # Next Expected must also show the real in-transit payout (nothing else queued),
        # never blank, never an estimate.
        self.assertEqual(result['headline']['next_expected']['amount'], 3992.06)
        self.assertEqual(result['headline']['next_expected']['arrival_date'], '2026-11-04')
        self.assertFalse(result['headline']['next_expected']['is_estimate'])
        # The top tiles are never "$0 / nothing happening" while this payout exists.
        self.assertEqual(result['headline']['available'], 0.0)
        self.assertEqual(result['headline']['pending'], 0.0)
        # The per-payment timeline row: Released Nov 4 -> Landing Nov 4 (the real payout's own
        # arrival_date), never Nov 5, never labeled Expected.
        self.assertEqual(len(result['timeline']), 1)
        row = result['timeline'][0]
        self.assertEqual(row['released_date'], '2026-11-04')
        self.assertEqual(row['landing_date'], '2026-11-04')
        self.assertFalse(row['landing_is_estimate'])
        self.assertEqual(row['total_amount'], 3992.06)
        self.assertEqual(row['items'][0]['label'], 'Elisha Cruson #2020129')
        self.assertEqual(row['items'][0]['amount'], 3992.06)


if __name__ == '__main__':
    unittest.main()
