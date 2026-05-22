import json
import os
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

STRIPE_API = 'https://api.stripe.com/v1'


def stripe_request(method, path, params=None):
    """Make a Stripe API request using urllib (no extra dependencies)."""
    key = os.environ.get('STRIPE_SECRET_KEY', '')
    if not key:
        raise Exception('STRIPE_SECRET_KEY not configured')

    url = f'{STRIPE_API}/{path}'
    data = urllib.parse.urlencode(params).encode() if params else None

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    req.add_header('Stripe-Version', '2023-10-16')

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        error_body = json.loads(e.read())
        raise Exception(error_body.get('error', {}).get('message', f'Stripe error {e.code}'))


def create_payment_link(grand, client, invnum, address):
    """
    Create a Stripe Payment Link for a SWJ invoice.
    Returns the payment link URL.
    """
    amount_cents = round(float(grand) * 100)

    # Step 1: Create a product for this invoice
    product = stripe_request('POST', 'products', {
        'name': f'Home Staging — {client}',
        'description': f'Staging services for {address}. Invoice #{invnum}.',
    })

    # Step 2: Create a price for the product
    price = stripe_request('POST', 'prices', {
        'product': product['id'],
        'unit_amount': amount_cents,
        'currency': 'usd',
    })

    # Step 3: Create the payment link
    payment_link = stripe_request('POST', 'payment_links', {
        'line_items[0][price]': price['id'],
        'line_items[0][quantity]': '1',
        'metadata[invoice]': invnum,
        'metadata[client]': client,
        'metadata[address]': address[:200],  # Stripe metadata has char limits
        'after_completion[type]': 'hosted_confirmation',
        'after_completion[hosted_confirmation][custom_message]': (
            f'Thank you, {client.split()[0]}! Your payment has been received. '
            f'Jasmine will be in touch to confirm your staging date.'
        ),
    })

    return payment_link['url'], payment_link['id']


def deactivate_payment_link(payment_link_id):
    """Deactivate an existing Stripe Payment Link so it can no longer be used."""
    result = stripe_request('POST', f'payment_links/{payment_link_id}', {
        'active': 'false'
    })
    return result.get('active', True) is False


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

        # ── DEACTIVATE action ──────────────────────────────────────
        # Called when invoice total changes and old link needs to be killed
        if body.get('action') == 'deactivate':
            payment_link_id = body.get('payment_link_id', '').strip()
            if not payment_link_id:
                self._respond(400, {'error': 'No payment_link_id provided'})
                return
            try:
                deactivated = deactivate_payment_link(payment_link_id)
                self._respond(200, {
                    'deactivated': deactivated,
                    'id': payment_link_id
                })
            except Exception as e:
                # Non-fatal — log but return 200 so invoice save isn't blocked
                print(f'[stripe] Deactivate error (non-fatal): {e}')
                self._respond(200, {
                    'deactivated': False,
                    'error': str(e)
                })
            return

        # ── CREATE payment link ────────────────────────────────────
        grand = body.get('grand')
        client = body.get('client', '').strip()
        invnum = body.get('invnum', '').strip()
        address = body.get('address', '').strip()

        if not grand:
            self._respond(400, {'error': 'Missing grand total'})
            return
        if not client:
            self._respond(400, {'error': 'Missing client name'})
            return
        if not invnum:
            self._respond(400, {'error': 'Missing invoice number'})
            return

        try:
            url, link_id = create_payment_link(grand, client, invnum, address)
            self._respond(200, {
                'url': url,
                'payment_link_id': link_id,
                'amount': grand,
            })
        except Exception as e:
            print(f'[stripe] Create error: {e}')
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
