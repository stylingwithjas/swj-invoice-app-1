import json
import os
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

def stripe_post(endpoint, params, key):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f'https://api.stripe.com/v1/{endpoint}',
        data=data,
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._respond(400, {'error': f'Invalid request: {e}'})
            return

        key = os.environ.get('STRIPE_SECRET_KEY', '')
        if not key:
            self._respond(500, {'error': 'Stripe key not configured on server'})
            return

        grand   = body.get('grand', 0)
        client  = body.get('client', '')
        invnum  = body.get('invnum', '')
        address = body.get('address', '')

        # Create price
        price = stripe_post('prices', {
            'unit_amount': str(int(round(grand * 100))),
            'currency': 'usd',
            'product_data[name]': f'Home Staging — {address}',
            'product_data[metadata][invoice]': invnum,
            'product_data[metadata][client]': client,
        }, key)

        if 'error' in price:
            self._respond(400, {'error': price['error']['message']})
            return

        # Create payment link
        link = stripe_post('payment_links', {
            'line_items[0][price]': price['id'],
            'line_items[0][quantity]': '1',
            'metadata[invoice]': invnum,
            'metadata[client]': client,
            'after_completion[type]': 'hosted_confirmation',
            f'after_completion[hosted_confirmation][custom_message]': f'Thank you {client}! Your staging is confirmed. — Styling With Jas',
        }, key)

        if 'error' in link:
            self._respond(400, {'error': link['error']['message']})
            return

        self._respond(200, {'url': link['url']})

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
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
