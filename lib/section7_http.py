"""GET-only database reader and isolated Section 7 HTTP dispatch.

This is imported only for ?section7= routes in the existing webhook function;
no new Vercel function, cron, Stripe call, mail send or database mutation.
"""
import datetime as dt
import base64
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from . import section7_auth as auth
from .section7 import build_report, csv_report

PAGE_SIZE = 250
MAX_SECONDS = 45
MAX_ROWS = 100000


class IncompleteScan(Exception):
    pass


def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def read_pass(base_url, key, deadline, opener=urllib.request.urlopen):
    rows, last, expected, seen = [], None, None, set()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IncompleteScan('Scan timed out. No complete historical result is available; retry.')
        params = {'select': 'invnum,client,invdate,created_at,data', 'order': 'invnum.asc', 'limit': PAGE_SIZE}
        if last is not None: params['invnum'] = 'gt.' + last
        request = urllib.request.Request(base_url.rstrip('/') + '/rest/v1/invoices?' + urllib.parse.urlencode(params),
            headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Prefer': 'count=exact'})
        with opener(request, timeout=min(15, remaining)) as response:
            batch = json.loads(response.read())
            content_range = response.headers.get('Content-Range', '')
        match = re.fullmatch(r'(?:\d+-\d+|\*)/(\d+)', content_range)
        if not isinstance(batch, list) or not match:
            raise IncompleteScan('Database row coverage could not be verified. No complete result is available.')
        if expected is None:
            expected = int(match.group(1))
            if expected > MAX_ROWS:
                raise IncompleteScan('Historical dataset exceeds the report limit; use a transaction-consistent read-only database audit.')
        for row in batch:
            value = row.get('invnum') if isinstance(row, dict) else None
            if not isinstance(value, str) or not value or value in seen:
                raise IncompleteScan('Invoice identifiers were missing, repeated or out of order. Completeness could not be verified.')
            last = value; seen.add(value); rows.append(row)
        if len(rows) > expected or len(rows) > MAX_ROWS:
            raise IncompleteScan('Invoice inventory changed during the scan. Retry.')
        # Do not assume a short page is the end: PostgREST may cap pages below
        # the requested limit. Exact count plus the keyset establishes coverage.
        if len(rows) == expected: return rows
        if not batch:
            raise IncompleteScan('The database returned an incomplete invoice inventory. Retry.')


def scan(base_url, key, opener=urllib.request.urlopen):
    try:
        payload = key.split('.')[1]
        role = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('role')
    except Exception:
        role = None
    if role != 'service_role':
        raise IncompleteScan('The existing server database key does not establish service-role visibility. No complete inventory can be certified.')
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    deadline = time.monotonic() + MAX_SECONDS
    first = read_pass(base_url, key, deadline, opener)
    second = read_pass(base_url, key, deadline, opener)
    stamp = fingerprint(first)
    if stamp != fingerprint(second):
        raise IncompleteScan('Invoice records changed between verification passes. Retry; no report was certified complete.')
    return first, {'complete': True, 'verification': 'two matching full keyset scans',
        'started_at': started, 'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'fingerprint': stamp, 'database_rows': len(first),
        'snapshot_note': 'Two matching reads are not a transactional database snapshot. Changes after reading are possible; rerun after bookkeeping updates.'}


def response(handler, code, data, headers=None):
    body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
    if len(body) > 3500000:
        raise IncompleteScan('The complete report exceeds the response budget. Use a transaction-consistent read-only database audit; no rows were silently omitted.')
    handler.send_response(code)
    for k, v in [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store, private'),
                 ('Pragma', 'no-cache'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                 ('Content-Length', str(len(body))), *(headers or [])]:
        handler.send_header(k, v)
    # Intentionally no wildcard CORS, unlike the old webhook responses.
    handler.end_headers(); handler.wfile.write(body)


def handle(handler, method, base_url, key, scanner=None):
    scanner = scan if scanner is None else scanner
    query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query, keep_blank_values=True)
    if 'section7' not in query: return False
    action = query['section7'][0]
    try:
        request_origin = auth.origin(handler)
        if not auth.same_origin(handler, required=method == 'POST'):
            response(handler, 403, {'error': 'Use the report from this site; cross-origin requests are not permitted.'}); return True
        if method == 'GET' and action == 'session':
            response(handler, 200, {'configured': auth.configured(), 'authenticated': auth.authorized(handler)}); return True
        if method == 'POST' and action == 'login':
            if not auth.configured():
                response(handler, 503, {'error': 'Secure report sign-in is not configured. No database query was made.'}); return True
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
        if not auth.configured():
            response(handler, 503, {'error': 'Secure report sign-in is not configured. No database query was made.'}); return True
        if not auth.authorized(handler):
            response(handler, 401, {'error': 'Report administrator sign-in required.'}); return True
        if method == 'GET' and action == 'report':
            period = query.get('period', ['2026-10'])[0]
            if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or not 1900 <= int(period[:4]) <= 9999:
                response(handler, 400, {'error': 'Use a valid YYYY-MM review period.'}); return True
            rows, coverage = scanner(base_url, key)
            report = build_report(rows, period)
            report['coverage'] = coverage; report['fingerprint'] = coverage['fingerprint']
            report['exports'] = {view: csv_report(report, view) for view in ('all', 'paid', 'paid_period', 'unverifiable')}
            response(handler, 200, report); return True
        response(handler, 404, {'error': 'Unknown report action.'}); return True
    except IncompleteScan as error:
        response(handler, 503, {'error': str(error), 'coverage': {'complete': False}}); return True
    except Exception:
        # Never echo URLs, database response bodies, headers, payloads or secrets.
        response(handler, 503, {'error': 'Historical scan unavailable. No complete result can be shown; retry or check server configuration.', 'coverage': {'complete': False}}); return True
