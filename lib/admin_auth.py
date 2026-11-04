"""Single server-side admin session for the whole SWJ site (owner decision, Nov 3, 2026).

Replaces two things at once:
  - auth.js's client-side-only gate. Its password was never actually secret -- it shipped in a
    JS file the site itself serves, readable from view-source by anyone with the URL. Treat that
    old password as already public; it is retired here, never reused anywhere, never accepted by
    this module no matter what value is typed.
  - The separate Section 7 report password (the old lib/section7_auth.py). One login now unlocks
    every admin page, Section 7 included, with no second prompt -- lib/section7_http.py and
    lib/stripe_recon_http.py both check sessions through THIS module now.

Same constant-time-compare, same HMAC-signed-cookie pattern the old Section 7 gate used
(password comparison and session validation never touch the database, so a Supabase hiccup
can't itself lock the owners out of checking who they are), generalized and extended with:
  - A SLIDING 30-day expiry (owner, Nov 3: "I like that sliding") -- every authorized check
    reissues the cookie with a fresh 30-day window, so a device in regular use never sees a
    login prompt in practice, while a device idle for a full 30 days drops off on its own. This
    is deliberately not a fixed 30 days from login and not never-expiring.
  - Real server-side sign-out: a signed, stateless cookie can't be revoked by itself, so one
    tiny table (sql/admin_auth_tables.sql, service-role only, zero anon access) records a
    session's nonce when its device signs out; authorized() rejects that nonce even though the
    cookie's signature and expiry are still otherwise valid. "Sign out" and "forget this device"
    are the same action.
  - Changing SWJ_ADMIN_PASSWORD invalidates EVERY session at once, with no extra code: the
    signing key is itself derived from the password, so a rotated password makes every
    previously-issued signature stop matching, immediately, everywhere. This is the kill switch
    for a lost device or a leaked password -- sign-out (above) is the one-device, day-to-day
    tool; rotating the password is the "something is actually wrong" tool.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie, CookieError

COOKIE = '__Secure-swj_admin'
LIFETIME = 30 * 24 * 3600  # sliding: see issue_session()/authorized_response_cookie() below
_LOCK = threading.Lock()
_ATTEMPTS = []


def configured():
    return len(os.environ.get('SWJ_ADMIN_PASSWORD', '')) >= 20 and bool(os.environ.get('SUPABASE_KEY'))


def signing_key():
    if not configured():
        raise ValueError('Admin sign-in is not configured.')
    # Domain-separated derivative; the existing server DB credential never leaves the server.
    # Rotating SWJ_ADMIN_PASSWORD changes this key, which is what makes rotation a real,
    # immediate, whole-fleet kill switch -- see the module docstring.
    material = b'swj-admin/v1\0' + hashlib.sha256(os.environ['SWJ_ADMIN_PASSWORD'].encode()).digest()
    return hmac.new(os.environ['SUPABASE_KEY'].encode(), material, hashlib.sha256).digest()


def encode(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode(raw):
    return base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)


def origin(handler):
    host = handler.headers.get('Host', '')
    if not host or len(host) > 255 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-' for c in host):
        raise ValueError('Invalid admin request origin.')
    # Production/preview cookies are always HTTPS. The trusted deployment routes Host;
    # forwarded host/proto headers supplied by callers are never trusted.
    return 'https://' + host.lower()


def same_origin(handler, required=False):
    supplied = handler.headers.get('Origin')
    return (not required and not supplied) or supplied == origin(handler)


def environment():
    return os.environ.get('VERCEL_ENV', 'local')


def issue_session(request_origin, now=None):
    now = int(time.time() if now is None else now)
    claims = {'v': 1, 'iat': now, 'exp': now + LIFETIME, 'origin': request_origin,
              'env': environment(), 'nonce': secrets.token_urlsafe(18)}
    payload = encode(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode())
    signature = encode(hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest())
    return payload + '.' + signature


def _token_from_handler(handler):
    try:
        cookie = SimpleCookie(); cookie.load(handler.headers.get('Cookie', ''))
        return cookie[COOKIE].value
    except (KeyError, CookieError):
        return None


def _claims_if_signature_valid(token, now):
    """Decodes and returns the claims dict only if the signature checks out and the shape is
    sane -- does NOT check expiry, origin, env, or revocation; callers layer those on top. A
    malformed/forged/missing token returns None, never raises."""
    try:
        if not isinstance(token, str) or len(token) > 2048: return None
        payload, signature = token.split('.')
        expected = hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(signature)): return None
        claims = json.loads(decode(payload))
        if not (claims.get('v') == 1 and type(claims.get('iat')) is int and type(claims.get('exp')) is int
                and claims['iat'] <= now + 30 and 0 < claims['exp'] - claims['iat'] <= LIFETIME):
            return None
        return claims
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def authorized(handler, now=None, base_url=None, key=None, skip_revocation_check=False):
    """True only if every layer passes: a present, correctly-signed, unexpired, origin/env-
    matching cookie, AND (unless skip_revocation_check is set) its nonce is not in the
    server-side revocation table.

    base_url/key are optional so the signature/expiry/origin checks stay pure and testable
    without a network dependency -- but every real caller in this codebase passes them, so a
    signed-out device is actually rejected, not just a device that still has its old cookie.
    skip_revocation_check exists only for call sites that are themselves about to do that lookup
    anyway (avoids a redundant fetch); it is never used to let a revoked session back in."""
    if not configured(): return False
    now = int(time.time() if now is None else now)
    claims = _claims_if_signature_valid(_token_from_handler(handler), now)
    if claims is None: return False
    if not (now < claims['exp'] and claims.get('origin') == origin(handler) and claims.get('env') == environment()):
        return False
    if skip_revocation_check or not (base_url and key):
        return True
    try:
        return claims['nonce'] not in fetch_revoked_nonces(base_url, key)
    except Exception:
        # Fail closed, same posture as every other scan/report in this app: a revocation check
        # that couldn't be confirmed is never treated as "not revoked."
        return False


def current_nonce(handler, now=None):
    """The signed-in session's own nonce, for the sign-out/forget-this-device action to revoke
    -- only ever the caller's OWN current session, never an arbitrary nonce supplied by the
    client (nothing in this module ever accepts a nonce as request input)."""
    now = int(time.time() if now is None else now)
    claims = _claims_if_signature_valid(_token_from_handler(handler), now)
    return claims.get('nonce') if claims else None


def renewed_session_cookie(handler, now=None, base_url=None, key=None):
    """If the request carries a currently-authorized session, returns a Set-Cookie value that
    reissues it with a fresh LIFETIME window from now (the sliding part of "sliding 30 days") --
    same origin/env/nonce, new iat/exp. Returns None if there is nothing valid to renew. Callers
    append this to every authenticated response's headers, not just login, so "every genuine use
    of the site resets the clock" the way the owner asked for."""
    now = int(time.time() if now is None else now)
    if not authorized(handler, now, base_url, key):
        return None
    claims = _claims_if_signature_valid(_token_from_handler(handler), now)
    payload = encode(json.dumps({**claims, 'iat': now, 'exp': now + LIFETIME},
                                 separators=(',', ':'), sort_keys=True).encode())
    signature = encode(hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest())
    return cookie_header(payload + '.' + signature)


def cookie_header(token='', clear=False):
    return COOKIE + '=' + token + '; Path=/api/webhook; Secure; HttpOnly; SameSite=Strict; Max-Age=' + ('0' if clear else str(LIFETIME))


def allow_login(now=None):
    # Per-instance defense only. Configure shared edge throttling for this route before
    # production; never claim serverless memory is a global rate limiter -- a strong passphrase
    # is what actually makes brute force impractical here.
    now = time.monotonic() if now is None else now
    with _LOCK:
        _ATTEMPTS[:] = [t for t in _ATTEMPTS if now - t < 60]
        if len(_ATTEMPTS) >= 8: return False
        _ATTEMPTS.append(now)
    return True


def password_matches(password):
    if not isinstance(password, str) or len(password) > 512 or not configured(): return False
    return hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                               hashlib.sha256(os.environ['SWJ_ADMIN_PASSWORD'].encode()).digest())


# ── Server-side revocation (sign-out / "forget this device") ──────────────────────────────
# A dedicated, service-role-only table (sql/admin_auth_tables.sql) -- never exposed to anon,
# never another system row inside `invoices`. See that file's own comment for why.

def fetch_revoked_nonces(base_url, key, opener=urllib.request.urlopen):
    request = urllib.request.Request(
        base_url.rstrip('/') + '/rest/v1/admin_revoked_sessions?select=nonce',
        headers={'apikey': key, 'Authorization': 'Bearer ' + key})
    with opener(request, timeout=10) as resp:
        rows = json.loads(resp.read())
    return {row['nonce'] for row in rows if isinstance(row, dict) and isinstance(row.get('nonce'), str)}


def revoke_session(base_url, key, nonce, opener=urllib.request.urlopen):
    """Records one nonce as revoked (the signing-out device's own, and only ever that one), and
    opportunistically prunes rows older than LIFETIME -- their tokens would already be expired
    on their own, so there is nothing left to protect by keeping them."""
    body = json.dumps({'nonce': nonce}).encode()
    request = urllib.request.Request(
        base_url.rstrip('/') + '/rest/v1/admin_revoked_sessions',
        data=body, method='POST',
        headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                 'Prefer': 'resolution=ignore-duplicates,return=minimal'})
    with opener(request, timeout=10) as resp:
        resp.read()
    cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - LIFETIME))
    prune = urllib.request.Request(
        base_url.rstrip('/') + '/rest/v1/admin_revoked_sessions?' + urllib.parse.urlencode({'revoked_at': 'lt.' + cutoff}),
        method='DELETE', headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Prefer': 'return=minimal'})
    with opener(prune, timeout=10) as resp:
        resp.read()
