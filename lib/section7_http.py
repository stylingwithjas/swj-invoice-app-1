"""Section 7 HTTP dispatch: a GET-only historical scan/report, plus (owner request, Nov 3,
2026) two narrowly-scoped admin writes -- exclude/restore a record from the reconciliation
list. Every other route in this module remains read-only; exclude/restore only ever
add/update a fixed set of excluded_*/restored_* keys inside one invoice's own `data` JSONB,
server-side, after re-validating the hard blocks (paid/signed/filed) itself -- never trusting
a client-side check. No stored invoice figure, PDF, signature or payment record is ever
touched. Imported only for ?section7= routes in the existing webhook function; no new Vercel
function, cron, Stripe call or mail send.

Authenticated through lib/admin_auth.py's single site-wide admin session (owner decision, Nov
3, 2026) -- the same session every other admin page shares, so opening this report after
logging in anywhere else on the site needs no second password prompt. ?section7=login/
session/logout still exist as their own routes (so this page's own self-contained login UI and
assets/section7.js keep working unchanged), they just check/issue the one shared session now
instead of a separate SWJ_REPORT_ADMIN_PASSWORD.
"""
import datetime as dt
import base64
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from . import admin_auth as auth
from .section7 import build_report, csv_report, check_invoice, exclusion_block_reason, real_invoice, EXCLUDE_REASONS

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


def _role(key):
    try:
        payload = key.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('role')
    except Exception:
        return None


def fetch_invoice_row(base_url, key, invnum, opener=urllib.request.urlopen):
    """One invoice, by exact invnum, with its full `data` object -- the read half of the
    read-modify-write exclude/restore needs (PostgREST has no partial-JSONB PATCH without a
    stored function, so the whole `data` object is read, merged in Python, and written back
    whole -- same pattern tax-report.html's own DOR-confirmation save already uses)."""
    if _role(key) != 'service_role':
        raise IncompleteScan('The existing server database key does not establish service-role visibility. No write can be certified safe.')
    params = {'invnum': 'eq.' + invnum, 'select': 'invnum,client,invdate,created_at,data', 'limit': 1}
    request = urllib.request.Request(base_url.rstrip('/') + '/rest/v1/invoices?' + urllib.parse.urlencode(params),
        headers={'apikey': key, 'Authorization': 'Bearer ' + key})
    with opener(request, timeout=15) as resp:
        rows = json.loads(resp.read())
    if not isinstance(rows, list) or not rows:
        return None
    return rows[0]


def write_invoice_data(base_url, key, invnum, data, opener=urllib.request.urlopen):
    """Writes the WHOLE merged `data` object back to exactly one invoice row, by invnum. Never
    called with anything but a dict built by spreading the record's own current `data` and
    overriding only the excluded_*/restored_* keys -- see exclude()/restore() below -- so every
    other stored field (lines, subtotal, tax, grand, paid, payments, signed, ...) passes through
    completely untouched."""
    if _role(key) != 'service_role':
        raise IncompleteScan('The existing server database key does not establish service-role visibility. No write can be certified safe.')
    body = json.dumps({'data': data}).encode()
    request = urllib.request.Request(base_url.rstrip('/') + '/rest/v1/invoices?' + urllib.parse.urlencode({'invnum': 'eq.' + invnum}),
        data=body, method='PATCH',
        headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'})
    with opener(request, timeout=15) as resp:
        resp.read()


def fetch_filed_periods(base_url, key, opener=urllib.request.urlopen):
    """'YYYY-MM' periods that already carry a DOR confirmation number -- the Tax Report's own
    TAX_REVIEW_<period> records, read the same way tax-report.html itself reads them. A record
    with a receipt dated inside one of these periods can never be excluded (owner rule)."""
    if _role(key) != 'service_role':
        raise IncompleteScan('The existing server database key does not establish service-role visibility. No complete filed-period check can be certified.')
    params = {'invnum': 'like.TAX_REVIEW_%', 'select': 'invnum,data'}
    request = urllib.request.Request(base_url.rstrip('/') + '/rest/v1/invoices?' + urllib.parse.urlencode(params),
        headers={'apikey': key, 'Authorization': 'Bearer ' + key})
    with opener(request, timeout=15) as resp:
        rows = json.loads(resp.read())
    filed = set()
    for row in (rows if isinstance(rows, list) else []):
        invnum = row.get('invnum') if isinstance(row, dict) else None
        data = row.get('data') if isinstance(row, dict) else None
        if isinstance(invnum, str) and invnum.startswith('TAX_REVIEW_') and isinstance(data, dict) and data.get('dor_confirmation_number'):
            filed.add(invnum[len('TAX_REVIEW_'):])
    return filed


def exclude_record(base_url, key, invnum, reason, note, opener=urllib.request.urlopen):
    """Validates the hard blocks server-side (never trusting the client) and, if allowed, flags
    the record excluded. Returns (ok: bool, message_or_None). Idempotent: excluding an
    already-excluded record just updates its reason/note/timestamp."""
    if reason not in EXCLUDE_REASONS:
        return False, 'Choose a reason from the list.'
    if reason == 'other' and not (note or '').strip():
        return False, 'A short note is required for "Other."'
    row = fetch_invoice_row(base_url, key, invnum, opener)
    if row is None or not real_invoice(row):
        return False, 'No matching invoice record was found.'
    d = row.get('data') if isinstance(row.get('data'), dict) else {}
    # Any period works for live_paid/signed/receipt_dates -- period only affects the unrelated
    # paid_in_review_period flag, which this check never reads.
    check_result = check_invoice(row, dt.date.today().strftime('%Y-%m'))
    filed_periods = fetch_filed_periods(base_url, key, opener)
    blocked = exclusion_block_reason(check_result, filed_periods)
    if blocked:
        return False, blocked
    merged = dict(d)
    merged.update({
        'excluded_from_reconciliation': True, 'excluded_reason': reason,
        'excluded_note': (note or '').strip()[:500] if reason == 'other' else None,
        'excluded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        # No per-user login identity exists in this app's auth model (one shared admin
        # password) -- this records WHICH gate the action came through, not a personal name.
        'excluded_by': 'report administrator',
    })
    write_invoice_data(base_url, key, invnum, merged, opener)
    return True, None


def restore_record(base_url, key, invnum, opener=urllib.request.urlopen):
    """Clears the active exclusion flag. The prior excluded_reason/_note/_at/_by are kept as
    history (never erased) -- only excluded_from_reconciliation flips back to False, plus a
    restored_at/_by audit pair, so a restore is itself logged, never silent."""
    row = fetch_invoice_row(base_url, key, invnum, opener)
    if row is None or not real_invoice(row):
        return False, 'No matching invoice record was found.'
    d = row.get('data') if isinstance(row.get('data'), dict) else {}
    merged = dict(d)
    merged.update({
        'excluded_from_reconciliation': False,
        'restored_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'restored_by': 'report administrator',
    })
    write_invoice_data(base_url, key, invnum, merged, opener)
    return True, None


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
                response(handler, 401, {'error': 'Incorrect password.'}); return True
            response(handler, 200, {'authenticated': True}, [('Set-Cookie', auth.cookie_header(auth.issue_session(request_origin)))]); return True
        if method == 'POST' and action == 'logout':
            nonce = auth.current_nonce(handler)
            if nonce:
                try: auth.revoke_session(base_url, key, nonce)
                except Exception: pass
            response(handler, 200, {'authenticated': False}, [('Set-Cookie', auth.cookie_header(clear=True))]); return True
        if not auth.configured():
            response(handler, 503, {'error': 'Admin sign-in is not configured. No database query was made.'}); return True
        if not auth.authorized(handler, base_url=base_url, key=key):
            response(handler, 401, {'error': 'Admin sign-in required.'}); return True
        if method == 'GET' and action == 'report':
            period = query.get('period', ['2026-10'])[0]
            if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period) or not 1900 <= int(period[:4]) <= 9999:
                response(handler, 400, {'error': 'Use a valid YYYY-MM review period.'}); return True
            filter_mode = query.get('filter', ['active'])[0]
            if filter_mode not in ('active', 'excluded', 'all'):
                response(handler, 400, {'error': 'Use a valid filter: active, excluded, or all.'}); return True
            rows, coverage = scanner(base_url, key)
            report = build_report(rows, period, filter_mode)
            # Each row names its own exclusion block reason (or null) so the client never has to
            # re-derive "is this paid/signed/filed" itself -- the filed-periods check in
            # particular needs the TAX_REVIEW_<period> records, which only the server reads.
            # Best-effort only: this is an auxiliary eligibility check for the exclude feature,
            # not the historical scan itself -- a hiccup reading TAX_REVIEW_* rows must never
            # take down the whole read-only report. Failure here just means every row is
            # treated as "not known to be filed" (exclude is never silently allowed to bypass a
            # filed period either way -- paid/signed still block, and Jasmine's test records
            # carry no receipt dates to check against a filed period at all).
            try:
                filed_periods = fetch_filed_periods(base_url, key)
            except Exception:
                filed_periods = set()
            for row in report['results']:
                row['exclusion_block_reason'] = exclusion_block_reason(row, filed_periods)
            report['coverage'] = coverage; report['fingerprint'] = coverage['fingerprint']
            report['exports'] = {view: csv_report(report, view) for view in ('all', 'paid', 'paid_period', 'unverifiable')}
            response(handler, 200, report); return True
        if method == 'POST' and action in ('exclude', 'restore'):
            if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                response(handler, 415, {'error': 'JSON request required.'}); return True
            try:
                length = int(handler.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048: raise ValueError()
                body = json.loads(handler.rfile.read(length))
                if not isinstance(body, dict): raise ValueError()
            except (ValueError, TypeError, json.JSONDecodeError):
                response(handler, 400, {'error': 'Invalid request.'}); return True
            invnum = body.get('invnum')
            if not isinstance(invnum, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', invnum):
                response(handler, 400, {'error': 'Invalid invoice number.'}); return True
            if action == 'exclude':
                ok, message = exclude_record(base_url, key, invnum, body.get('reason'), body.get('note'))
            else:
                ok, message = restore_record(base_url, key, invnum)
            if not ok:
                response(handler, 409, {'error': message or 'This action could not be completed.'}); return True
            response(handler, 200, {'ok': True}); return True
        response(handler, 404, {'error': 'Unknown report action.'}); return True
    except IncompleteScan as error:
        response(handler, 503, {'error': str(error), 'coverage': {'complete': False}}); return True
    except Exception:
        # Never echo URLs, database response bodies, headers, payloads or secrets.
        response(handler, 503, {'error': 'Historical scan unavailable. No complete result can be shown; retry or check server configuration.', 'coverage': {'complete': False}}); return True
