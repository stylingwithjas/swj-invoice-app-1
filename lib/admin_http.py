"""Single admin session HTTP dispatch: ?admin=session|login|logout.

This is the one login every admin page on the site now shares (today.html, index.html,
luxury.html, gs-invoice.html, board.html, payroll.html, archive.html, shop.html,
tax-report.html) -- see admin-auth.js for the client side. Section 7's own report
(lib/section7_http.py) and the Stripe reconciliation report (lib/stripe_recon_http.py) check
sessions through lib/admin_auth.py directly rather than through this file, since each already
has its own ?section7=/?stripe_recon= login/session actions wired to the same module -- no
second password prompt, no separate cookie, just the same admin_auth session either way.

Imported only for ?admin= routes in the existing webhook function; no new Vercel function, cron,
Stripe call or mail send.
"""
import json
import re
import urllib.parse

from . import admin_auth as auth


def response(handler, code, data, headers=None):
    body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
    handler.send_response(code)
    for k, v in [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store, private'),
                 ('Pragma', 'no-cache'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                 ('Content-Length', str(len(body))), *(headers or [])]:
        handler.send_header(k, v)
    handler.end_headers(); handler.wfile.write(body)


def handle(handler, method, base_url, key):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query, keep_blank_values=True)
    if 'admin' not in query: return False
    action = query['admin'][0]
    try:
        request_origin = auth.origin(handler)
        if not auth.same_origin(handler, required=method == 'POST'):
            response(handler, 403, {'error': 'Use the site itself; cross-origin requests are not permitted.'}); return True

        if method == 'GET' and action == 'session':
            authed = auth.authorized(handler, base_url=base_url, key=key)
            headers = []
            if authed:
                renewed = auth.renewed_session_cookie(handler, base_url=base_url, key=key)
                if renewed: headers.append(('Set-Cookie', renewed))
            response(handler, 200, {'configured': auth.configured(), 'authenticated': authed}, headers); return True

        if method == 'POST' and action == 'login':
            if not auth.configured():
                response(handler, 503, {'error': 'Admin sign-in is not configured. No database query was made.'}); return True
            if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                response(handler, 415, {'error': 'JSON request required.'}); return True
            if not auth.allow_login():
                response(handler, 429, {'error': 'Too many sign-in attempts. Wait one minute and retry.'}, [('Retry-After', '60')]); return True
            try:
                length = int(handler.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048: raise ValueError()
                body = json.loads(handler.rfile.read(length))
                if not isinstance(body, dict): raise ValueError()
            except (ValueError, TypeError, json.JSONDecodeError):
                response(handler, 400, {'error': 'Invalid sign-in request.'}); return True
            if not auth.password_matches(body.get('password')):
                # Deliberately the same generic message regardless of WHY it failed (wrong
                # password vs. missing configuration already handled above) -- no hint given
                # about which part was wrong.
                response(handler, 401, {'error': 'Incorrect password.'}); return True
            response(handler, 200, {'authenticated': True}, [('Set-Cookie', auth.cookie_header(auth.issue_session(request_origin)))]); return True

        if method == 'POST' and action == 'logout':
            # Sign out / "forget this device" are the same action: revoke this device's own
            # session nonce server-side (never an arbitrary nonce the client supplies), then
            # clear the cookie. Idempotent and harmless if there was nothing valid to revoke.
            nonce = auth.current_nonce(handler)
            if nonce:
                try: auth.revoke_session(base_url, key, nonce)
                except Exception: pass  # the cookie clear below still happens either way
            response(handler, 200, {'authenticated': False}, [('Set-Cookie', auth.cookie_header(clear=True))]); return True

        response(handler, 404, {'error': 'Unknown admin action.'}); return True
    except Exception:
        response(handler, 503, {'error': 'Admin sign-in unavailable. Retry or check server configuration.'}); return True
