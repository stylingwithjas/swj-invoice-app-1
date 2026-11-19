import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


invoices = module('invoice_save_split', 'api/invoices.py')

FIXTURE = {
    'client': 'Preview Test Only', 'address': 'Synthetic property', 'invnum': 'TEST-SPLIT',
    'invdate': '2026-11-18', 'startdate': '2026-11-25', 'baserooms': 'Living room',
    'extrate': 333.33, 'taxrate': 10.55, 'base_price': 2000,
    'addons': [], 'promo1': False, 'promo2': False, 'promo3': False, 'promo4': False,
    'subtotal_override': 500, 'tax_override': 52.75, 'total_override': 552.75,
    'generator_request': True,
    'rental_service_split': True, 'rental_total': 500, 'service_total': 1500,
    'base_split': {'rental': 500, 'service': 1500}, 'rental_term_days': 45,
    'rental_split_pct': 25,
    'contract_terms_version': 'v2_2026_11_ext_tax_on_top',
}


class SplitOverrideReasonTests(unittest.TestCase):
    """Nov 18, 2026 split-override spec item 3: a reason is mandatory whenever the rental
    split isn't the standard 50/50. Server-side backstop (api/invoices.py) for the same
    check the generator already makes client-side, so it can't be bypassed by posting
    directly. None of these cases should touch the database when rejected."""

    def setUp(self):
        for guard in ('require_verified', 'require_number', 'require_owner'):
            patcher = patch.object(invoices, guard, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        stored = patch.object(invoices, 'stored_invoice', return_value=None)
        stored.start()
        self.addCleanup(stored.stop)

    def fake_handler(self, body):
        h = invoices.handler.__new__(invoices.handler)
        raw = json.dumps(body).encode()
        h.headers = {'Content-Length': str(len(raw))}
        h.rfile = io.BytesIO(raw)
        h.result = None
        h._respond = lambda code, b: setattr(h, 'result', (code, b))
        return h

    def test_non_standard_split_without_reason_rejected_before_any_write(self):
        h = self.fake_handler({**FIXTURE, 'split_reason': ''})
        with patch.object(invoices, 'supabase_request') as db:
            h.do_POST()
        self.assertEqual(h.result[0], 422)
        self.assertIn('reason', h.result[1]['error'].lower())
        db.assert_not_called()

    def test_non_standard_split_with_reason_proceeds(self):
        h = self.fake_handler({**FIXTURE, 'split_reason': 'Client provided large items'})
        with patch.object(invoices, 'supabase_request', return_value=(201, {})) as db:
            h.do_POST()
        self.assertEqual(h.result[0], 200)
        db.assert_called_once()

    def test_standard_50_50_never_requires_a_reason(self):
        h = self.fake_handler({**FIXTURE, 'rental_split_pct': 50, 'split_reason': ''})
        with patch.object(invoices, 'supabase_request', return_value=(201, {})) as db:
            h.do_POST()
        self.assertEqual(h.result[0], 200)
        db.assert_called_once()

    def test_missing_rental_split_pct_defaults_to_standard(self):
        # An invoice saved before this field existed has no rental_split_pct at all --
        # must default to 50 (standard), never be treated as an unexplained override.
        body = {**FIXTURE, 'split_reason': ''}
        del body['rental_split_pct']
        h = self.fake_handler(body)
        with patch.object(invoices, 'supabase_request', return_value=(201, {})) as db:
            h.do_POST()
        self.assertEqual(h.result[0], 200)
        db.assert_called_once()

    def test_draft_with_non_standard_split_and_no_reason_still_saves(self):
        # Drafts are explicitly allowed to be incomplete (saveDraft() doesn't even require
        # a client name or address); the reason requirement gates a real generated
        # invoice, not an in-progress draft.
        h = self.fake_handler({**FIXTURE, '_is_draft': True, 'split_reason': ''})
        with patch.object(invoices, 'supabase_request', return_value=(201, {})) as db:
            h.do_POST()
        self.assertEqual(h.result[0], 200)
        db.assert_called_once()

    def test_full_mode_invoice_never_requires_a_reason(self):
        # rental_service_split:False (full/plain tax mode) never gates on this at all,
        # regardless of whatever rental_split_pct happens to be left in the payload.
        h = self.fake_handler({**FIXTURE, 'rental_service_split': False, 'split_reason': ''})
        with patch.object(invoices, 'supabase_request', return_value=(201, {})) as db:
            h.do_POST()
        self.assertEqual(h.result[0], 200)
        db.assert_called_once()


if __name__ == '__main__':
    unittest.main()
