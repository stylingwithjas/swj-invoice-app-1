"""Read-only paid-invoice checks shared by generation and generator saves."""
import json
import os
import urllib.parse
import urllib.request

SUPABASE_URL = 'https://cwvdfgrjlrpsdfwduwvn.supabase.co'


def is_paid(data):
    return bool(data.get('paid') is True or
                float(data.get('amount_paid') or 0) > 0 or
                float(data.get('stripe_amount') or 0) > 0 or
                any(p.get('status') == 'paid' for p in (data.get('payments') or [])))


def stored_invoice(invnum):
    key = os.environ.get('SUPABASE_KEY', '')
    if not key:
        raise RuntimeError('Invoice protection is unavailable; no invoice was changed.')
    query = urllib.parse.urlencode({'invnum': 'eq.' + str(invnum), 'select': 'data', 'limit': 1})
    request = urllib.request.Request(SUPABASE_URL + '/rest/v1/invoices?' + query,
                                    headers={'apikey': key, 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(request, timeout=10) as response:
        rows = json.loads(response.read())
    if not isinstance(rows, list):
        raise RuntimeError('Invoice protection is unavailable; no invoice was changed.')
    return rows[0]['data'] if rows else None
