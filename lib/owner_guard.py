"""Shared fail-closed boundary for owner-only HTTP actions.

Public signature and employee endpoints use separate, scoped authorization. Never
use a browser-supplied identity or the public Supabase key as owner authorization.
"""
import os
from lib import admin_auth
from lib.admin_http import response

BASE_URL = 'https://cwvdfgrjlrpsdfwduwvn.supabase.co'


def require_owner(handler, *, write=True):
    try:
        if not admin_auth.same_origin(handler, required=write):
            response(handler, 403, {'error': 'Open this action from the SWJ website.'})
            return False
        if not admin_auth.authorized(handler, base_url=BASE_URL,
                                     key=os.environ.get('SUPABASE_KEY', '')):
            response(handler, 401, {'error': 'Sign in to continue.'})
            return False
    except Exception:
        response(handler, 401, {'error': 'Could not verify your session. Sign in again.'})
        return False
    return True
