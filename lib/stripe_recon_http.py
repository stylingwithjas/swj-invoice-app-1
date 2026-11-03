"""GET-only Stripe + database reader and isolated Stripe-reconciliation HTTP dispatch.

Reuses lib/section7_http.py's invoice scan (same table, same completeness verification) and
lib/section7_auth.py's session handling directly (same admin password, same signed cookie) --
this report is independent of Section 7 functionally, but there is no reason to duplicate
either the invoice-reading or the login machinery a second time.

Imported only for ?stripe_recon= routes in the existing webhook function; no new Vercel
function, cron, mail send, charge, refund or database mutation of any kind.
"""
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request

from . import section7_auth as auth
from .section7_http import scan as scan_invoices, IncompleteScan
from .stripe_recon import build_report, csv_report

STRIPE_HOST = 'https://api.stripe.com'
PAGE_SIZE = 100
MAX_SECONDS = 45
MAX_ROWS = 50000


def _stripe_key():
    return os.environ.get('STRIPE_RECONCILE_KEY', '')


def configured():
    """Layers one more requirement on top of section7_auth's own (password + SUPABASE_KEY):
    a dedicated, livemode, restricted Stripe key. A standard secret key (sk_) or a test-mode
    restricted key (rk_test_) is refused outright -- this report must never run with more
    access than it needs, and a test-mode key would read Stripe data it structurally cannot
    answer "is this really livemode" about (a test key never sees a livemode object at all)."""
    key = _stripe_key()
    return auth.configured() and key.startswith('rk_live_')


def stripe_list_all(path, extra_params, key, deadline):
    """Exhaustive cursor pagination (follows has_more to its real end) with a time budget and
    row ceiling. Stripe's list API has no exact-count header to verify against the way
    PostgREST's does, so completeness here means "paginated all the way to has_more=false
    within budget" -- raises IncompleteScan rather than returning a partial result silently."""
    rows, cursor = [], None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IncompleteScan('Stripe scan timed out before reaching the end of the list. No complete result is available; retry.')
        params = dict(extra_params)
        params['limit'] = PAGE_SIZE
        if cursor:
            params['starting_after'] = cursor
        request = urllib.request.Request(
            STRIPE_HOST + path + '?' + urllib.parse.urlencode(params),
            headers={'Authorization': 'Bearer ' + key})
        with urllib.request.urlopen(request, timeout=min(15, remaining)) as response:
            payload = json.loads(response.read())
        batch = payload.get('data')
        if not isinstance(batch, list):
            raise IncompleteScan('Stripe returned an unexpected response shape. No complete result is available.')
        rows.extend(batch)
        if len(rows) > MAX_ROWS:
            raise IncompleteScan('Stripe history exceeds this report\'s row ceiling; a longer-range, dedicated export is needed instead.')
        if not payload.get('has_more'):
            return rows
        if not batch:
            raise IncompleteScan('Stripe reported more results but returned an empty page. Retry.')
        cursor = batch[-1].get('id')
        if not cursor:
            raise IncompleteScan('A Stripe list page was missing object ids; pagination cannot continue safely.')


def stripe_scan(key, period):
    """Charges and refunds for the review period, plus a one-month pad on each side so a
    charge/payment just outside the calendar month can still satisfy the paid_date +-7 day
    window at the edges of the month. Livemode filtering happens in build_report() against
    each object's own `livemode` field, not by trusting the key's mode alone."""
    year, month = int(period[:4]), int(period[5:7])
    start = dt.date(year, month, 1) - dt.timedelta(days=14)
    end_month = month + 1 if month < 12 else 1
    end_year = year if month < 12 else year + 1
    end = dt.date(end_year, end_month, 1) + dt.timedelta(days=14)
    created = {'gte': int(dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc).timestamp()),
               'lte': int(dt.datetime(end.year, end.month, end.day, tzinfo=dt.timezone.utc).timestamp())}
    deadline = time.monotonic() + MAX_SECONDS
    charges = stripe_list_all('/v1/charges', {'created[gte]': created['gte'], 'created[lte]': created['lte']}, key, deadline)
    return charges


def response(handler, code, data, headers=None):
    body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
    if len(body) > 3500000:
        raise IncompleteScan('The complete report exceeds the response budget. A narrower month or a dedicated export is needed; no rows were silently omitted.')
    handler.send_response(code)
    for k, v in [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store, private'),
                 ('Pragma', 'no-cache'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                 ('Content-Length', str(len(body))), *(headers or [])]:
        handler.send_header(k, v)
    handler.end_headers(); handler.wfile.write(body)


def handle(handler, method, base_url, supabase_key, invoice_scanner=None, stripe_fetcher=None):
    invoice_scanner = scan_invoices if invoice_scanner is None else invoice_scanner
    stripe_fetcher = stripe_scan if stripe_fetcher is None else stripe_fetcher
    query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query, keep_blank_values=True)
    if 'stripe_recon' not in query:
        return False
    action = query['stripe_recon'][0]
    try:
        request_origin = auth.origin(handler)
        if not auth.same_origin(handler, required=method == 'POST'):
            response(handler, 403, {'error': 'Use the report from this site; cross-origin requests are not permitted.'}); return True
        if method == 'GET' and action == 'session':
            response(handler, 200, {
                'configured': configured(), 'authenticated': auth.authorized(handler),
                'password_configured': auth.configured(),
                'stripe_configured': _stripe_key().startswith('rk_live_'),
            }); return True
        if method == 'POST' and action == 'login':
            if not auth.configured():
                response(handler, 503, {'error': 'Secure report sign-in is not configured. No database or Stripe query was made.'}); return True
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
                response(handler, 401, {'error': 'Sign-in failed.'}); return True
            response(handler, 200, {'authenticated': True}, [('Set-Cookie', auth.cookie_header(auth.issue_session(request_origin)))]); return True
        if method == 'POST' and action == 'logout':
            response(handler, 200, {'authenticated': False}, [('Set-Cookie', auth.cookie_header(clear=True))]); return True
        if not auth.authorized(handler):
            response(handler, 401, {'error': 'Report administrator sign-in required.'}); return True
        if not configured():
            response(handler, 503, {'error': 'Stripe reconciliation is not configured (a livemode restricted Stripe key is required). No query was made.'}); return True
        if method == 'GET' and action == 'report':
            period = query.get('period', ['2026-10'])[0]
            if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or not 1900 <= int(period[:4]) <= 9999:
                response(handler, 400, {'error': 'Use a valid YYYY-MM review period.'}); return True
            invoice_rows, coverage = invoice_scanner(base_url, supabase_key)
            stripe_charges = stripe_fetcher(_stripe_key(), period)
            report = build_report(invoice_rows, stripe_charges, period)
            report['coverage'] = coverage
            report['exports'] = {view: csv_report(report, view) for view in ('all', 'matched', 'unmatched_charges', 'unmatched_items', 'mismatches')}
            response(handler, 200, report); return True
        response(handler, 404, {'error': 'Unknown report action.'}); return True
    except IncompleteScan as error:
        response(handler, 503, {'error': str(error), 'coverage': {'complete': False}}); return True
    except Exception:
        # Never echo URLs, Stripe/database response bodies, headers, payloads or secrets.
        response(handler, 503, {'error': 'Reconciliation scan unavailable. No complete result can be shown; retry or check server configuration.', 'coverage': {'complete': False}}); return True
