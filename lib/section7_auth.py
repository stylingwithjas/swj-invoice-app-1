"""Server-enforced report access; the browser-only app gate is not authorization.

No new DB credentials. A separate high-entropy report password must be configured
in secure server environment variables; never reuse the exposed frontend gate.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError

COOKIE = '__Secure-swj_section7'
LIFETIME = 1800
_LOCK = threading.Lock()
_ATTEMPTS = []


def configured():
    return len(os.environ.get('SWJ_REPORT_ADMIN_PASSWORD', '')) >= 20 and bool(os.environ.get('SUPABASE_KEY'))


def signing_key():
    if not configured():
        raise ValueError('Secure report sign-in is not configured.')
    # Domain-separated derivative; the existing server DB credential never
    # leaves the server. Password rotation invalidates every report session.
    material = b'swj-section7/v1\0' + hashlib.sha256(os.environ['SWJ_REPORT_ADMIN_PASSWORD'].encode()).digest()
    return hmac.new(os.environ['SUPABASE_KEY'].encode(), material, hashlib.sha256).digest()


def encode(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode(raw):
    return base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)


def origin(handler):
    host = handler.headers.get('Host', '')
    if not host or len(host) > 255 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-' for c in host):
        raise ValueError('Invalid report origin.')
    # Production/preview cookies are always HTTPS. The trusted deployment routes
    # Host; forwarded host/proto headers supplied by callers are never trusted.
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


def authorized(handler, now=None):
    if not configured(): return False
    now = int(time.time() if now is None else now)
    try:
        cookie = SimpleCookie(); cookie.load(handler.headers.get('Cookie', ''))
        token = cookie[COOKIE].value
        if len(token) > 2048: return False
        payload, signature = token.split('.')
        expected = hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(signature)): return False
        claims = json.loads(decode(payload))
        return (claims.get('v') == 1 and type(claims.get('iat')) is int and type(claims.get('exp')) is int
                and claims['iat'] <= now + 30 and now < claims['exp']
                and 0 < claims['exp'] - claims['iat'] <= LIFETIME
                and claims.get('origin') == origin(handler) and claims.get('env') == environment())
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError, CookieError):
        return False


def cookie_header(token='', clear=False):
    return COOKIE + '=' + token + '; Path=/api/webhook; Secure; HttpOnly; SameSite=Strict; Max-Age=' + ('0' if clear else str(LIFETIME))


def allow_login(now=None):
    # Per-instance defense only. Configure shared edge throttling for this route
    # before production; never claim serverless memory is a global rate limiter.
    now = time.monotonic() if now is None else now
    with _LOCK:
        _ATTEMPTS[:] = [t for t in _ATTEMPTS if now - t < 60]
        if len(_ATTEMPTS) >= 8: return False
        _ATTEMPTS.append(now)
    return True


def password_matches(password):
    if not isinstance(password, str) or len(password) > 512 or not configured(): return False
    return hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                               hashlib.sha256(os.environ['SWJ_REPORT_ADMIN_PASSWORD'].encode()).digest())
