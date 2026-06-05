import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))

            grand   = float(body.get('grand', 0))
            client  = str(body.get('client', 'Client'))
            invnum  = str(body.get('invnum', ''))
            address = str(body.get('address', ''))

            if grand < 1:
                self._respond(400, {'error': 'Invalid amount'})
                return

            secret_key = os.environ.get('STRIPE_SECRET_KEY', '')
            if not secret_key:
                self._respond(500, {'error': 'Stripe key not configured'})
                return

            # Stripe wants amounts in cents
            amount_cents = int(round(grand * 100))

            client_email = str(body.get('client_email', '')).strip()

            # Build the form-encoded body for Stripe's REST API
            # Note: this uses the Payment Links API, NOT checkout sessions
            data = {
                'line_items[0][price_data][currency]': 'usd',
                'line_items[0][price_data][product_data][name]': f'Staging Proposal {invnum}',
                'line_items[0][price_data][product_data][metadata][invoice]': invnum,
                'line_items[0][price_data][unit_amount]': str(amount_cents),
                'line_items[0][quantity]': '1',
                'metadata[invoice]': invnum,
                'metadata[client]': client[:40],
                'metadata[address]': address[:100],
                'metadata[client_email]': client_email[:100],
            }

            # If we have an email, also pre-fill it on the Stripe payment page
            if client_email:
                data['customer_creation'] = 'if_required'

            encoded = urllib.parse.urlencode(data).encode('utf-8')

            req = urllib.request.Request(
                'https://api.stripe.com/v1/payment_links',
                data=encoded,
                method='POST',
            )
            req.add_header('Authorization', f'Bearer {secret_key}')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')

            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())

            url = resp.get('url', '')
            if not url:
                self._respond(500, {'error': 'No URL returned from Stripe'})
                return

            self._respond(200, {'url': url})

        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read())
                msg = err_body.get('error', {}).get('message', str(e))
            except Exception:
                msg = str(e)
            self._respond(500, {'error': f'Stripe API error: {msg}'})

        except Exception as e:
            self._respond(500, {'error': str(e)})

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
