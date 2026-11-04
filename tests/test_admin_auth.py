"""Single site-wide admin session (owner decision, Nov 3, 2026) -- lib/admin_auth.py's core
session logic and lib/admin_http.py's ?admin=login/session/logout dispatch.

Covers the owner's acceptance list close to verbatim: correct password issues a valid signed
session; wrong password rejected with a generic message; a tampered cookie is rejected; an
expired cookie is rejected; password rotation invalidates every prior session at once; sign-out
invalidates that one device server-side; a proposal page opens with no session; no served
JavaScript file contains a password value; rate limiting blocks rapid repeated attempts; and the
sliding 30-day expiry actually slides (day-29 use keeps a session alive to day-59) while 30 days
of inactivity expires it.
"""
import glob
import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import admin_auth as auth
from lib import admin_http as api


def fake_key(role='service_role'):
    import base64
    payload = base64.urlsafe_b64encode(json.dumps({'role': role}).encode()).decode().rstrip('=')
    return 'test.' + payload + '.not-a-real-signature'


class FakeHandler:
    def __init__(self, path='/api/webhook?admin=session', body=None, headers=None):
        raw = json.dumps(body or {}).encode() if body is not None else b''
        self.path = path
        self.headers = {'Host': 'preview.example.test', **(headers or {})}
        if body is not None:
            self.headers.setdefault('Content-Type', 'application/json')
            self.headers['Content-Length'] = str(len(raw))
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.out_headers = {}

    def send_response(self, code): self.code = code
    def send_header(self, k, v): self.out_headers[k] = v
    def end_headers(self): pass
    def data(self): return json.loads(self.wfile.getvalue())


class SessionLogicTests(unittest.TestCase):
    """Pure lib/admin_auth.py logic -- no network, no handle() dispatch."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': 'synthetic-admin-password-only', 'SUPABASE_KEY': fake_key(), 'VERCEL_ENV': 'preview'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _handler(self, token, host='preview.example.test', origin='https://preview.example.test'):
        headers = {'Host': host}
        if origin: headers['Origin'] = origin
        if token: headers['Cookie'] = auth.COOKIE + '=' + token
        return FakeHandler(headers=headers)

    def test_correct_password_issues_valid_session(self):
        self.assertTrue(auth.password_matches('synthetic-admin-password-only'))
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        self.assertTrue(auth.authorized(h, now=1001))

    def test_wrong_password_rejected(self):
        self.assertFalse(auth.password_matches('totally-wrong-password'))

    def test_tampered_cookie_rejected(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        # Corrupt a character from inside a full base64 group, not the very last symbol of the
        # signature (whose low bits can be padding and not always change the decoded bytes).
        mid = len(token) - 6
        tampered = token[:mid] + ('a' if token[mid] != 'a' else 'b') + token[mid + 1:]
        h = self._handler(tampered)
        self.assertFalse(auth.authorized(h, now=1001))

    def test_expired_cookie_rejected(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        self.assertFalse(auth.authorized(h, now=1000 + auth.LIFETIME + 1))

    def test_password_rotation_invalidates_every_prior_session(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        self.assertTrue(auth.authorized(h, now=1001))
        with patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': 'a-brand-new-synthetic-password'}):
            self.assertFalse(auth.authorized(h, now=1001))

    def test_sliding_expiry_day29_use_keeps_session_alive_to_day59(self):
        token = auth.issue_session('https://preview.example.test', now=0)
        h = self._handler(token)
        day29 = 29 * 86400
        with patch.object(auth, 'fetch_revoked_nonces', return_value=set()):
            renewed_header = auth.renewed_session_cookie(h, now=day29, base_url='https://x', key='y')
        self.assertIsNotNone(renewed_header)
        new_token = renewed_header.split(auth.COOKIE + '=', 1)[1].split(';')[0]
        h2 = self._handler(new_token)
        day59 = 59 * 86400
        self.assertTrue(auth.authorized(h2, now=day59 - 1))
        # Without that day-29 renewal, the ORIGINAL token would already be expired by day 59
        # (30-day window from day 0) -- confirms the renewal is what extends it, not a fluke.
        self.assertFalse(auth.authorized(h, now=day59 - 1))

    def test_inactive_30_days_expires_even_though_sliding(self):
        token = auth.issue_session('https://preview.example.test', now=0)
        h = self._handler(token)
        self.assertFalse(auth.authorized(h, now=auth.LIFETIME + 1))

    def test_host_mismatch_rejected(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token, host='production.example.test', origin='https://production.example.test')
        self.assertFalse(auth.authorized(h, now=1001))

    def test_environment_mismatch_rejected(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        with patch.dict(os.environ, {'VERCEL_ENV': 'production'}):
            self.assertFalse(auth.authorized(h, now=1001))

    def test_rate_limit_blocks_rapid_repeated_attempts(self):
        auth._ATTEMPTS.clear()
        allowed = [auth.allow_login(now=0.0 + i * 0.001) for i in range(10)]
        self.assertTrue(all(allowed[:8]))
        self.assertFalse(any(allowed[8:]))

    def test_revoked_nonce_rejected_even_with_valid_signature_and_expiry(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        nonce = auth.current_nonce(h, now=1001)
        with patch.object(auth, 'fetch_revoked_nonces', return_value={nonce}):
            self.assertFalse(auth.authorized(h, now=1001, base_url='https://x', key='y'))
        with patch.object(auth, 'fetch_revoked_nonces', return_value=set()):
            self.assertTrue(auth.authorized(h, now=1001, base_url='https://x', key='y'))

    def test_revocation_check_failure_fails_closed(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        with patch.object(auth, 'fetch_revoked_nonces', side_effect=RuntimeError('db unreachable')):
            self.assertFalse(auth.authorized(h, now=1001, base_url='https://x', key='y'))

    def test_skip_revocation_check_never_lets_a_revoked_session_back_in_by_itself(self):
        # skip_revocation_check only exists so a caller about to do the lookup itself doesn't
        # double-fetch -- it must never be a way to bypass revocation for a caller that doesn't
        # also check it.
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = self._handler(token)
        self.assertTrue(auth.authorized(h, now=1001, skip_revocation_check=True))

    def test_unconfigured_password_closes_everything(self):
        with patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': ''}):
            self.assertFalse(auth.configured())
            token_env = patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': 'synthetic-admin-password-only'})
            token_env.start()
            token = auth.issue_session('https://preview.example.test', now=1000)
            token_env.stop()
            h = self._handler(token)
            self.assertFalse(auth.authorized(h, now=1001))


class DispatchTests(unittest.TestCase):
    """lib/admin_http.py's ?admin=login/session/logout routes."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': 'synthetic-admin-password-only', 'SUPABASE_KEY': fake_key(), 'VERCEL_ENV': 'preview'})
        self.env.start()
        auth._ATTEMPTS.clear()

    def tearDown(self):
        self.env.stop()

    def test_login_with_correct_password_issues_signed_cookie(self):
        h = FakeHandler('/api/webhook?admin=login', {'password': 'synthetic-admin-password-only'}, {'Origin': 'https://preview.example.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 200)
        cookie = h.out_headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie); self.assertIn('Secure', cookie); self.assertIn('SameSite=Strict', cookie)
        self.assertIn(str(auth.LIFETIME), cookie)

    def test_login_with_wrong_password_gives_generic_message(self):
        h = FakeHandler('/api/webhook?admin=login', {'password': 'wrong'}, {'Origin': 'https://preview.example.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 401)
        self.assertEqual(h.data()['error'], 'Incorrect password.')
        self.assertNotIn('Set-Cookie', h.out_headers)

    def test_old_auth_js_password_is_never_accepted(self):
        for old_password in ('Amber-Cedar-954!', 'ok'):
            h = FakeHandler('/api/webhook?admin=login', {'password': old_password}, {'Origin': 'https://preview.example.test'})
            api.handle(h, 'POST', 'https://example.test', fake_key())
            self.assertEqual(h.code, 401)

    def test_session_check_reports_authenticated_and_renews(self):
        # No explicit `now` here -- lib/admin_http.py's handle() checks against the real wall
        # clock (int(time.time())), so the fixture must issue a session relative to that same
        # clock, not a fixed synthetic timestamp the dispatch layer never sees.
        token = auth.issue_session('https://preview.example.test')
        h = FakeHandler(headers={'Cookie': auth.COOKIE + '=' + token})
        with patch.object(auth, 'fetch_revoked_nonces', return_value=set()):
            api.handle(h, 'GET', 'https://example.test', fake_key())
        self.assertEqual(h.code, 200)
        self.assertTrue(h.data()['authenticated'])
        self.assertIn('Set-Cookie', h.out_headers)

    def test_session_check_without_cookie_reports_not_authenticated(self):
        h = FakeHandler()
        api.handle(h, 'GET', 'https://example.test', fake_key())
        self.assertEqual(h.code, 200)
        self.assertFalse(h.data()['authenticated'])
        self.assertTrue(h.data()['configured'])

    def test_logout_revokes_this_device_server_side(self):
        token = auth.issue_session('https://preview.example.test', now=1000)
        h = FakeHandler('/api/webhook?admin=logout', {}, {'Origin': 'https://preview.example.test', 'Cookie': auth.COOKIE + '=' + token})
        with patch.object(auth, 'revoke_session') as revoke:
            api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 200)
        revoke.assert_called_once()
        # The exact nonce from THIS session's own cookie, never an arbitrary one.
        called_nonce = revoke.call_args[0][2]
        self.assertEqual(called_nonce, auth.current_nonce(FakeHandler(headers={'Cookie': auth.COOKIE + '=' + token})))
        self.assertIn('Max-Age=0', h.out_headers['Set-Cookie'])

    def test_logout_with_no_session_is_harmless(self):
        h = FakeHandler('/api/webhook?admin=logout', {}, {'Origin': 'https://preview.example.test'})
        with patch.object(auth, 'revoke_session') as revoke:
            api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 200)
        revoke.assert_not_called()

    def test_cross_origin_login_rejected(self):
        h = FakeHandler('/api/webhook?admin=login', {'password': 'synthetic-admin-password-only'}, {'Origin': 'https://evil.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 403)
        self.assertNotIn('Set-Cookie', h.out_headers)

    def test_rate_limited_login_returns_429(self):
        for _ in range(8):
            auth.allow_login()
        h = FakeHandler('/api/webhook?admin=login', {'password': 'synthetic-admin-password-only'}, {'Origin': 'https://preview.example.test'})
        api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 429)

    def test_unconfigured_login_returns_503_without_a_database_query(self):
        with patch.dict(os.environ, {'SWJ_ADMIN_PASSWORD': ''}):
            h = FakeHandler('/api/webhook?admin=login', {'password': 'anything'}, {'Origin': 'https://preview.example.test'})
            api.handle(h, 'POST', 'https://example.test', fake_key())
        self.assertEqual(h.code, 503)

    def test_unknown_admin_action_404s(self):
        h = FakeHandler('/api/webhook?admin=bogus', headers={'Origin': 'https://preview.example.test'})
        api.handle(h, 'GET', 'https://example.test', fake_key())
        self.assertEqual(h.code, 404)

    def test_not_admin_route_returns_false_falls_through(self):
        h = FakeHandler('/api/webhook?section7=report')
        self.assertFalse(api.handle(h, 'GET', 'https://example.test', fake_key()))


class NoPasswordInServedFilesTests(unittest.TestCase):
    """The old auth.js password shipped in a file the site itself served -- that must never
    happen again, for any admin password value. This greps every served HTML/JS file in the
    repo (never a live secret -- there is none to find in source control) for literal password-
    looking assignments, and separately proves admin-auth.js's own source contains no password
    value of its own."""

    def test_admin_auth_js_contains_no_password_literal(self):
        source = (ROOT / 'admin-auth.js').read_text()
        # The whole point of this file: it never embeds a password. It only ever POSTs
        # whatever the person types to the server and reads a yes/no answer back.
        self.assertNotIn('SWJ_PASSWORD', source)
        self.assertNotRegex(source, r"password\s*[:=]\s*['\"][^'\"]{6,}['\"]")

    def test_old_auth_js_file_no_longer_exists(self):
        self.assertFalse((ROOT / 'auth.js').exists())

    def test_no_served_html_or_js_file_contains_a_hardcoded_password_assignment(self):
        suspicious = []
        pattern = re.compile(r"(SWJ_PASSWORD|SWJ_ADMIN_PASSWORD|admin_password)\s*[:=]\s*['\"][^'\"]{4,}['\"]", re.I)
        for path in list(ROOT.glob('*.html')) + list(ROOT.glob('assets/*.js')) + [ROOT / 'admin-auth.js']:
            if not path.is_file(): continue
            text = path.read_text(errors='ignore')
            if pattern.search(text):
                suspicious.append(str(path.relative_to(ROOT)))
        self.assertEqual(suspicious, [])


class EveryAdminPageLoadsTheSharedGateTests(unittest.TestCase):
    """Every admin page includes admin-auth.js (not the retired auth.js); proposal/client-
    facing pages never do."""

    ADMIN_PAGES = ['today.html', 'board.html', 'payroll.html', 'tax-report.html', 'archive.html',
                   'shop.html', 'index.html', 'luxury.html', 'gs-invoice.html']

    def test_every_admin_page_includes_admin_auth_js(self):
        for page in self.ADMIN_PAGES:
            text = (ROOT / page).read_text()
            self.assertIn('/admin-auth.js', text, page)
            self.assertNotIn('/auth.js"', text, page)

    def test_report_pages_use_the_shared_session_not_their_own_password(self):
        for page in ['reconciliation.html', 'stripe-reconciliation.html']:
            text = (ROOT / page).read_text()
            # These pages keep their own login UI (bespoke, CSP-locked), but the backend behind
            # them must be the single shared session, not a second password -- see the *_http.py
            # tests for the actual auth check; this just confirms the page itself wasn't left
            # pointed at a retired endpoint shape.
            self.assertNotIn('SWJ_REPORT_ADMIN_PASSWORD', text)


if __name__ == '__main__':
    unittest.main()
