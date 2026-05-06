import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

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
        else:
            # Try upsert if already exists
            status2, data2 = supabase_request(
                'PATCH',
                f'invoices?invnum=eq.{invnum}',
                {'data': body, 'client': client, 'address': address}
            )
            self._respond(200, {'saved': True, 'invnum': invnum})

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
