import base64
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import stripe_recon as recon, stripe_recon_http as api, admin_auth as auth

BASE_INVOICE = {'base_price': 3933, 'taxrate': 10.55, 'addons': [{'desc': 'Logistics', 'price': 185, 'type': 'rental'}]}


def ts(iso):
    return int(dt.datetime.fromisoformat(iso + 'T12:00:00+00:00').timestamp())


def charge(id, dollars, created_iso, metadata_invoice='', name='', refunded=0, livemode=True):
    return {'id': id, 'amount': round(dollars * 100), 'amount_refunded': round(refunded * 100),
            'created': ts(created_iso), 'livemode': livemode,
            'metadata': {'invoice': metadata_invoice} if metadata_invoice else {},
            'billing_details': {'name': name}, 'status': 'succeeded', 'payment_intent': 'pi_' + id}


def row(invnum, client, data, invdate='2026-10-01'):
    return {'invnum': invnum, 'client': client, 'invdate': invdate,
            'created_at': invdate + 'T00:00:00Z', 'data': copy.deepcopy(data)}


def fake_key(role='service_role'):
    payload = base64.urlsafe_b64encode(json.dumps({'role': role}).encode()).decode().rstrip('=')
    return 'test.' + payload + '.not-a-real-signature'


class CalculationTests(unittest.TestCase):
    def test_metadata_match_requires_amount_agreement(self):
        rows = [row('2020123', 'John Mullenix', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28})]
        charges = [charge('ch1', 3527.28, '2026-10-01', metadata_invoice='2020123', name='John Mullenix')]
        report = recon.build_report(rows, charges, '2026-10')
        self.assertEqual(report['matched_count'], 1)
        self.assertEqual(report['matched'][0]['match_basis'], 'metadata')
        self.assertEqual(report['matched'][0]['stored_amount'], '3527.28')
        self.assertEqual(report['matched'][0]['charge_amount'], '3527.28')

    def test_metadata_identity_with_amount_disagreement_is_a_mismatch_not_a_match(self):
        rows = [row('2020123', 'John Mullenix', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28})]
        charges = [charge('ch1', 3500.00, '2026-10-01', metadata_invoice='2020123', name='John Mullenix')]
        report = recon.build_report(rows, charges, '2026-10')
        self.assertEqual(report['matched_count'], 0)
        self.assertEqual(report['mismatch_count'], 1)
        self.assertEqual(report['mismatches'][0]['difference'], '-27.28')

    def test_amount_date_client_match_when_metadata_empty(self):
        # The Baker pattern: a manually-confirmed Stripe payment with no metadata captured.
        rows = [row('2020128', 'Joe and Delee Baker', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-15', 'stripe_amount': 3978.00})]
        charges = [charge('ch_baker', 3978.00, '2026-10-15', metadata_invoice='', name='Joe and Delee Baker')]
        report = recon.build_report(rows, charges, '2026-10')
        self.assertEqual(report['matched_count'], 1)
        self.assertEqual(report['matched'][0]['match_basis'], 'amount_date_client')

    def test_never_matches_on_amount_alone_missing_date_or_missing_client(self):
        base = row('2020128', 'Joe and Delee Baker', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-15', 'stripe_amount': 3978.00})
        # Right amount, right date, wrong client -> no match.
        wrong_client = [charge('ch1', 3978.00, '2026-10-15', name='Someone Else')]
        self.assertEqual(recon.build_report([base], wrong_client, '2026-10')['matched_count'], 0)
        # Right amount, right client, date outside the +-7 day window -> no match.
        wrong_date = [charge('ch1', 3978.00, '2026-11-01', name='Joe and Delee Baker')]
        self.assertEqual(recon.build_report([base], wrong_date, '2026-10')['matched_count'], 0)
        # Right amount, right client, charge has no date at all (malformed `created`) -> no match.
        no_created = charge('ch1', 3978.00, '2026-10-15', name='Joe and Delee Baker')
        no_created['created'] = None
        self.assertEqual(recon.build_report([base], [no_created], '2026-10')['matched_count'], 0)

    def test_missing_record_stripe_charge_has_no_candidate(self):
        # The Deji pattern: a real Stripe charge with no invoice or extension record at all.
        report = recon.build_report([], [charge('ch_deji', 1024.50, '2026-09-01', name='Deji')], '2026-09')
        self.assertEqual(report['unmatched_charge_count'], 1)
        self.assertEqual(report['matched_count'], 0)
        self.assertEqual(report['unmatched_charges'][0]['charge_id'], 'ch_deji')

    def test_ambiguous_candidate_never_auto_matched_missing_date_variant(self):
        # The Hill pattern: two extensions share an amount; one is already metadata-matched to
        # a different charge, leaving the other without a usable paid_date -- neither one may
        # be auto-picked for the ambiguous September charge.
        data = {**BASE_INVOICE, 'extensions': [
            {'invnum': '202092-EXT1', 'amount': 1280.54, 'paid': True, 'paid_date': None},
            {'invnum': '202092-EXT2', 'amount': 1280.54, 'paid': True, 'paid_date': '2026-07-21'},
        ]}
        rows = [row('202092', 'Hill', data)]
        charges = [
            charge('ch_ext2', 1280.54, '2026-07-21', metadata_invoice='202092-EXT2', name='Hill'),
            charge('ch_sep1', 1280.54, '2026-09-01', metadata_invoice='', name='Hill'),
        ]
        report = recon.build_report(rows, charges, '2026-09')
        self.assertEqual(report['matched_count'], 1)
        self.assertEqual(report['matched'][0]['charge_id'], 'ch_ext2')
        charge_ids = [r['charge_id'] for r in report['unmatched_charges']]
        self.assertIn('ch_sep1', charge_ids)
        self.assertEqual(report['unmatched_item_count'], 1)  # EXT1, undated, stays unmatched too

    def test_ambiguous_candidate_never_auto_matched_tie_variant(self):
        data = {**BASE_INVOICE, 'extensions': [
            {'invnum': '202092-EXT1', 'amount': 1280.54, 'paid': True, 'paid_date': '2026-09-02'},
            {'invnum': '202092-EXT2', 'amount': 1280.54, 'paid': True, 'paid_date': '2026-09-03'},
        ]}
        rows = [row('202092', 'Hill', data)]
        charges = [charge('ch_sep1', 1280.54, '2026-09-01', metadata_invoice='', name='Hill')]
        report = recon.build_report(rows, charges, '2026-09')
        self.assertEqual(report['matched_count'], 0)
        ambiguous = report['unmatched_charges'][0]
        self.assertEqual(sorted(ambiguous['candidates']), ['202092-EXT1', '202092-EXT2'])

    def test_refund_shown_against_original_charge_not_as_unmatched_or_negative(self):
        rows = [row('202200', 'June Refund Client', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-06-10', 'stripe_amount': 500})]
        charges = [charge('ch_refund', 500, '2026-06-10', metadata_invoice='202200', name='June Refund Client', refunded=500)]
        report = recon.build_report(rows, charges, '2026-06')
        self.assertEqual(report['matched_count'], 1)
        self.assertEqual(report['unmatched_charge_count'], 0)
        matched = report['matched'][0]
        self.assertEqual(matched['refunded_amount'], '500.00')
        self.assertEqual(matched['net_amount'], '0.00')

    def test_test_mode_charges_excluded_entirely(self):
        rows = [row('2020123', 'John Mullenix', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28})]
        charges = [charge('ch_test', 3527.28, '2026-10-01', metadata_invoice='2020123', name='John Mullenix', livemode=False)]
        report = recon.build_report(rows, charges, '2026-10')
        self.assertEqual(report['matched_count'], 0)
        self.assertEqual(report['unmatched_charge_count'], 0)
        self.assertEqual(report['test_charge_count'], 1)

    def test_unconfirmed_livemode_flag_never_silently_included(self):
        rows = []
        bad = charge('ch_unsure', 100, '2026-10-01', name='X')
        del bad['livemode']
        report = recon.build_report(rows, [bad], '2026-10')
        self.assertEqual(report['unconfirmed_livemode_count'], 1)
        self.assertEqual(report['matched_count'] + report['unmatched_charge_count'], 0)

    def test_gs_photography_charges_excluded_from_every_list(self):
        report = recon.build_report([], [charge('ch_gs', 155.25, '2026-08-05', metadata_invoice='GS-100', name='Hexcel')], '2026-08')
        self.assertEqual(report['gs_charge_count'], 1)
        self.assertEqual(report['unmatched_charge_count'], 0)
        self.assertEqual(report['matched_count'], 0)

    def test_no_stored_paid_amount_is_flagged_not_substituted_silently(self):
        rows = [row('2020199', 'No Amount Client', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01'})]
        report = recon.build_report(rows, [], '2026-10')
        self.assertEqual(report['unmatched_item_count'], 1)
        item = report['unmatched_items'][0]
        self.assertTrue(item['amount_is_grand_fallback'])
        self.assertIsNotNone(item['stored_amount'])  # the grand-total fallback value itself is still shown...

    def test_split_payment_and_extension_items_tracked_independently(self):
        data = {**BASE_INVOICE, 'payments': [
            {'id': 'PAY1', 'status': 'paid', 'paid_date': '2026-10-01', 'amount': 2000},
            {'id': 'PAY2', 'status': 'paid', 'paid_date': '2026-10-10', 'amount': 2000},
        ]}
        rows = [row('2020150', 'Split Client', data)]
        charges = [
            charge('ch_pay1', 2000, '2026-10-01', metadata_invoice='2020150-PAY1', name='Split Client'),
            charge('ch_pay2', 2000, '2026-10-10', metadata_invoice='2020150-PAY2', name='Split Client'),
        ]
        report = recon.build_report(rows, charges, '2026-10')
        self.assertEqual(report['matched_count'], 2)

    def test_record_never_mutated(self):
        rows = [row('2020123', 'John Mullenix', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28})]
        charges = [charge('ch1', 3527.28, '2026-10-01', metadata_invoice='2020123', name='John Mullenix')]
        before_rows, before_charges = copy.deepcopy(rows), copy.deepcopy(charges)
        report1 = recon.build_report(rows, charges, '2026-10')
        report2 = recon.build_report(rows, charges, '2026-10')
        self.assertEqual(rows, before_rows)
        self.assertEqual(charges, before_charges)
        self.assertEqual(report1['matched_count'], report2['matched_count'])
        self.assertEqual(report1['matched'], report2['matched'])

    def test_period_validation(self):
        for p in ('2026-00', '2026-13', 'whatever', '2026-10-01'):
            with self.assertRaises(ValueError):
                recon.build_report([], [], p)

    def test_csv_export_safe_text(self):
        rows = [row('2020123', '=HYPERLINK("evil")', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28})]
        charges = [charge('ch1', 3527.28, '2026-10-01', metadata_invoice='2020123', name='=cmd|evil')]
        report = recon.build_report(rows, charges, '2026-10')
        csv = recon.csv_report(report, 'matched')
        self.assertIn("'=HYPERLINK", csv)
        self.assertIn("'=cmd", csv)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class ScanTests(unittest.TestCase):
    def test_exhaustive_pagination_follows_has_more(self):
        pages = [{'data': [{'id': 'ch1'}], 'has_more': True}, {'data': [{'id': 'ch2'}], 'has_more': False}]
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(pages[len(calls) - 1])

        with patch('urllib.request.urlopen', opener):
            rows = api.stripe_list_all('/v1/charges', {}, fake_key(), __import__('time').monotonic() + 30)
        self.assertEqual([r['id'] for r in rows], ['ch1', 'ch2'])
        self.assertEqual(len(calls), 2)
        self.assertIn('starting_after=ch1', calls[1])

    def test_deadline_exceeded_fails_closed(self):
        with self.assertRaises(api.IncompleteScan):
            api.stripe_list_all('/v1/charges', {}, fake_key(), __import__('time').monotonic() - 1)

    def test_empty_page_with_has_more_true_fails_closed(self):
        def opener(request, timeout):
            return FakeResponse({'data': [], 'has_more': True})
        with patch('urllib.request.urlopen', opener):
            with self.assertRaises(api.IncompleteScan):
                api.stripe_list_all('/v1/charges', {}, fake_key(), __import__('time').monotonic() + 30)

    def test_row_ceiling_fails_closed(self):
        def opener(request, timeout):
            return FakeResponse({'data': [{'id': 'ch' + str(i)} for i in range(api.MAX_ROWS + 1)], 'has_more': False})
        with patch('urllib.request.urlopen', opener):
            with self.assertRaises(api.IncompleteScan):
                api.stripe_list_all('/v1/charges', {}, fake_key(), __import__('time').monotonic() + 30)


class FakeHandler:
    def __init__(self, path='/api/webhook?stripe_recon=report&period=2026-10', body=None, headers=None):
        raw = json.dumps(body or {}).encode()
        self.path = path
        self.headers = {'Host': 'preview.example.test', 'Content-Type': 'application/json',
                         'Content-Length': str(len(raw)), **(headers or {})}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.out_headers = {}

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.out_headers[k] = v

    def end_headers(self):
        pass

    def data(self):
        return json.loads(self.wfile.getvalue())


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'SWJ_ADMIN_PASSWORD': 'synthetic-admin-password-only',
            'SUPABASE_KEY': fake_key(), 'VERCEL_ENV': 'preview',
            'STRIPE_RECONCILE_KEY': 'rk_live_synthetic_only',
        })
        self.env.start()
        auth._ATTEMPTS.clear()
        self.revocations = patch.object(auth, 'fetch_revoked_nonces', return_value=set())
        self.revocations.start()

    def tearDown(self):
        self.env.stop()
        self.revocations.stop()

    def authorized(self):
        return {'Cookie': auth.COOKIE + '=' + auth.issue_session('https://preview.example.test')}

    def test_shares_section7_session_but_gates_its_own_configured_check(self):
        with patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': ''}):
            h = FakeHandler('/api/webhook?stripe_recon=session')
            api.handle(h, 'GET', 'https://example.test', fake_key())
            body = h.data()
            self.assertTrue(body['password_configured'])
            self.assertFalse(body['stripe_configured'])
            self.assertFalse(body['configured'])

    def test_standard_secret_key_never_accepted(self):
        with patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': 'sk_live_full_access_key'}):
            self.assertFalse(api.configured())

    def test_anonymous_and_browser_gate_cannot_read_data(self):
        for headers in ({}, {'Cookie': 'swj_auth_v1=ok'}, {'Authorization': 'Bearer pretend-anon-key'}):
            h = FakeHandler(headers=headers)
            with patch.object(api, 'scan_invoices') as scanner:
                api.handle(h, 'GET', 'https://example.test', fake_key(), scanner)
                scanner.assert_not_called()
                self.assertEqual(h.code, 401)

    def test_login_cookie_and_report(self):
        h = FakeHandler('/api/webhook?stripe_recon=login', {'password': 'synthetic-admin-password-only'}, {'Origin': 'https://preview.example.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 200)
        cookie = h.out_headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie); self.assertIn('Secure', cookie); self.assertIn('SameSite=Strict', cookie)

        h = FakeHandler(headers={'Cookie': cookie.split(';')[0]})
        rows = [row('2020123', 'John Mullenix', {**BASE_INVOICE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28})]
        coverage = {'complete': True, 'fingerprint': 'synthetic', 'completed_at': '2026-11-02T18:00:00Z'}
        invoice_scanner = lambda *a: (rows, coverage)
        stripe_fetcher = lambda key, period: [charge('ch1', 3527.28, '2026-10-01', metadata_invoice='2020123', name='John Mullenix')]
        api.handle(h, 'GET', 'https://example.test', fake_key(), invoice_scanner, stripe_fetcher)
        self.assertEqual(h.code, 200)
        self.assertEqual(h.data()['matched_count'], 1)
        self.assertEqual(h.out_headers['Cache-Control'], 'no-store, private')
        self.assertNotIn('synthetic-admin-password-only', h.wfile.getvalue().decode())

    def test_cross_origin_login_rejected(self):
        h = FakeHandler('/api/webhook?stripe_recon=login', {'password': 'synthetic-admin-password-only'}, {'Origin': 'https://evil.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 403)
        self.assertNotIn('Set-Cookie', h.out_headers)

    def test_stripe_not_configured_closes_report_even_with_valid_session(self):
        with patch.dict(os.environ, {'STRIPE_RECONCILE_KEY': ''}):
            h = FakeHandler(headers=self.authorized())
            api.handle(h, 'GET', 'https://example.test', fake_key())
            self.assertEqual(h.code, 503)

    def test_actual_webhook_dispatch_never_runs_cron_or_stripe_webhook(self):
        spec = importlib.util.spec_from_file_location('stripe_recon_webhook', ROOT / 'api/webhook.py')
        webhook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(webhook)
        h = FakeHandler('/api/webhook?stripe_recon=report&period=2026-10&job=monthly_close', headers=self.authorized())
        coverage = {'complete': True, 'fingerprint': 'test', 'completed_at': '2026-11-02T18:00:00Z'}
        with patch.object(api, 'scan_invoices', return_value=([], coverage)), \
             patch.object(api, 'stripe_scan', return_value=[]), \
             patch.object(webhook, 'run_monthly_close') as close, \
             patch.object(webhook, 'run_destage_reminders') as remind, \
             patch.object(webhook, 'verify_signature') as stripe_sig:
            webhook.handler.do_GET(h)
            self.assertEqual(h.code, 200)
            close.assert_not_called(); remind.assert_not_called(); stripe_sig.assert_not_called()
        h = FakeHandler('/api/webhook?stripe_recon=report')
        webhook.handler.do_OPTIONS(h)
        self.assertEqual(h.code, 405)
        self.assertNotIn('Access-Control-Allow-Origin', h.out_headers)


if __name__ == '__main__':
    unittest.main()
