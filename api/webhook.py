"""
SWJ Stripe Webhook
==================
Fires on Stripe events. When a client pays:
  1. Generates a beautiful "Payment Receipt" PDF
  2. Emails the client a thank-you with the receipt PDF attached
  3. Emails Jasmine a notification (CC'd on the client email too)
  4. Updates Supabase to mark the invoice as paid

When Stripe creates a payout:
  - Emails Jasmine the bank deposit notification

Required env vars (all already set in Vercel):
  STRIPE_WEBHOOK_SECRET — from Stripe dashboard
  EMAIL_PASSWORD        — Hostinger SMTP password
  SUPABASE_KEY          — service role key
"""

import json
import os
import re
import hashlib
import hmac
import smtplib
import urllib.request
import urllib.parse
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from http.server import BaseHTTPRequestHandler

# Import the receipt builder from the sibling module
try:
    from generate_receipt import build_receipt_pdf
except ImportError:
    # Vercel serverless sometimes can't cross-import — inline fallback
    build_receipt_pdf = None

SMTP_HOST = 'smtp.hostinger.com'
SMTP_PORT = 465
FROM_EMAIL = 'info@stylingwithjas.com'
JASMINE_EMAIL = 'info@stylingwithjas.com'

SUPABASE_URL = 'https://cwvdfgrjlrpsdfwduwvn.supabase.co'


# ════════════════════════════════════════════════════════════════
# EMAIL SENDING
# ════════════════════════════════════════════════════════════════

def send_email(to_email, subject, body_text, html_body=None, pdf_attachment=None, pdf_filename=None, cc=None):
    """Send email via Hostinger SMTP with optional PDF attachment."""
    try:
        password = os.environ.get('EMAIL_PASSWORD', '')
        if not password:
            print('EMAIL_PASSWORD not set')
            return False

        msg = MIMEMultipart('mixed')
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        if cc:
            msg['Cc'] = cc
        msg['Subject'] = subject

        # Plain + HTML body
        body = MIMEMultipart('alternative')
        body.attach(MIMEText(body_text, 'plain'))
        if html_body:
            body.attach(MIMEText(html_body, 'html'))
        msg.attach(body)

        # Optional PDF attachment
        if pdf_attachment and pdf_filename:
            att = MIMEApplication(pdf_attachment, _subtype='pdf')
            att.add_header('Content-Disposition', 'attachment', filename=pdf_filename)
            msg.attach(att)

        recipients = [to_email]
        if cc:
            recipients.append(cc)

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.login(FROM_EMAIL, password)
            server.sendmail(FROM_EMAIL, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f'Email error: {e}')
        return False


def notification_html(body_text):
    """SWJ-branded notification email HTML."""
    return f"""
    <html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;background:#f5f2ed;padding:24px;color:#1a1814;margin:0">
      <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:14px;padding:24px;border:1px solid #e5e0d8">
        <div style="font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;letter-spacing:3px;color:#1a1814;margin-bottom:4px">SWJ</div>
        <div style="font-size:10px;letter-spacing:2px;color:#a29c96;text-transform:uppercase;margin-bottom:18px">Styling With Jas</div>
        <div style="font-size:15px;line-height:1.55;color:#3d3830;white-space:pre-line">{body_text}</div>
        <div style="margin-top:22px;padding-top:14px;border-top:1px solid #ede8e0;font-size:11px;color:#a29c96">
          Sent automatically when a Stripe event fires.
        </div>
      </div>
    </body></html>
    """


def client_thankyou_html(first_name, amount_str, installation_date, address):
    """Warm, luxury-feel thank-you HTML for the client."""
    inst_line = f'<p style="margin:0 0 16px 0;color:#3d3830">Your staging is confirmed for <strong style="color:#1a1814">{installation_date}</strong>. I\'ll be in touch as we get closer to coordinate final details.</p>' if installation_date else ''
    addr_line = f'<p style="margin:0 0 16px 0;color:#a29c96;font-size:13px">{address}</p>' if address else ''
    return f"""
    <html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;background:#f5f2ed;padding:32px 16px;color:#1a1814;margin:0">
      <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;border:1px solid #e5e0d8">
        <div style="background:#1a1814;padding:28px 24px;text-align:center">
          <div style="font-family:'Cormorant Garamond',Georgia,serif;font-size:26px;letter-spacing:5px;color:#fff">SWJ</div>
          <div style="font-size:10px;letter-spacing:2.5px;color:#a29c96;text-transform:uppercase;margin-top:4px">Styling With Jas</div>
        </div>
        <div style="padding:32px 28px">
          <div style="font-family:Georgia,serif;font-size:28px;color:#1a1814;margin-bottom:6px">Thank you, {first_name}.</div>
          <div style="font-family:Georgia,serif;font-style:italic;font-size:14px;color:#7a6f69;margin-bottom:20px">Your payment of {amount_str} has been received.</div>
          {addr_line}
          {inst_line}
          <p style="margin:0 0 16px 0;color:#3d3830">Your detailed receipt is attached for your records.</p>
          <p style="margin:0;color:#3d3830;font-style:italic">It is our privilege to prepare your home for market.</p>
          <div style="margin-top:24px;padding-top:18px;border-top:1px solid #ede8e0;font-size:13px;color:#3d3830;font-style:italic">
            Jasmine Santana<br>
            <span style="font-size:10px;font-style:normal;letter-spacing:1.5px;color:#a29c96;text-transform:uppercase">Owner &amp; Creative Director</span>
          </div>
        </div>
        <div style="background:#1a1814;padding:14px;text-align:center;color:#7a6f69;font-size:11px">
          206-422-5618  ·  info@stylingwithjas.com<br>
          Lux Ventures LLC  ·  © 2026 Styling With Jas
        </div>
      </div>
    </body></html>
    """


# ════════════════════════════════════════════════════════════════
# STRIPE SIGNATURE VERIFY
# ════════════════════════════════════════════════════════════════

def verify_signature(raw_body, sig_header, secret):
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


# ════════════════════════════════════════════════════════════════
# FORMATTERS
# ════════════════════════════════════════════════════════════════

def fmt_amount(cents):
    return f'${cents / 100:,.2f}'

def fmt_date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%B %d, %Y')

def fmt_time(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%I:%M %p').lstrip('0')

def fmt_arrival(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%B %d, %Y')


# ════════════════════════════════════════════════════════════════
# SUPABASE
# ════════════════════════════════════════════════════════════════

def supabase_get_invoice(invnum):
    """Get a single invoice by invnum."""
    try:
        key = os.environ.get('SUPABASE_KEY', '')
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}&select=*',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        return rows[0] if rows else None
    except Exception:
        return None


def supabase_get_by_amount(amount_cents):
    """Fallback lookup by grand total when metadata is missing."""
    try:
        key = os.environ.get('SUPABASE_KEY', '')
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?select=invnum,client,address,data&order=created_at.desc&limit=20',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            invoices = json.loads(r.read())
        amount_dollars = amount_cents / 100
        for inv in invoices:
            data = inv.get('data', {})
            grand = data.get('grand', 0) or 0
            if abs(float(grand) - amount_dollars) < 0.02:
                return inv
        return None
    except Exception:
        return None


# Matches the synthetic invoice numbers split-payment links are created with, e.g. "202092-PAY1".
# Deliberately distinct from the existing "-EXT<n>" extension-payment scheme so extension
# handling (unchanged) can never collide with this.
SPLIT_PAY_RE = re.compile(r'^(.+)-PAY(\d+)$')


def compute_grand(data):
    """Same Subtotal/Tax/Total-with-overrides formula used by api/generate.py and the
    generators, so 'fully paid' is decided against the exact number on the invoice."""
    base_price = float(data.get('base_price', 2500) or 2500)
    addon_total = sum(float(a.get('price', 0) or 0) for a in (data.get('addons') or []))
    sub_ov = data.get('subtotal_override')
    tax_ov = data.get('tax_override')
    tot_ov = data.get('total_override')
    taxrate = float(data.get('taxrate', 0) or 0)
    subtotal = float(sub_ov) if sub_ov not in (None, '') else base_price + addon_total
    tax = float(tax_ov) if tax_ov not in (None, '') else subtotal * taxrate / 100
    return float(tot_ov) if tot_ov not in (None, '') else subtotal + tax


def _patch_invoice_data(invnum, data):
    key = os.environ.get('SUPABASE_KEY', '')
    body = json.dumps({'data': data}).encode()
    patch_req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}',
        data=body, method='PATCH'
    )
    patch_req.add_header('apikey', key)
    patch_req.add_header('Authorization', f'Bearer {key}')
    patch_req.add_header('Content-Type', 'application/json')
    patch_req.add_header('Prefer', 'return=minimal')
    with urllib.request.urlopen(patch_req, timeout=10):
        return True


# ════════════════════════════════════════════════════════════════
# DAILY DESTAGE REMINDERS (Vercel Cron — see vercel.json "crons")
# ════════════════════════════════════════════════════════════════
# Two fixed checkpoints, both predetermined off the already-known destage_date:
#   15 days out — heads-up to Jasmine, mainly a staffing check (mover assigned yet?)
#   7 days out  — heads-up to Jasmine with a ready-to-review mailto: link she can tap to
#                 send the client a predetermined reminder — nothing emails the client
#                 automatically, she decides.
# reminder_15_sent / reminder_7_sent flags on the invoice make each checkpoint fire once,
# and use <= rather than == so a missed cron run still catches up the next day.

def supabase_list_invoices_with_destage():
    """All invoices that have a destage date set, for the daily reminder cron. Fetches a
    generous page and filters in Python rather than PostgREST JSON-path date comparisons,
    which are easy to get subtly wrong against a text-stored ISO date."""
    try:
        key = os.environ.get('SUPABASE_KEY', '')
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?select=invnum,client,address,data&order=created_at.desc&limit=1000',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            invoices = json.loads(r.read())
        return [inv for inv in invoices if (inv.get('data') or {}).get('destage_date')]
    except Exception as e:
        print(f'[reminders] list error: {e}')
        return []


def client_reminder_mailto(client_email, client_first_name, destage_date_fmt):
    """A pre-written client reminder, opened in Jasmine's own mail app for her to review
    and send herself — matches the sms: link pattern already used for movers/photographer."""
    subject = urllib.parse.quote('Your Styling With Jas Rental — Ending Soon')
    body = urllib.parse.quote(
        f"Hi {client_first_name},\n\n"
        f"Just a friendly reminder that your staging rental period wraps up on {destage_date_fmt}. "
        f"If you'd like to extend, just let us know — otherwise we'll proceed with destage as scheduled.\n\n"
        f"Thank you!\n"
        f"Jasmine Santana\nStyling With Jas\n206-422-5618"
    )
    return f'mailto:{client_email}?subject={subject}&body={body}'


def run_destage_reminders():
    today = datetime.date.today()
    sent = []
    for inv in supabase_list_invoices_with_destage():
        d = inv.get('data') or {}
        stage = d.get('stage', 'proposal')
        if stage in ('cancelled', 'voided') or d.get('voided'):
            continue
        try:
            destage_date = datetime.datetime.strptime(d['destage_date'], '%Y-%m-%d').date()
        except Exception:
            continue
        days_until = (destage_date - today).days
        if days_until < 0 or days_until > 15:
            continue  # already past, or too far out to matter yet

        invnum = inv.get('invnum', '')
        client = inv.get('client', '') or d.get('client', '')
        address = inv.get('address', '') or d.get('address', '')
        address_short = address.split(',')[0] if address else ''
        destage_fmt = destage_date.strftime('%A, %B %d')
        mover = d.get('destage_mover', '')
        updates = {}

        if days_until <= 15 and not d.get('reminder_15_sent'):
            mover_line = f'Destage mover: {mover}' if mover else 'No destage mover assigned yet.'
            # Auto-advance the board card from Staged into Destage Scheduled — she shouldn't
            # have to remember to drag it over herself once the countdown starts. Only moves
            # it from the expected predecessor stage; leaves anything already further along
            # (or manually held back) alone.
            moved = False
            if stage == 'staged':
                updates['stage'] = 'destage'
                moved = True
            body = (
                f'Destage for {client} at {address} is coming up on {destage_fmt} ({days_until} days away).\n\n'
                f'{mover_line}\n\n'
                + ('Moved this card to Destage Scheduled on the board.\n\n' if moved else '')
                + f'Invoice #{invnum}.'
            )
            send_email(JASMINE_EMAIL, f'Destage in {days_until}d — {client} ({address_short})',
                       body, html_body=notification_html(body))
            updates['reminder_15_sent'] = True
            sent.append(f'{invnum}:15d')

        if days_until <= 7 and not d.get('reminder_7_sent'):
            client_email = d.get('client_email', '')
            client_first = client.split()[0] if client else 'there'
            if client_email:
                link = client_reminder_mailto(client_email, client_first, destage_fmt)
                body = (
                    f'Destage for {client} at {address} is in {days_until} days ({destage_fmt}).\n\n'
                    f'Want to remind {client_first} their rental is wrapping up? Tap below to open a '
                    f'pre-written email to them — nothing sends automatically, review it first:\n\n'
                    f'{link}\n\n'
                    f'Invoice #{invnum}.'
                )
            else:
                body = (
                    f'Destage for {client} at {address} is in {days_until} days ({destage_fmt}), '
                    f'but there\'s no client email on file to send them a reminder.\n\n'
                    f'Invoice #{invnum}.'
                )
            send_email(JASMINE_EMAIL, f'Destage in {days_until}d — remind {client}? ({address_short})',
                       body, html_body=notification_html(body))
            updates['reminder_7_sent'] = True
            sent.append(f'{invnum}:7d')

        if updates:
            try:
                _patch_invoice_data(invnum, {**d, **updates})
            except Exception as e:
                print(f'[reminders] patch failed for {invnum}: {e}')

    return sent


def supabase_update_split_payment(parent_invnum, pay_id, payment_info):
    """A split/partial payment link (id like '202092-PAY1') got paid. Update the matching
    entry in the PARENT invoice's data.payments[] and recompute the running amount_paid —
    rather than looking for a Supabase row that doesn't exist (that's the gap the older
    extension-payment flow has: it never finds a row for '<invnum>-EXT1' and silently no-ops)."""
    try:
        key = os.environ.get('SUPABASE_KEY', '')
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(parent_invnum)}&select=data',
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if not rows:
            return False
        current_data = rows[0].get('data', {}) or {}
        payments = current_data.get('payments') or []

        entry = next((p for p in payments if p.get('id') == pay_id), None)
        if entry is None:
            print(f'Split payment {pay_id} not found on {parent_invnum}')
            return False

        # Idempotent against Stripe webhook redelivery: if this exact payment already
        # recorded this payment_id as paid, don't re-apply.
        if entry.get('status') == 'paid' and entry.get('payment_id') == payment_info.get('payment_id'):
            return True

        entry['status'] = 'paid'
        entry['payment_id'] = payment_info.get('payment_id', '')
        entry['paid_date'] = payment_info.get('paid_date', '')
        entry['amount'] = payment_info.get('amount', entry.get('amount', 0))  # trust Stripe's actual charge

        # Running total: legacy single full-payment amount (if that path was ALSO used) + every
        # paid split entry. The two never overlap — splits live only in payments[], never in
        # the legacy stripe_amount/paid fields — so this can't double-count.
        legacy_amount = float(current_data.get('stripe_amount', 0) or 0) if current_data.get('paid') else 0
        splits_amount = sum(float(p.get('amount', 0) or 0) for p in payments if p.get('status') == 'paid')
        amount_paid = legacy_amount + splits_amount
        grand = compute_grand(current_data)

        current_data['payments'] = payments
        current_data['amount_paid'] = amount_paid
        current_data['paid'] = amount_paid >= grand - 0.01
        if current_data['paid'] and not current_data.get('paid_date'):
            current_data['paid_date'] = entry['paid_date']
        if payment_info.get('client_email'):
            current_data['client_email'] = payment_info['client_email']

        # This link just cleared — stop pointing the client's Approve/Sign page at it.
        if current_data.get('active_client_payment_id') == pay_id:
            current_data['active_client_payment_id'] = None

        return _patch_invoice_data(parent_invnum, current_data)
    except Exception as e:
        print(f'Split payment update error: {e}')
        return False


def supabase_update_payment(invnum, payment_info):
    # Route split-payment links (metadata.invoice like "202092-PAY1") to the parent
    # invoice's payments[] array instead of looking for a row that doesn't exist.
    m = SPLIT_PAY_RE.match(invnum)
    if m:
        return supabase_update_split_payment(m.group(1), 'PAY' + m.group(2), payment_info)

    try:
        key = os.environ.get('SUPABASE_KEY', '')
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
            'amount_paid': payment_info.get('amount', 0),
            'payout_arrival': payment_info.get('arrival_date', ''),
            'client_email': payment_info.get('client_email', ''),   # NEW
        })
        return _patch_invoice_data(invnum, current_data)
    except Exception as e:
        print(f'Supabase update error: {e}')
        return False


# ════════════════════════════════════════════════════════════════
# RECEIPT PDF (inline fallback if cross-import fails on Vercel)
# ════════════════════════════════════════════════════════════════

def _inline_build_receipt(data):
    """Inline copy of build_receipt_pdf — keeps webhook self-contained."""
    import io as _io
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors

    client = data.get('client', 'Client')
    address = data.get('address', '')
    invnum = data.get('invnum', '')
    amount = float(data.get('amount', 0))
    payment_date = data.get('payment_date', '')
    payment_time = data.get('payment_time', '')
    payment_method = data.get('payment_method', 'Credit Card')
    stripe_ref = data.get('stripe_ref', '')
    installation_date = data.get('installation_date', '')

    buf = _io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    PW, PH = letter
    ML, MR = 50, 50
    CW = PW - ML - MR

    INK = colors.HexColor('#1a1814')
    WARM = colors.HexColor('#faf8f5')
    TAUPE = colors.HexColor('#a29c96')
    TAUPE_DK = colors.HexColor('#7a6f69')
    GOLD = colors.HexColor('#c9a96e')
    GOLD_DK = colors.HexColor('#9d7a44')
    GREEN = colors.HexColor('#4a7c50')
    GREEN_LT = colors.HexColor('#edf4ed')
    GREEN_DK = colors.HexColor('#2d6a0a')
    INK_SOFT = colors.HexColor('#3d3830')

    c.setFillColor(INK)
    c.rect(0, PH - 110, PW, 110, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont('Times-Roman', 32)
    c.drawCentredString(PW/2, PH - 60, 'SWJ')
    c.setFont('Helvetica', 7.5)
    c.setFillColor(TAUPE)
    sub = 'STYLING WITH JAS'
    sx = PW/2 - (c.stringWidth(sub, 'Helvetica', 7.5) + 2.4*(len(sub)-1))/2
    for ch in sub:
        c.drawString(sx, PH - 78, ch)
        sx += c.stringWidth(ch, 'Helvetica', 7.5) + 2.4

    y = PH - 175
    c.setFillColor(TAUPE); c.setFont('Helvetica', 9)
    tt = 'PAYMENT RECEIPT'; sp = 3.5
    tw = c.stringWidth(tt, 'Helvetica', 9) + sp*(len(tt)-1)
    sx = PW/2 - tw/2
    for ch in tt:
        c.drawString(sx, y, ch); sx += c.stringWidth(ch, 'Helvetica', 9) + sp
    y -= 14
    c.setStrokeColor(GOLD); c.setLineWidth(0.8)
    c.line(PW/2 - 28, y, PW/2 + 28, y)

    y -= 46
    first_name = client.split()[0] if client else 'there'
    if first_name.lower() == 'estate':
        parts = client.split()
        if len(parts) > 2: first_name = parts[2]
    c.setFillColor(INK); c.setFont('Times-Roman', 30)
    c.drawCentredString(PW/2, y, f'Thank you, {first_name}.')
    y -= 22
    c.setFont('Times-Italic', 14); c.setFillColor(TAUPE_DK)
    c.drawCentredString(PW/2, y, 'Your payment has been received.')

    y -= 54
    card_h = 100
    c.setFillColor(GREEN_LT)
    c.roundRect(ML+40, y-card_h, CW-80, card_h, 14, stroke=0, fill=1)
    c.setFillColor(GREEN); c.setFont('Helvetica', 9)
    al = 'AMOUNT PAID'; sp = 2.5
    tw = c.stringWidth(al, 'Helvetica', 9) + sp*(len(al)-1)
    sx = PW/2 - tw/2
    for ch in al:
        c.drawString(sx, y-28, ch); sx += c.stringWidth(ch, 'Helvetica', 9) + sp
    c.setFillColor(GREEN_DK); c.setFont('Times-Roman', 46)
    c.drawCentredString(PW/2, y-68, f'${amount:,.2f}')
    rl = ''
    if payment_date and payment_time: rl = f'Received {payment_date} at {payment_time}'
    elif payment_date: rl = f'Received {payment_date}'
    if rl:
        c.setFillColor(GREEN); c.setFont('Helvetica', 9)
        c.drawCentredString(PW/2, y-86, rl)
    y -= card_h + 32

    def row(label, value, y_pos):
        if not value: return False
        c.setFillColor(TAUPE); c.setFont('Helvetica', 8)
        sp = 1.5; sxl = ML+6
        for ch in label.upper():
            c.drawString(sxl, y_pos, ch); sxl += c.stringWidth(ch, 'Helvetica', 8) + sp
        c.setFillColor(INK); c.setFont('Helvetica', 11)
        c.drawRightString(PW-MR-6, y_pos, str(value))
        c.setStrokeColor(colors.HexColor('#e5e0d8')); c.setLineWidth(0.4)
        c.line(ML, y_pos-9, PW-MR, y_pos-9)
        return True

    for lbl, val in [('Client', client), ('Property', address), ('Invoice', f'#{invnum}' if invnum else ''), ('Payment Method', payment_method), ('Reference', stripe_ref)]:
        if row(lbl, val, y): y -= 20
    y -= 6

    if installation_date:
        bh = 82
        c.setFillColor(WARM)
        c.setStrokeColor(colors.HexColor('#e5e0d8')); c.setLineWidth(0.8)
        c.roundRect(ML, y-bh, CW, bh, 12, stroke=1, fill=1)
        c.setFillColor(GOLD_DK); c.setFont('Helvetica-Bold', 9)
        nxt = 'WHAT HAPPENS NEXT'; sp = 2.2; sxl = ML+18
        for ch in nxt:
            c.drawString(sxl, y-20, ch); sxl += c.stringWidth(ch, 'Helvetica-Bold', 9) + sp
        c.setFillColor(INK_SOFT); c.setFont('Times-Roman', 11.5)
        c.drawString(ML+18, y-40, f'Your staging is confirmed for {installation_date}.')
        c.setFont('Times-Italic', 10.5); c.setFillColor(TAUPE_DK)
        c.drawString(ML+18, y-55, 'I\'ll be in touch as we get closer to coordinate final details,')
        c.drawString(ML+18, y-68, 'access, and any last-minute design preferences.')
        y -= bh + 26
    else:
        y -= 6

    c.setFillColor(INK); c.setFont('Times-Italic', 12)
    c.drawCentredString(PW/2, y, 'With gratitude,')
    y -= 30
    # Use Parisienne if available, else fallback
    _sig_font = 'Times-BoldItalic'
    _sig_size = 22
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import os as _os
        _fp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'Parisienne-Regular.ttf')
        if _os.path.exists(_fp):
            if 'Parisienne' not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont('Parisienne', _fp))
            _sig_font = 'Parisienne'
            _sig_size = 22
    except Exception:
        pass
    c.setFont(_sig_font, _sig_size); c.setFillColor(INK)
    c.drawCentredString(PW/2, y, 'Jasmine Santana')
    y -= 18
    c.setFont('Helvetica', 8); c.setFillColor(TAUPE)
    role = 'OWNER & CREATIVE DIRECTOR'; sp = 2
    sxl = PW/2 - (c.stringWidth(role, 'Helvetica', 8) + sp*(len(role)-1))/2
    for ch in role:
        c.drawString(sxl, y, ch); sxl += c.stringWidth(ch, 'Helvetica', 8) + sp

    c.setFillColor(INK); c.rect(0, 0, PW, 58, stroke=0, fill=1)
    c.setFillColor(colors.white); c.setFont('Times-Roman', 14)
    c.drawCentredString(PW/2, 36, 'SWJ')
    c.setFillColor(TAUPE_DK); c.setFont('Helvetica', 8)
    c.drawCentredString(PW/2, 21, 'Jasmine Santana  \u00b7  206-422-5618  \u00b7  info@stylingwithjas.com')
    c.drawCentredString(PW/2, 10, 'Lux Ventures LLC  \u00b7  \u00a9 2026 Styling With Jas')

    c.save()
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════
# CLIENT EMAIL LOOKUP (Stripe charge has the customer email)
# ════════════════════════════════════════════════════════════════

def get_client_email_from_stripe(payment_intent_id):
    """Fetch the receipt_email or customer email from the Stripe charge."""
    try:
        key = os.environ.get('STRIPE_SECRET_KEY', '')
        if not key or not payment_intent_id:
            return ''
        req = urllib.request.Request(
            f'https://api.stripe.com/v1/payment_intents/{payment_intent_id}?expand[]=latest_charge',
            headers={'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            pi = json.loads(r.read())
        # Try multiple places where the email could live
        charge = pi.get('latest_charge') or {}
        if isinstance(charge, dict):
            email = charge.get('billing_details', {}).get('email') or charge.get('receipt_email')
            if email:
                return email
        return pi.get('receipt_email', '') or ''
    except Exception as e:
        print(f'Stripe lookup error: {e}')
        return ''


# ════════════════════════════════════════════════════════════════
# WEBHOOK HANDLER
# ════════════════════════════════════════════════════════════════

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Vercel Cron Jobs hit this via GET once daily (see vercel.json). Guarded by
        # CRON_SECRET if it's set — Vercel auto-sends it as a Bearer token when the env var
        # exists. Worst case without it is Jasmine getting an extra reminder email, not data
        # loss, so this doesn't hard-fail while the secret isn't configured yet.
        secret = os.environ.get('CRON_SECRET', '')
        if secret and self.headers.get('Authorization', '') != f'Bearer {secret}':
            self._respond(401, {'error': 'Unauthorized'})
            return
        try:
            sent = run_destage_reminders()
            self._respond(200, {'ok': True, 'reminders_sent': sent})
        except Exception as e:
            self._respond(500, {'error': str(e)})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length)

        sig_header = self.headers.get('Stripe-Signature', '')
        webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

        if webhook_secret and sig_header:
            if not verify_signature(raw_body, sig_header, webhook_secret):
                self._respond(400, {'error': 'Invalid signature'})
                return

        try:
            event = json.loads(raw_body)
        except Exception:
            self._respond(400, {'error': 'Invalid JSON'})
            return

        event_type = event.get('type', '')
        obj = event.get('data', {}).get('object', {})

        # ── PAYMENT RECEIVED ──
        if event_type == 'payment_intent.succeeded':
            amount = obj.get('amount', 0)
            amount_str = fmt_amount(amount)
            created = obj.get('created', 0)
            payment_date = fmt_date(created)
            payment_time = fmt_time(created)
            paid_date_iso = datetime.datetime.fromtimestamp(created).strftime('%Y-%m-%d')

            metadata = obj.get('metadata', {})
            invnum = metadata.get('invoice', '').strip()
            client = metadata.get('client', '').strip()
            address = metadata.get('address', '').strip()
            payment_id = obj.get('id', '')

            # Payment method (last 4 digits)
            payment_method = 'Credit Card'
            charges = obj.get('charges', {}).get('data', [])
            if charges:
                card = charges[0].get('payment_method_details', {}).get('card', {})
                brand = (card.get('brand') or 'Card').title()
                last4 = card.get('last4', '')
                if last4:
                    payment_method = f'{brand} ending in {last4}'

            # Pull more data from Supabase if we have the invoice number
            installation_date = ''
            inv_record = None
            if invnum:
                inv_record = supabase_get_invoice(invnum)
                if inv_record:
                    if not client: client = inv_record.get('client', '')
                    if not address: address = inv_record.get('address', '')
                    inv_data = inv_record.get('data', {}) or {}
                    startdate = inv_data.get('startdate', '')
                    if startdate:
                        try:
                            dt = datetime.datetime.strptime(startdate, '%Y-%m-%d')
                            installation_date = dt.strftime('%B %d, %Y')
                        except Exception:
                            installation_date = startdate

            # Fallback: amount-based lookup
            if not invnum or not client:
                matched = supabase_get_by_amount(amount)
                if matched:
                    invnum = invnum or matched.get('invnum', '')
                    client = client or matched.get('client', 'Client')
                    address = address or matched.get('address', '')

            client_display = client or 'Client'

            # Generate receipt PDF
            receipt_data = {
                'client': client_display,
                'address': address,
                'invnum': invnum,
                'amount': amount / 100,
                'payment_date': payment_date,
                'payment_time': payment_time,
                'payment_method': payment_method,
                'stripe_ref': payment_id,
                'installation_date': installation_date,
            }

            try:
                pdf_bytes = build_receipt_pdf(receipt_data) if build_receipt_pdf else _inline_build_receipt(receipt_data)
            except Exception as e:
                print(f'Receipt PDF error: {e}')
                pdf_bytes = None

            # Get client email — prefer metadata (reliable), fall back to Stripe charge (unreliable)
            client_email = metadata.get('client_email', '').strip()
            if not client_email:
                client_email = get_client_email_from_stripe(payment_id)

            # Build client thank-you email (only if we have their email)
            first_name = client_display.split()[0] if client_display else 'there'
            if first_name.lower() == 'estate':
                parts = client_display.split()
                if len(parts) > 2: first_name = parts[2]

            pdf_filename = f'SWJ_Receipt_{invnum or "payment"}.pdf'

            # ── 1) CLIENT THANK-YOU EMAIL (only if we have their email) ──
            if client_email and pdf_bytes:
                send_email(
                    to_email=client_email,
                    subject=f'Thank you, {first_name} — your payment has been received',
                    body_text=(
                        f'Dear {first_name},\n\n'
                        f'Thank you. Your payment of {amount_str} has been received and your staging is confirmed'
                        + (f' for {installation_date}' if installation_date else '')
                        + '.\n\n'
                        f'Your detailed receipt is attached for your records. I\'ll be in touch as we get closer '
                        f'to coordinate final details, access, and any last-minute design preferences.\n\n'
                        f'It is our privilege to prepare your home for market.\n\n'
                        f'Jasmine Santana\n'
                        f'Owner & Creative Director · Styling With Jas\n'
                        f'206-422-5618 · info@stylingwithjas.com'
                    ),
                    html_body=client_thankyou_html(first_name, amount_str, installation_date, address),
                    pdf_attachment=pdf_bytes,
                    pdf_filename=pdf_filename,
                    # No CC — Jasmine gets her own separate notification (easier to forward later)
                )

            # ── 2) JASMINE NOTIFICATION EMAIL (always — even when client email exists) ──
            jasmine_subject = f'💰 Payment Received — {client_display} ({amount_str})'
            jasmine_body = (
                f'Payment Received\n\n'
                f'Client: {client_display}\n'
                f'Amount: {amount_str}\n'
                f'Date: {payment_date} at {payment_time}\n'
                f'Invoice: #{invnum or "(not matched)"}\n'
            )
            if address: jasmine_body += f'Property: {address}\n'
            if installation_date: jasmine_body += f'Staging date: {installation_date}\n'
            if client_email:
                jasmine_body += f'\nReceipt sent to: {client_email}\n'
                jasmine_body += '(The PDF is attached below — forward to client if they say they didn\'t receive it.)'
            else:
                jasmine_body += '\n(No client email on file — receipt PDF not auto-sent to client.\n'
                jasmine_body += 'Forward the attached PDF manually if needed.)'

            send_email(
                to_email=JASMINE_EMAIL,
                subject=jasmine_subject,
                body_text=jasmine_body,
                html_body=notification_html(jasmine_body),
                pdf_attachment=pdf_bytes,
                pdf_filename=pdf_filename,
            )

            # Update Supabase
            if invnum:
                supabase_update_payment(invnum, {
                    'paid_date': paid_date_iso,
                    'payment_id': payment_id,
                    'amount': amount / 100,
                    'arrival_date': '',
                    'client_email': client_email,   # NEW
                })

            self._respond(200, {'received': True, 'type': event_type})

        # ── PAYOUT CREATED ──
        elif event_type == 'payout.created':
            amount = obj.get('amount', 0)
            amount_str = fmt_amount(amount)
            arrival_ts = obj.get('arrival_date', 0)
            arrival_str = fmt_arrival(arrival_ts) if arrival_ts else 'TBD'
            payout_id = obj.get('id', '')

            subject = f'🏦 Deposit Coming — {amount_str} arriving {arrival_str}'
            body = (
                f'Bank Deposit Scheduled\n\n'
                f'Amount: {amount_str}\n'
                f'Est. arrival: {arrival_str}\n'
                f'Stripe reference: {payout_id}\n\n'
                f'Funds are on the way to your bank account.'
            )
            send_email(
                to_email=JASMINE_EMAIL,
                subject=subject,
                body_text=body,
                html_body=notification_html(body),
            )
            self._respond(200, {'received': True, 'type': event_type})

        else:
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
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Stripe-Signature')

    def log_message(self, format, *args):
        pass
