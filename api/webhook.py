import json
import os
import hashlib
import hmac
import smtplib
import urllib.request
import urllib.parse
import datetime
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler

SMTP_HOST = 'smtp.hostinger.com'
SMTP_PORT = 465
FROM_EMAIL = 'info@stylingwithjas.com'
JASMINE_SMS = '2064225618@vtext.com'  # Verizon email-to-text (free)
SUPABASE_URL = 'https://cwvdfgrjlrpsdfwduwvn.supabase.co'

def send_sms(message):
    """Send text to Jasmine via Verizon email-to-text gateway — free."""
    try:
        password = os.environ.get('EMAIL_PASSWORD', '')
        if not password:
            return False
        msg = MIMEText(message)
        msg['From'] = FROM_EMAIL
        msg['To'] = JASMINE_SMS
        msg['Subject'] = ''
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(FROM_EMAIL, password)
            server.sendmail(FROM_EMAIL, JASMINE_SMS, msg.as_string())
        return True
    except Exception as e:
        print(f'SMS error: {e}')
        return False

def verify_signature(raw_body, sig_header, secret):
    """Verify Stripe webhook signature to ensure request is genuine."""
    try:
        parts = {}
        sigs = []
        for part in sig_header.split(','):
            k, v = part.split('=', 1)
            if k == 'v1':
                sigs.append(v)
            else:
                parts[k] = v
        timestamp = parts.get('t', '')
        signed = f"{timestamp}.{raw_body.decode('utf-8')}"
        expected = hmac.new(
            secret.encode('utf-8'),
            signed.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in sigs)
    except Exception as e:
        print(f'Signature error: {e}')
        return False

def fmt_amount(cents):
    return f'${cents / 100:,.2f}'

def fmt_date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%b %d')

def fmt_arrival(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%b %d, %Y')

def supabase_get_by_amount(amount_cents):
    """Find invoice by amount as fallback when no invoice number in metadata."""
    try:
        key = os.environ.get('SUPABASE_KEY', '')
        # Get recent invoices and match by amount
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?select=invnum,client,data&order=created_at.desc&limit=20',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            invoices = json.loads(r.read())
        # Match by grand total
        amount_dollars = amount_cents / 100
        for inv in invoices:
            data = inv.get('data', {})
            grand = data.get('grand', 0) or 0
            if abs(float(grand) - amount_dollars) < 0.02:
                return inv
        return None
    except:
        return None

def supabase_update_payment(invnum, payment_info):
    """Update invoice record with payment details."""
    try:
        key = os.environ.get('SUPABASE_KEY', '')
        # Get current data first
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}&select=data',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if not rows:
            return False
        current_data = rows[0].get('data', {}) or {}
        current_data.update({
            'paid': True,
            'paid_date': payment_info.get('paid_date', ''),
            'stripe_payment_id': payment_info.get('payment_id', ''),
            'stripe_amount': payment_info.get('amount', 0),
            'payout_arrival': payment_info.get('arrival_date', ''),
        })
        body = json.dumps({'data': current_data}).encode()
        patch_req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}',
            data=body, method='PATCH'
        )
        patch_req.add_header('apikey', key)
        patch_req.add_header('Authorization', f'Bearer {key}')
        patch_req.add_header('Content-Type', 'application/json')
        patch_req.add_header('Prefer', 'return=minimal')
        with urllib.request.urlopen(patch_req, timeout=10) as r:
            return True
    except Exception as e:
        print(f'Supabase update error: {e}')
        return False


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)

        # Verify Stripe signature
        sig_header = self.headers.get('Stripe-Signature', '')
        webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

        if webhook_secret and sig_header:
            if not verify_signature(raw_body, sig_header, webhook_secret):
                self._respond(400, {'error': 'Invalid signature'})
                return

        try:
            event = json.loads(raw_body)
        except:
            self._respond(400, {'error': 'Invalid JSON'})
            return

        event_type = event.get('type', '')
        obj = event.get('data', {}).get('object', {})

        # ── PAYMENT RECEIVED ──
        if event_type in ('payment_intent.succeeded', 'checkout.session.completed'):
            # Normalize fields between the two event shapes
            if event_type == 'checkout.session.completed':
                amount      = obj.get('amount_total', 0)           # cents
                created     = obj.get('created', 0)
                metadata    = obj.get('metadata', {})
                invnum      = metadata.get('invoice', '').strip()
                client      = metadata.get('client', '').strip()
                payment_id  = obj.get('payment_intent', '')
                # If metadata empty, try customer_details name
                if not client:
                    cd = obj.get('customer_details', {})
                    client = cd.get('name', '') or cd.get('email', '')
            else:
                # payment_intent.succeeded (direct API payments)
                amount      = obj.get('amount', 0)
                created     = obj.get('created', 0)
                metadata    = obj.get('metadata', {})
                invnum      = metadata.get('invoice', '').strip()
                client      = metadata.get('client', '').strip()
                payment_id  = obj.get('id', '')

            amount_str = fmt_amount(amount)
            date_str = fmt_date(created)
            paid_date = datetime.datetime.fromtimestamp(created).strftime('%Y-%m-%d')
            if not invnum or not client:
                matched = supabase_get_by_amount(amount)
                if matched:
                    invnum = invnum or matched.get('invnum', '')
                    client = client or matched.get('client', 'Client')

            client_display = client or 'Client'

            # Send SMS notification to Jasmine
            sms = (
                f'SWJ Payment\n'
                f'{client_display} paid {amount_str}\n'
                f'Date: {date_str}\n'
                f'Inv #{invnum}'
            )
            send_sms(sms)

            # Update Supabase
            if invnum:
                supabase_update_payment(invnum, {
                    'paid_date': paid_date,
                    'payment_id': payment_id,
                    'amount': amount / 100,
                    'arrival_date': ''
                })

            self._respond(200, {'received': True, 'type': event_type})

        # ── PAYOUT CREATED (money on its way to bank) ──
        elif event_type == 'payout.created':
            amount = obj.get('amount', 0)
            amount_str = fmt_amount(amount)
            arrival_ts = obj.get('arrival_date', 0)
            arrival_str = fmt_arrival(arrival_ts) if arrival_ts else 'TBD'
            payout_id = obj.get('id', '')

            sms = (
                f'SWJ Deposit Coming\n'
                f'{amount_str} transferring to bank\n'
                f'Est. arrival: {arrival_str}'
            )
            send_sms(sms)
            self._respond(200, {'received': True, 'type': event_type})

        else:
            # Acknowledge all other events
            self._respond(200, {'received': True, 'type': event_type})

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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Stripe-Signature')

    def log_message(self, format, *args):
        pass
