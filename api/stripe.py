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

            secret_key = os.environ.get('STRIPE_SECRET_KEY', '')
            if not secret_key:
                self._respond(500, {'error': 'Stripe key not configured'})
                return

            # Split/partial-payment links need to be retired when a newer one supersedes
            # them (so a client can't pay a stale full-amount link after a deposit was set
            # up). Same file/function as link creation to stay within the Hobby 12-function cap.
            if body.get('action') == 'deactivate':
                link_id = str(body.get('link_id', '')).strip()
                if not link_id:
                    self._respond(400, {'error': 'Missing link_id'})
                    return
                try:
                    req = urllib.request.Request(
                        f'https://api.stripe.com/v1/payment_links/{urllib.parse.quote(link_id)}',
                        data=urllib.parse.urlencode({'active': 'false'}).encode('utf-8'),
                        method='POST',
                    )
                    req.add_header('Authorization', f'Bearer {secret_key}')
                    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
                    with urllib.request.urlopen(req, timeout=15):
                        pass
                    self._respond(200, {'ok': True})
                except Exception as e:
                    # Non-fatal from the caller's perspective — the new link still works even
                    # if the old one couldn't be deactivated (e.g. already inactive).
                    self._respond(200, {'ok': False, 'error': str(e)})
                return

            grand   = float(body.get('grand', 0))
            client  = str(body.get('client', 'Client'))
            invnum  = str(body.get('invnum', ''))
            address = str(body.get('address', ''))
            label   = str(body.get('label', '')).strip()

            if grand < 1:
                self._respond(400, {'error': 'Invalid amount'})
                return

            # Stripe wants amounts in cents
            amount_cents = int(round(grand * 100))

            client_email = str(body.get('client_email', '')).strip()

            product_name = f'Staging Proposal {invnum}' + (f' — {label}' if label else '')

            # Build the form-encoded body for Stripe's REST API
            # Note: this uses the Payment Links API, NOT checkout sessions
            data = {
                'line_items[0][price_data][currency]': 'usd',
                'line_items[0][price_data][product_data][name]': product_name,
                'line_items[0][price_data][product_data][metadata][invoice]': invnum,
                'line_items[0][price_data][unit_amount]': str(amount_cents),
                'line_items[0][quantity]': '1',
                'metadata[invoice]': invnum,
                'metadata[client]': client[:40],
                'metadata[address]': address[:100],
                'metadata[client_email]': client_email[:100],
                'metadata[label]': label[:60],
                # Stamp the SAME metadata onto the PaymentIntent. Payment Link metadata
                # does NOT propagate to the PaymentIntent, and the webhook listens to
                # payment_intent.succeeded — so without this the webhook sees no invoice
                # number or client email ("not matched" / "no email on file").
                'payment_intent_data[metadata][invoice]': invnum,
                'payment_intent_data[metadata][client]': client[:40],
                'payment_intent_data[metadata][address]': address[:100],
                'payment_intent_data[metadata][client_email]': client_email[:100],
                'payment_intent_data[metadata][label]': label[:60],
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

            self._respond(200, {'url': url, 'id': resp.get('id', '')})

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
