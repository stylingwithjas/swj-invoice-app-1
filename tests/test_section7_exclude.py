"""Exclude/restore from Section 7 reconciliation (owner request, Nov 3, 2026).

Reuses the exact fixtures (invoice(), fake_key(), FakeHandler, AccessTests.authorized()) and
mock-opener conventions already established in tests/test_section7.py -- never a second,
diverging way of faking a request or an invoice record.
"""
import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import section7 as audit, section7_http as api
from tests.test_section7 import invoice, fake_key, CLEAN, FakeHandler


class FakeJsonResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def paid_invoice(invnum='2020101', **extra):
    d = {**CLEAN, 'paid': True, 'paid_date': '2026-10-05', 'amount_paid': CLEAN['total_override']}
    d.update(extra)
    return invoice(d, invnum=invnum)


def signed_unpaid_invoice(invnum='2020103'):
    d = {**CLEAN, 'signed': True}
    return invoice(d, invnum=invnum)


def test_record_invoice(invnum='2020102v1'):
    # Jasmine Santana's actual test-record shape from Gabe's screenshot: no payments, no
    # signature, nothing that should block exclusion.
    d = {**CLEAN}
    for key in ('subtotal_override', 'tax_override', 'total_override'):
        pass
    return invoice(d, invnum=invnum, client='Jasmine Santana')


class ExclusionBlockReasonTests(unittest.TestCase):
    """Pure-function tests for lib/section7.py's exclusion_block_reason()."""

    def check(self, row, period='2026-10'):
        return audit.check_invoice(row, period)

    def test_unpaid_unsigned_record_is_excludable(self):
        result = self.check(test_record_invoice())
        self.assertIsNone(audit.exclusion_block_reason(result, filed_periods=set()))

    def test_paid_record_is_never_excludable(self):
        result = self.check(paid_invoice())
        reason = audit.exclusion_block_reason(result, filed_periods=set())
        self.assertIsNotNone(reason)
        self.assertIn('Paid', reason)

    def test_signed_unpaid_record_is_never_excludable(self):
        result = self.check(signed_unpaid_invoice())
        reason = audit.exclusion_block_reason(result, filed_periods=set())
        self.assertIsNotNone(reason)
        self.assertIn('Signed', reason)

    def test_record_with_receipt_in_a_filed_period_is_never_excludable(self):
        result = self.check(paid_invoice())
        # Paid already blocks it -- build an UNPAID record whose receipt_dates we fake instead,
        # to isolate the filed-period check specifically from the paid check.
        result['live_paid'] = False
        result['receipt_dates'] = ['2026-09-15']
        reason = audit.exclusion_block_reason(result, filed_periods={'2026-09'})
        self.assertIsNotNone(reason)
        self.assertIn('filed', reason.lower())

    def test_record_with_receipt_outside_any_filed_period_is_excludable(self):
        result = self.check(test_record_invoice())
        result['receipt_dates'] = ['2026-08-01']
        self.assertIsNone(audit.exclusion_block_reason(result, filed_periods={'2026-09'}))


class BuildReportFilterModeTests(unittest.TestCase):
    """Acceptance: after any exclusion, the active list's totals must still sum exactly to its
    own rows -- never a count that disagrees with what's actually shown."""

    def _excluded_test_record(self):
        # Flagged for a genuine reason (total_override off by a dollar), same as the real
        # Jasmine Santana #2020102v1 record from the owner's screenshot -- a clean record never
        # appears in `results` at all (flagged_count only counts rows with an issue), so an
        # excluded-but-clean fixture wouldn't exercise filter_mode at all.
        d = {**CLEAN, 'total_override': CLEAN['total_override'] + 1, 'excluded_from_reconciliation': True,
             'excluded_reason': 'test_record', 'excluded_note': None,
             'excluded_at': '2026-11-03T12:00:00Z', 'excluded_by': 'report administrator'}
        return invoice(d, invnum='2020102v1', client='Jasmine Santana')

    def _active_discrepancy(self):
        return invoice({**CLEAN, 'total_override': 1}, invnum='2020150')

    def test_active_mode_hides_excluded_row_and_drops_its_count(self):
        rows = [self._excluded_test_record(), self._active_discrepancy()]
        report = audit.build_report(rows, '2026-10', filter_mode='active')
        self.assertEqual(report['flagged_count'], 1)
        self.assertEqual([r['invnum'] for r in report['results']], ['2020150'])
        self.assertEqual(report['discrepancy_count'], sum(r['has_discrepancy'] for r in report['results']))

    def test_excluded_mode_shows_only_excluded_rows_with_reason(self):
        rows = [self._excluded_test_record(), self._active_discrepancy()]
        report = audit.build_report(rows, '2026-10', filter_mode='excluded')
        self.assertEqual([r['invnum'] for r in report['results']], ['2020102v1'])
        self.assertEqual(report['results'][0]['excluded_reason'], 'test_record')

    def test_all_mode_shows_every_flagged_row(self):
        rows = [self._excluded_test_record(), self._active_discrepancy()]
        report = audit.build_report(rows, '2026-10', filter_mode='all')
        self.assertEqual(len(report['results']), 2)

    def test_active_default_matches_prior_behavior_when_nothing_excluded(self):
        rows = [self._active_discrepancy()]
        report = audit.build_report(rows, '2026-10')
        self.assertEqual(report['filter_mode'], 'active')
        self.assertEqual(report['flagged_count'], 1)

    def test_counts_always_sum_to_visible_rows_regardless_of_mode(self):
        rows = [self._excluded_test_record(), self._active_discrepancy()]
        for mode in ('active', 'excluded', 'all'):
            report = audit.build_report(rows, '2026-10', filter_mode=mode)
            self.assertEqual(report['discrepancy_count'], sum(r['has_discrepancy'] for r in report['results']))
            self.assertEqual(report['unverifiable_count'], sum(r['has_unverifiable'] for r in report['results']))
            self.assertEqual(report['flagged_count'], len(report['results']))

    def test_invalid_filter_mode_rejected(self):
        with self.assertRaises(ValueError):
            audit.build_report([self._active_discrepancy()], '2026-10', filter_mode='bogus')


class CsvExportTests(unittest.TestCase):
    def test_csv_always_has_excluded_columns_and_active_view_leaves_them_blank(self):
        rows = [invoice({**CLEAN, 'total_override': 1}, invnum='2020150')]
        report = audit.build_report(rows, '2026-10', filter_mode='active')
        csv_text = audit.csv_report(report, 'all')
        header = csv_text.splitlines()[0]
        self.assertIn('Excluded from reconciliation', header)
        self.assertIn('Excluded reason', header)
        data_line = csv_text.splitlines()[1]
        self.assertIn('False', data_line)

    def test_csv_under_all_marks_excluded_rows_with_reason(self):
        excluded = invoice({**CLEAN, 'total_override': CLEAN['total_override'] + 1, 'excluded_from_reconciliation': True,
                             'excluded_reason': 'test_record', 'excluded_at': '2026-11-03T12:00:00Z'}, invnum='2020102v1')
        report = audit.build_report([excluded], '2026-10', filter_mode='all')
        csv_text = audit.csv_report(report, 'all')
        self.assertIn('Test record (not a real job)', csv_text)
        self.assertIn('True', csv_text.splitlines()[1])


class ExcludeRecordWriteTests(unittest.TestCase):
    """lib/section7_http.py's exclude_record()/restore_record() -- the only write path in this
    module. Validates server-side (never trusting a client-side check) and never touches a
    financial field."""

    def _opener_for(self, invoice_row, filed_rows=None, captured_writes=None):
        filed_rows = filed_rows or []
        captured_writes = captured_writes if captured_writes is not None else []

        def opener(request, timeout=None):
            url = request.full_url
            method = request.get_method()
            if method == 'PATCH':
                captured_writes.append({'url': url, 'body': json.loads(request.data)})
                return FakeJsonResponse([])
            q = parse_qs(urlparse(url).query)
            if 'TAX_REVIEW_' in q.get('invnum', [''])[0]:
                return FakeJsonResponse(filed_rows)
            return FakeJsonResponse([invoice_row] if invoice_row else [])
        return opener, captured_writes

    def test_unpaid_test_record_is_excludable_and_write_only_touches_exclusion_fields(self):
        row = test_record_invoice()
        original_data = copy.deepcopy(row['data'])
        opener, writes = self._opener_for(row)
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020102v1', 'test_record', '', opener)
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(len(writes), 1)
        written = writes[0]['body']['data']
        self.assertTrue(written['excluded_from_reconciliation'])
        self.assertEqual(written['excluded_reason'], 'test_record')
        self.assertIsNotNone(written['excluded_at'])
        # Every financial/original field passes through byte-identical.
        for key in original_data:
            self.assertEqual(written[key], original_data[key], key)

    def test_paid_record_cannot_be_excluded_no_write_made(self):
        row = paid_invoice()
        opener, writes = self._opener_for(row)
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020101', 'test_record', '', opener)
        self.assertFalse(ok)
        self.assertIn('Paid', message)
        self.assertEqual(writes, [])

    def test_signed_record_cannot_be_excluded_no_write_made(self):
        row = signed_unpaid_invoice()
        opener, writes = self._opener_for(row)
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020103', 'test_record', '', opener)
        self.assertFalse(ok)
        self.assertIn('Signed', message)
        self.assertEqual(writes, [])

    def test_filed_period_record_cannot_be_excluded_no_write_made(self):
        # An unpaid record whose extension/payment carries a receipt in a filed month --
        # synthesize via a payments list entry instead of the top-level paid flag so live_paid
        # stays true only through that dated event (mirrors a real partial/extension receipt).
        d = {**CLEAN, 'payments': [{'status': 'paid', 'paid_date': '2026-08-15', 'amount': 100}]}
        row = invoice(d, invnum='2020104')
        filed_rows = [{'invnum': 'TAX_REVIEW_2026-08', 'data': {'dor_confirmation_number': 'DOR-12345'}}]
        opener, writes = self._opener_for(row, filed_rows=filed_rows)
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020104', 'test_record', '', opener)
        self.assertFalse(ok)
        self.assertEqual(writes, [])

    def test_invalid_reason_rejected_before_any_fetch(self):
        opener, writes = self._opener_for(None)
        called = []
        def tracking_opener(req, timeout=None):
            called.append(req.full_url); return opener(req, timeout)
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020102v1', 'not_a_real_reason', '', tracking_opener)
        self.assertFalse(ok)
        self.assertEqual(called, [])

    def test_other_reason_without_note_rejected(self):
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020102v1', 'other', '  ', lambda *a, **k: (_ for _ in ()).throw(AssertionError('should not fetch')))
        self.assertFalse(ok)
        self.assertIn('note', message.lower())

    def test_other_reason_with_note_is_excludable_and_note_is_stored(self):
        row = test_record_invoice()
        opener, writes = self._opener_for(row)
        ok, message = api.exclude_record('https://example.test', fake_key(), '2020102v1', 'other', 'April confirmed this was a sandbox entry.', opener)
        self.assertTrue(ok)
        self.assertEqual(writes[0]['body']['data']['excluded_note'], 'April confirmed this was a sandbox entry.')

    def test_nonexistent_invoice_rejected(self):
        opener, writes = self._opener_for(None)
        ok, message = api.exclude_record('https://example.test', fake_key(), '9999999', 'test_record', '', opener)
        self.assertFalse(ok)
        self.assertEqual(writes, [])

    def test_system_row_rejected(self):
        row = invoice({**CLEAN}, invnum='TAX_REVIEW_2026-09')
        opener, writes = self._opener_for(row)
        ok, message = api.exclude_record('https://example.test', fake_key(), 'TAX_REVIEW_2026-09', 'test_record', '', opener)
        self.assertFalse(ok)
        self.assertEqual(writes, [])


class RestoreRecordTests(unittest.TestCase):
    def test_excluded_record_restores_cleanly_and_logs_the_restore(self):
        d = {**CLEAN, 'excluded_from_reconciliation': True, 'excluded_reason': 'test_record',
             'excluded_at': '2026-11-03T12:00:00Z', 'excluded_by': 'report administrator'}
        row = invoice(d, invnum='2020102v1')
        original_financial = copy.deepcopy(CLEAN)
        writes = []
        def opener(request, timeout=None):
            if request.get_method() == 'PATCH':
                writes.append(json.loads(request.data)); return FakeJsonResponse([])
            return FakeJsonResponse([row])
        ok, message = api.restore_record('https://example.test', fake_key(), '2020102v1', opener)
        self.assertTrue(ok)
        written = writes[0]['data']
        self.assertFalse(written['excluded_from_reconciliation'])
        self.assertIsNotNone(written['restored_at'])
        # History kept, never erased.
        self.assertEqual(written['excluded_reason'], 'test_record')
        for key in original_financial:
            self.assertEqual(written[key], original_financial[key], key)

    def test_restore_of_nonexistent_invoice_rejected(self):
        ok, message = api.restore_record('https://example.test', fake_key(), '9999999', lambda *a, **k: FakeJsonResponse([]))
        self.assertFalse(ok)


class FetchFiledPeriodsTests(unittest.TestCase):
    def test_only_periods_with_a_confirmation_number_count_as_filed(self):
        rows = [
            {'invnum': 'TAX_REVIEW_2026-08', 'data': {'dor_confirmation_number': 'DOR-1'}},
            {'invnum': 'TAX_REVIEW_2026-09', 'data': {'reviewed': True}},  # reviewed, not filed
            {'invnum': 'TAX_REVIEW_2026-10', 'data': {'dor_confirmation_number': ''}},  # blank
        ]
        opener = lambda req, timeout=None: FakeJsonResponse(rows)
        filed = api.fetch_filed_periods('https://example.test', fake_key(), opener)
        self.assertEqual(filed, {'2026-08'})


class DispatchTests(unittest.TestCase):
    """End-to-end through lib/section7_http.py's handle() -- the actual POST route a browser
    would hit."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': 'synthetic-admin-password-only', 'SUPABASE_KEY': fake_key(), 'VERCEL_ENV': 'preview'})
        self.env.start()
        from lib import admin_auth as auth
        auth._ATTEMPTS.clear()
        self.auth = auth
        # authorized() with base_url/key does a real revocation-table lookup -- these tests
        # exercise the exclude/restore dispatch, not the revocation table itself (that has its
        # own dedicated tests in test_admin_auth.py), so a real network call is never right here.
        self.revocations = patch.object(auth, 'fetch_revoked_nonces', return_value=set())
        self.revocations.start()

    def tearDown(self):
        self.env.stop()
        self.revocations.stop()

    def authorized_headers(self):
        return {'Cookie': self.auth.COOKIE + '=' + self.auth.issue_session('https://preview.example.test'),
                'Origin': 'https://preview.example.test'}

    def test_exclude_requires_authentication(self):
        h = FakeHandler('/api/webhook?section7=exclude', {'invnum': '2020102v1', 'reason': 'test_record'}, {'Origin': 'https://preview.example.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 401)

    def test_exclude_rejects_invalid_invnum_shape(self):
        h = FakeHandler('/api/webhook?section7=exclude', {'invnum': '../../etc/passwd', 'reason': 'test_record'}, self.authorized_headers())
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 400)

    def test_exclude_success_returns_200_and_blocked_record_returns_409(self):
        row = test_record_invoice()
        with patch.object(api, 'exclude_record', return_value=(True, None)) as mocked:
            h = FakeHandler('/api/webhook?section7=exclude', {'invnum': '2020102v1', 'reason': 'test_record'}, self.authorized_headers())
            api.handle(h, 'POST', 'https://example.test', fake_key())
            self.assertEqual(h.code, 200)
            mocked.assert_called_once()
        with patch.object(api, 'exclude_record', return_value=(False, 'Paid — cannot be excluded.')):
            h = FakeHandler('/api/webhook?section7=exclude', {'invnum': '2020101', 'reason': 'test_record'}, self.authorized_headers())
            api.handle(h, 'POST', 'https://example.test', fake_key())
            self.assertEqual(h.code, 409)
            self.assertIn('Paid', h.data()['error'])

    def test_restore_success_returns_200(self):
        with patch.object(api, 'restore_record', return_value=(True, None)) as mocked:
            h = FakeHandler('/api/webhook?section7=restore', {'invnum': '2020102v1'}, self.authorized_headers())
            api.handle(h, 'POST', 'https://example.test', fake_key())
            self.assertEqual(h.code, 200)
            mocked.assert_called_once()

    def test_report_respects_filter_query_param(self):
        rows = [invoice({**CLEAN, 'total_override': 1}, invnum='2020150')]
        coverage = {'complete': True, 'fingerprint': 'fp', 'completed_at': '2026-11-03T00:00:00Z'}
        h = FakeHandler('/api/webhook?section7=report&period=2026-10&filter=all', headers=self.authorized_headers())
        with patch.object(api, 'fetch_filed_periods', return_value=set()):
            api.handle(h, 'GET', 'https://example.test', fake_key(), lambda *a: (rows, coverage))
        self.assertEqual(h.code, 200)
        self.assertEqual(h.data()['filter_mode'], 'all')

    def test_report_rejects_invalid_filter(self):
        h = FakeHandler('/api/webhook?section7=report&period=2026-10&filter=bogus', headers=self.authorized_headers())
        api.handle(h, 'GET', 'https://example.test', fake_key())
        self.assertEqual(h.code, 400)

    def test_report_attaches_exclusion_block_reason_per_row(self):
        rows = [paid_invoice(total_override=CLEAN['total_override'] + 1)]
        coverage = {'complete': True, 'fingerprint': 'fp', 'completed_at': '2026-11-03T00:00:00Z'}
        h = FakeHandler('/api/webhook?section7=report&period=2026-10', headers=self.authorized_headers())
        with patch.object(api, 'fetch_filed_periods', return_value=set()):
            api.handle(h, 'GET', 'https://example.test', fake_key(), lambda *a: (rows, coverage))
        self.assertEqual(h.code, 200)
        result = h.data()['results'][0]
        self.assertIn('Paid', result['exclusion_block_reason'])


if __name__ == '__main__':
    unittest.main()
