import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from lib.invoice_protection import stored_invoice, is_paid

SUPABASE_URL = "https://cwvdfgrjlrpsdfwduwvn.supabase.co"

def get_key():
    return os.environ.get('SUPABASE_KEY', '')

def supabase_request(method, path, body=None):
    key = get_key()
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Prefer', 'return=representation')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 500, {'error': str(e)}

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Return last 10 invoices
        status, data = supabase_request(
            'GET',
            'invoices?select=invnum,client,address,invdate,data&order=created_at.desc&limit=10'
        )
        self._respond(status if status < 300 else 200, data if isinstance(data, list) else [])

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
        except:
            self._respond(400, {'error': 'Invalid JSON'})
            return

        try:
            saved = stored_invoice(body.get('invnum', ''))
            if saved and is_paid(saved):
                self._respond(409, {'error': 'Paid invoice protected. No stored figures were changed.'})
                return
        except Exception:
            self._respond(503, {'error': 'Could not verify stored invoice status. Nothing was saved.'})
            return

        invnum = body.get('invnum', '')
        client = body.get('client', '')
        address = body.get('address', '')
        invdate = body.get('invdate', '')

        # Upsert invoice data
        status, data = supabase_request('POST', 'invoices', {
            'invnum': invnum,
            'client': client,
            'address': address,
            'invdate': invdate,
            'data': body
        })

        if status in [200, 201]:
            self._respond(200, {'saved': True, 'invnum': invnum})
        elif status == 409 and saved is not None:
            # Compare the receipt state at write time. A concurrent payment changes
            # at least one predicate, so this PATCH cannot overwrite a newly paid
            # invoice. Do not put the full, potentially large form payload in a URL.
            receipt_match = {'invnum': 'eq.' + invnum}
            for field in ('paid', 'paid_date', 'amount_paid', 'stripe_amount'):
                value = saved.get(field)
                text = json.dumps(value) if isinstance(value, (bool, int, float)) else str(value)
                receipt_match['data->>' + field] = 'is.null' if value is None else 'eq.' + text
            payments = saved.get('payments')
            # JSON null is a stored JSON value, unlike SQL NULL (missing key).
            # Preserve the exact state so unchanged legacy invoices can save,
            # while a concurrent payment still prevents the PATCH.
            receipt_match['data->payments'] = ('is.null' if 'payments' not in saved
                else 'eq.' + json.dumps(payments, separators=(',', ':')))
            match = urlencode(receipt_match)
            status2, data2 = supabase_request(
                'PATCH', 'invoices?' + match,
                {'data': body, 'client': client, 'address': address, 'invdate': invdate}
            )
            if status2 == 200 and isinstance(data2, list) and len(data2) == 1:
                self._respond(200, {'saved': True, 'invnum': invnum})
            elif status2 < 300:
                self._respond(409, {'error': 'Invoice receipt status changed while open. Reload it; nothing was overwritten.'})
            else:
                self._respond(502, {'error': 'Invoice save failed.'})
        elif status == 409:
            self._respond(409, {'error': 'Invoice receipt status changed while open. Reload it; nothing was overwritten.'})
        else:
            self._respond(502, {'error': 'Invoice save failed.'})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
