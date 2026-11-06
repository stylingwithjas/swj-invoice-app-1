"""Invoice/role-scoped bearer links; no client accounts or passwords.

The key is independent of Jasmine's password so changing her password does not
break client links. Tokens are bound to the issuing deployment origin/environment.
"""
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from lib import admin_auth

SIGN_ROLES = {'client', 'cosigner'}
CREW_ROLES = {'mover1', 'mover2', 'dmover', 'photographer'}
LIFETIME = 365 * 24 * 3600


def signing_key():
    secret = os.environ.get('SWJ_PRIVATE_LINK_SECRET') or os.environ.get('SUPABASE_KEY', '')
    if len(secret) < 20:
        raise ValueError('Private links are not configured.')
    return hmac.new(secret.encode(), b'swj-private-links/v1', hashlib.sha256).digest()


def valid_target(invnum, role, kind):
    return (isinstance(invnum, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', invnum))
            and not invnum.startswith(('DRAFT-', 'FINANCE_', 'TAX_REVIEW_', 'PRICING_', 'TIER_'))
            and role in (SIGN_ROLES if kind == 'proposal' else CREW_ROLES if kind == 'confirm' else set()))


def issue(invnum, role, kind, origin, now=None):
    if not valid_target(invnum, role, kind):
        raise ValueError('Invalid private-link target.')
    now = int(time.time() if now is None else now)
    claims = {'v':1, 'inv':invnum, 'role':role, 'kind':kind, 'origin':origin,
              'env':admin_auth.environment(), 'iat':now, 'exp':now+LIFETIME}
    payload = admin_auth.encode(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode())
    signature = admin_auth.encode(hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest())
    return payload+'.'+signature


def verify(token, invnum, role, kind, origin, now=None):
    try:
        if not isinstance(token, str) or len(token) > 2048 or not valid_target(invnum, role, kind):
            return False
        payload, signature = token.split('.')
        expected = hmac.new(signing_key(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, admin_auth.decode(signature)):
            return False
        claims = json.loads(admin_auth.decode(payload))
        now = int(time.time() if now is None else now)
        return (claims.get('v') == 1 and claims.get('inv') == invnum and claims.get('role') == role
                and claims.get('kind') == kind and claims.get('origin') == origin
                and claims.get('env') == admin_auth.environment()
                and type(claims.get('iat')) is int and type(claims.get('exp')) is int
                and claims['iat'] <= now+30 and now < claims['exp']
                and 0 < claims['exp']-claims['iat'] <= LIFETIME)
    except (ValueError, TypeError, KeyError, UnicodeError):
        return False


def link(origin, invnum, role='client', kind='proposal'):
    return origin+'/'+kind+'.html?'+urlencode({'inv':invnum, 'role':role,
                                             'token':issue(invnum, role, kind, origin)})


def secure_email_links(text, origin):
    """Stamp first-party approval links in both plain text and HTML email paths.

    Ignore Stripe URLs and other origins; never turn an arbitrary external link into
    a trusted SWJ approval link. Existing tokens are replaced with fresh ones.
    """
    pattern = re.compile(re.escape(origin) + r'/(proposal|confirm)\.html\?[^\s<>"\']+')
    def replace(match):
        parsed = urlsplit(match.group(0))
        query = parse_qs(parsed.query)
        kind = match.group(1)
        inv = query.get('inv', [''])[0]
        role = query.get('role', ['client' if kind == 'proposal' else ''])[0]
        return link(origin, inv, role, kind)
    return pattern.sub(replace, text or '')
