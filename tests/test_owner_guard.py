"""Authorization regression tests; no network, email or payment calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from lib import owner_guard
from tests.test_admin_auth import FakeHandler

ROOT = Path(__file__).resolve().parents[1]


class OwnerBoundaryTests(unittest.TestCase):
    def test_missing_session_rejected_before_financial_work(self):
        for name, methods in {'stripe':['POST'], 'invoices':['GET','POST'],
                              'agent':['POST'], 'search':['POST'], 'email':['POST'],
                              'generate_receipt':['POST'], 'distance':['POST'],
                              'counter':['GET','POST'], 'generate':['POST']}.items():
            spec = importlib.util.spec_from_file_location('boundary_'+name, ROOT/'api'/f'{name}.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for method in methods:
                with self.subTest(endpoint=name, method=method):
                    request = FakeHandler(body={'action':'refund'}, headers={'Origin':'https://preview.example.test'})
                    with patch.object(owner_guard.admin_auth, 'authorized', return_value=False), patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
                        getattr(module.handler, 'do_'+method)(request)
                    self.assertEqual(request.code, 401)
                    self.assertEqual(request.rfile.tell(), 0)

    def test_webhook_requires_signature_and_configured_secret(self):
        spec = importlib.util.spec_from_file_location('boundary_webhook', ROOT/'api/webhook.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for secret, signature, valid, expected in (('', '', False, 503), ('fixture', '', False, 400), ('fixture', 'bad', False, 400)):
            request = FakeHandler(path='/api/webhook', body={'type':'payment_intent.succeeded'},
                                  headers={'Stripe-Signature':signature})
            request._respond = lambda code, data: owner_guard.response(request, code, data)
            with patch.dict(os.environ, {'STRIPE_WEBHOOK_SECRET':secret}), patch.object(module, 'verify_signature', return_value=valid), patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
                module.handler.do_POST(request)
            self.assertEqual(request.code, expected)

    def test_cron_without_configured_secret_cannot_send(self):
        spec = importlib.util.spec_from_file_location('boundary_cron', ROOT/'api/webhook.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        request = FakeHandler(path='/api/webhook?job=monthly_close')
        request._respond = lambda code, data: owner_guard.response(request, code, data)
        with patch.dict(os.environ, {'CRON_SECRET':''}), patch.object(module, 'run_monthly_close') as close:
            module.handler.do_GET(request)
            close.assert_not_called()
        self.assertEqual(request.code, 401)

    def test_cross_origin_and_missing_write_origin_rejected(self):
        for origin in (None, 'https://attacker.example'):
            request = FakeHandler(headers={} if origin is None else {'Origin':origin})
            with patch.object(owner_guard.admin_auth, 'authorized') as authorized:
                self.assertFalse(owner_guard.require_owner(request))
                authorized.assert_not_called()
            self.assertEqual(request.code, 403)

    def test_valid_owner_uses_revocation_check(self):
        request = FakeHandler(headers={'Origin':'https://preview.example.test'})
        with patch.dict(os.environ, {'SUPABASE_KEY':'fixture-only'}), patch.object(owner_guard.admin_auth, 'authorized', return_value=True) as authorized:
            self.assertTrue(owner_guard.require_owner(request))
            self.assertEqual(authorized.call_args.kwargs['key'], 'fixture-only')
            self.assertNotIn('skip_revocation_check', authorized.call_args.kwargs)

    def test_authorization_failure_is_closed(self):
        request = FakeHandler(headers={'Origin':'https://preview.example.test'})
        with patch.object(owner_guard.admin_auth, 'authorized', side_effect=RuntimeError('unavailable')):
            self.assertFalse(owner_guard.require_owner(request))
        self.assertEqual(request.code, 401)
        self.assertEqual(request.out_headers['Cache-Control'], 'no-store, private')


if __name__ == '__main__':
    unittest.main()
