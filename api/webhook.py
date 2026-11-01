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
import csv
import io
import calendar
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

# Matches the refundable pet deposit's payment link, e.g. "202092-PETDEP". Handled entirely
# separately from the invoice's own paid/balance math — see supabase_update_pet_deposit.
DEPOSIT_PAY_RE = re.compile(r'^(.+)-PETDEP$')

# Matches an extension payment link, e.g. "202092-EXT1" — see supabase_update_extension_payment.
EXT_PAY_RE = re.compile(r'^(.+)-EXT(\d+)$')


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
#   15 days out — heads-up to Jasmine, mainly a staffing check (mover assigned yet?); also
#                 auto-advances the board card from Staged into Destage Scheduled.
#   9 days out  — heads-up to Jasmine with a ready-to-review mailto: link she can tap to
#                 send the client a predetermined reminder — nothing emails the client
#                 automatically, she decides (she can also send it directly from the board's
#                 Destage section any time via emailClientDestageReminder(), same wording).
# reminder_15_sent / reminder_7_sent flags on the invoice make each checkpoint fire once, and
# use <= rather than == so a missed cron run still catches up the next day. The second flag
# is still named reminder_7_sent (not renamed to reminder_9_sent) on purpose — invoices where
# the old 7-day checkpoint already fired must not get a duplicate reminder just because the
# threshold moved; keeping the same key preserves that history.

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

        if days_until <= 9 and not d.get('reminder_7_sent'):
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
            sent.append(f'{invnum}:9d')

        if updates:
            try:
                _patch_invoice_data(invnum, {**d, **updates})
            except Exception as e:
                print(f'[reminders] patch failed for {invnum}: {e}')

    return sent


# ════════════════════════════════════════════════════════════════
# QUOTE LIFECYCLE SWEEP + WEEKLY DIGEST (Phase 1 — folds into the SAME daily cron as
# run_destage_reminders(), rather than adding new vercel.json cron entries, since the
# Vercel Hobby plan this project is on caps a project at 2 cron jobs and both are already
# spoken for — see vercel.json)
# ════════════════════════════════════════════════════════════════

QUOTE_SILENCE_DAYS = 30   # keep in sync with board.html's constant of the same name

PRE_PAYMENT_STAGES = ('proposal', 'future_hold', 'done', 'denied', 'closed_lost')


def payment_due_date(d):
    """Payment due = staging date minus 48 hours. Returns a date or None."""
    startdate = d.get('startdate')
    if not startdate or startdate == 'TBD':
        return None
    try:
        return datetime.datetime.strptime(startdate, '%Y-%m-%d').date() - datetime.timedelta(days=2)
    except Exception:
        return None


def _list_business_invoices():
    """Every Jasmine-business invoice (SWJ staging), excluding drafts, GS Photography, and
    the internal system/config rows (PRICING_DEFAULTS/TIER_PRICING) — the same exclusion set
    every client-facing list in the app now applies (Phase 2.1/2.4)."""
    key = os.environ.get('SUPABASE_KEY', '')
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?select=invnum,client,address,invdate,data&order=created_at.desc&limit=2000',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        invoices = json.loads(r.read())
    out = []
    for inv in invoices:
        invnum = str(inv.get('invnum', ''))
        if invnum.startswith('DRAFT-') or invnum.startswith('GS') or invnum.startswith('TAX_REVIEW_'):
            continue
        if invnum in ('PRICING_DEFAULTS', 'TIER_PRICING', 'FINANCE_CONFIG', 'FINANCE_OVERRIDES'):
            continue
        out.append(inv)
    return out


def run_quote_lifecycle_sweep():
    """A quote with no client response for QUOTE_SILENCE_DAYS after the latest date_sent (or
    invdate, for legacy records with no date_sent yet) moves to closed_lost — archived, never
    deleted, still searchable from Archive's "Reactivate" button. future_hold quotes are
    exempt (client already accepted, just not signing yet)."""
    today = datetime.date.today()
    archived = []
    for inv in _list_business_invoices():
        d = inv.get('data') or {}
        stage = d.get('stage', 'proposal')
        if stage != 'proposal':
            continue   # future_hold is exempt; anything else is already past this stage
        sent_str = d.get('date_sent') or d.get('invdate')
        if not sent_str:
            continue
        try:
            sent_date = datetime.datetime.strptime(sent_str[:10], '%Y-%m-%d').date()
        except Exception:
            continue
        silent_days = (today - sent_date).days
        if silent_days < QUOTE_SILENCE_DAYS:
            continue
        invnum = inv.get('invnum', '')
        client = inv.get('client') or d.get('client') or 'Client'
        try:
            _patch_invoice_data(invnum, {**d, 'stage': 'closed_lost'})
            archived.append(f'{invnum}:{client}:{silent_days}d')
        except Exception as e:
            print(f'[quote_lifecycle] patch failed for {invnum}: {e}')

    if archived:
        body = (
            f'{len(archived)} quote(s) moved to Closed — Lost after {QUOTE_SILENCE_DAYS}+ days of silence '
            f'(no client response since the proposal was sent). Nothing was deleted — each one is still '
            f'searchable from Archive, with a "Reactivate to Quoted" button if the client responds late.\n\n'
            + '\n'.join(archived)
        )
        send_email(JASMINE_EMAIL, f'{len(archived)} quote(s) archived as Closed — Lost',
                   body, html_body=notification_html(body))
    return archived


def run_weekly_digest():
    """Internal-only summary (never a client send) listing overdue payments, payments due
    soon, and payer-unknown quick invoices needing a payer attached. Gated to Mondays by the
    caller — this function itself has no day-of-week logic."""
    today = datetime.date.today()
    overdue, due_soon, needs_payer = [], [], []
    for inv in _list_business_invoices():
        d = inv.get('data') or {}
        stage = d.get('stage', 'proposal')
        client = inv.get('client') or d.get('client') or 'Client'
        invnum = inv.get('invnum', '')

        if d.get('payer_unknown'):
            needs_payer.append(f'#{invnum} — {client}')

        if d.get('paid') or stage in PRE_PAYMENT_STAGES:
            continue
        due = payment_due_date(d)
        if not due:
            continue
        days = (today - due).days
        if days > 0:
            overdue.append(f'#{invnum} — {client} — due {abs(days)}d ago')
        elif -days <= 7:
            due_soon.append(f'#{invnum} — {client} — due in {-days}d')

    body = (
        f'Weekly Needs Attention digest — {today.strftime("%B %d, %Y")} (Styling With Jas only; '
        f'GS Photography is never included).\n\n'
        f'Overdue payments ({len(overdue)}):\n' + ('\n'.join(overdue) if overdue else 'None') + '\n\n'
        f'Payment due within 7 days ({len(due_soon)}):\n' + ('\n'.join(due_soon) if due_soon else 'None') + '\n\n'
        f'Needs a payer attached ({len(needs_payer)}):\n' + ('\n'.join(needs_payer) if needs_payer else 'None')
    )
    return send_email(JASMINE_EMAIL, f'Weekly Needs Attention Digest — {today.strftime("%b %d")}',
                       body, html_body=notification_html(body))


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


def supabase_update_extension_payment(parent_invnum, ext_invnum, payment_info):
    """An extension payment link (invnum like '202092-EXT1') got paid. Extensions have never
    been their own row in the invoices table — they live only in the PARENT invoice's
    data.extensions[] array — so this marks the matching entry paid there instead of looking
    for a Supabase row that doesn't exist (the gap supabase_update_payment's docstring notes).
    This is what makes extension revenue visible to cash-basis tax reporting at all (Phase 5.3:
    "Extension fees appear as their own visible category") — before this, a paid extension had
    no recorded payment date anywhere, so it could never be picked up by period."""
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
        extensions = current_data.get('extensions') or []

        entry = next((e for e in extensions if e.get('invnum') == ext_invnum), None)
        if entry is None:
            print(f'Extension {ext_invnum} not found on {parent_invnum}')
            return False

        # Idempotent against Stripe webhook redelivery.
        if entry.get('paid') and entry.get('payment_id') == payment_info.get('payment_id'):
            return True

        entry['paid'] = True
        entry['payment_id'] = payment_info.get('payment_id', '')
        entry['paid_date'] = payment_info.get('paid_date', '')
        entry['amount'] = payment_info.get('amount', entry.get('amount', 0))

        current_data['extensions'] = extensions
        return _patch_invoice_data(parent_invnum, current_data)
    except Exception as e:
        print(f'Extension payment update error: {e}')
        return False


def supabase_update_pet_deposit(parent_invnum, payment_info):
    """The refundable pet deposit link (id like '202092-PETDEP') got paid. Marks
    data.pet_deposit as paid on the PARENT invoice — never touches amount_paid/paid/grand,
    since the deposit was always kept out of that math (not taxed, not counted as revenue)."""
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
        pet_deposit = current_data.get('pet_deposit') or {}

        # Idempotent against Stripe webhook redelivery.
        if pet_deposit.get('status') == 'paid' and pet_deposit.get('payment_id') == payment_info.get('payment_id'):
            return True

        pet_deposit['status'] = 'paid'
        pet_deposit['payment_id'] = payment_info.get('payment_id', '')
        pet_deposit['paid_date'] = payment_info.get('paid_date', '')
        pet_deposit['amount'] = payment_info.get('amount', pet_deposit.get('amount', 0))
        current_data['pet_deposit'] = pet_deposit
        return _patch_invoice_data(parent_invnum, current_data)
    except Exception as e:
        print(f'Pet deposit update error: {e}')
        return False


def supabase_update_payment(invnum, payment_info):
    # Route split-payment links (metadata.invoice like "202092-PAY1") to the parent
    # invoice's payments[] array instead of looking for a row that doesn't exist.
    m = SPLIT_PAY_RE.match(invnum)
    if m:
        return supabase_update_split_payment(m.group(1), 'PAY' + m.group(2), payment_info)

    # Route extension payment links (metadata.invoice like "202092-EXT1") to the parent
    # invoice's extensions[] array — same reasoning as the split-payment route above.
    m = EXT_PAY_RE.match(invnum)
    if m:
        return supabase_update_extension_payment(m.group(1), invnum, payment_info)

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
# MONTHLY TAX REPORT (Vercel Cron — see vercel.json)
# ════════════════════════════════════════════════════════════════
# Fires on the 1st of each month and emails Jasmine the prior month's report as two CSV
# attachments. This is a server-side Python port of tax-report.html's client-side JS — kept
# as faithful to it as possible, but it IS a separate copy (same tradeoff as index.html vs.
# luxury.html elsewhere in this app), so a change to one should be mirrored in the other.

MONTH_NAMES = ['January','February','March','April','May','June','July','August',
               'September','October','November','December']


def _tax_ov(v):
    return float(v) if v not in (None, '') else None


def tax_compute_subtotal(d):
    base = float(d.get('base_price', 2500) or 2500)
    addons_total = sum(float(a.get('price', 0) or 0) for a in (d.get('addons') or []))
    sub_ov = _tax_ov(d.get('subtotal_override'))
    return sub_ov if sub_ov is not None else (base + addons_total)


def tax_compute_tax(d):
    tax_ov = _tax_ov(d.get('tax_override'))
    if tax_ov is not None:
        return tax_ov
    subtotal = tax_compute_subtotal(d)
    taxrate = float(d.get('taxrate', 0) or 0)
    return subtotal * taxrate / 100


def tax_compute_grand(d):
    tot_ov = _tax_ov(d.get('total_override'))
    if tot_ov is not None:
        return tot_ov
    return tax_compute_subtotal(d) + tax_compute_tax(d)


def tax_compute_due_date(d):
    startdate = d.get('startdate')
    if not startdate or startdate == 'TBD':
        return 'TBD'
    try:
        sd = datetime.datetime.strptime(startdate, '%Y-%m-%d') - datetime.timedelta(days=2)
        return sd.strftime('%Y-%m-%d')
    except Exception:
        return 'TBD'


# Same best-effort regex approach as the client-side version — no structured city field
# exists anywhere in this app, so this is a heuristic, not a guarantee. ok=False means it
# should be reviewed by hand rather than trusted.
def tax_parse_address(address):
    addr = (address or '').strip()
    if not addr:
        return {'city': '', 'zip': '', 'ok': False}
    zip_match = re.search(r'\b(\d{5})(-\d{4})?\b', addr)
    zip_code = zip_match.group(1) if zip_match else ''
    city_match = re.search(r",\s*([A-Za-z .'-]+?)[, ]+WA\b", addr, re.IGNORECASE)
    if city_match:
        return {'city': city_match.group(1).strip(), 'zip': zip_code, 'ok': True}
    parts = [p.strip() for p in addr.split(',') if p.strip()]
    if len(parts) >= 2:
        city = re.sub(r'\s+WA.*$', '', parts[1], flags=re.IGNORECASE).strip()
        return {'city': city, 'zip': zip_code, 'ok': bool(zip_code)}
    return {'city': '', 'zip': zip_code, 'ok': False}


def tax_categorize(desc):
    s = (desc or '').lower()
    if 'photo' in s or 'consult' in s or 'design fee' in s:
        return 'service'
    return 'retailing'


# Phase 5.1 invoices (flagged price_led_tax) store real city/zip fields captured at creation;
# older invoices only have them inside the free-text address, so fall back to the same
# best-effort parse this report has always used for those.
def tax_resolve_location(inv, d):
    if d.get('price_led_tax') and d.get('zip'):
        return {'city': d.get('city') or '', 'zip': d.get('zip'), 'ok': bool(d.get('city'))}
    return tax_parse_address(inv.get('address') or d.get('address') or '')


# Fix spec (Nov 2026): a job whose jurisdiction can't be confirmed is flagged clearly, not
# merged into a vague "(Unparsed)" bucket — it still counts fully in every total.
TAX_JURISDICTION_UNCONFIRMED = 'Jurisdiction unconfirmed — review'


# B&O rates (Phase 5.3) — see TAX_METHODOLOGY.md. Not a certified classification.
BO_RETAIL_RATE = 0.00471
BO_SERVICE_RATE = 0.015
# Card-processing-fee benchmark — no live Stripe fee feed exists in this app.
STRIPE_FEE_PCT = 0.029
STRIPE_FEE_FLAT = 0.30


def tax_invoice_split(d):
    """Mirrors invoiceTaxSplit() in tax-report.html exactly — must stay in sync with it, and
    with priceLedTax() in index.html/luxury.html, or the automated email won't match the
    interactive report or the client's own invoice."""
    has_override = any(_tax_ov(d.get(k)) is not None for k in ('subtotal_override', 'tax_override', 'total_override'))
    base = float(d.get('base_price', 2500) or 2500)
    addons = d.get('addons') or []
    addon_total = sum(float(a.get('price', 0) or 0) for a in addons)
    rate = float(d.get('taxrate', 0) or 0)

    if has_override or not d.get('price_led_tax'):
        subtotal = tax_compute_subtotal(d)
        tax = tax_compute_tax(d)
        grand = tax_compute_grand(d)
        svc = 0.0
        ret = base
        for a in addons:
            amt = float(a.get('price', 0) or 0)
            if (a.get('type') or tax_categorize(a.get('desc'))) == 'service':
                svc += amt
            else:
                ret += amt
        line_total = svc + ret
        rental_share = (ret / line_total) if line_total > 0.009 else 1.0
        return {'subtotal': subtotal, 'tax': tax, 'grand': grand, 'rental_share': rental_share}

    grand = base + addon_total + (base + addon_total) * rate / 100
    rsb = d.get('rental_share_base')
    rental_share_base = float(rsb) if rsb not in (None, '') else 0.5
    rental_dollars = base * rental_share_base + sum(
        float(a.get('price', 0) or 0) for a in addons if a.get('type') != 'service'
    )
    all_dollars = base + addon_total
    rental_share = (rental_dollars / all_dollars) if all_dollars > 0.009 else 1.0
    r = rate / 100
    subtotal = (grand / (1 + r * rental_share)) if r > 0 else grand
    tax = grand - subtotal
    return {'subtotal': subtotal, 'tax': tax, 'grand': grand, 'rental_share': rental_share}


def tax_payment_events(d):
    """Every payment EVENT that actually cleared, each tagged with the date received —
    cash-basis reporting keys off this, not the invoice date."""
    events = []
    splits = d.get('payments') or []
    if splits:
        for p in splits:
            if p.get('status') == 'paid' and p.get('paid_date'):
                # manual_method:'stripe' is a confirmed-after-the-fact correction (no real
                # payment_id captured) — see archive.html's Cleanup Review. Never fabricated.
                events.append({
                    'date': p['paid_date'],
                    'amount': float(p.get('amount', 0) or 0),
                    'method': 'Stripe (Card)' if (p.get('payment_id') or p.get('manual_method') == 'stripe') else 'Other/Manual',
                })
    elif d.get('paid') and d.get('paid_date'):
        amount = d.get('amount_paid')
        if amount is None:
            amount = d.get('stripe_amount')
        if amount is None:
            amount = tax_compute_grand(d)
        events.append({
            'date': d['paid_date'],
            'amount': float(amount or 0),
            'method': 'Stripe (Card)' if d.get('stripe_payment_id') else 'Other/Manual',
        })
    return events


TAX_SYSTEM_INVNUMS = ('PRICING_DEFAULTS', 'TIER_PRICING', 'FINANCE_CONFIG', 'FINANCE_OVERRIDES')


def _tax_list_invoices():
    """Every Styling With Jas staging invoice, drafts/GS/system rows excluded. Fix spec (Nov
    2026): the audit record must show every dollar received — signature status is a flag on
    a line, never a filter that removes it, so this no longer requires signed is True (that
    used to also incidentally exclude the system rows below, which now need an explicit
    exclusion instead)."""
    key = os.environ.get('SUPABASE_KEY', '')
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?select=invnum,client,address,data&order=created_at.desc&limit=1000',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        invoices = json.loads(r.read())
    out = []
    for inv in invoices:
        invnum = str(inv.get('invnum', ''))
        if invnum.startswith('DRAFT-') or invnum.startswith('GS') or invnum.startswith('TAX_REVIEW_'):
            continue
        if invnum in TAX_SYSTEM_INVNUMS:
            continue
        out.append(inv)
    return out


def build_monthly_tax_report(year, month):
    """Returns (summary_csv_bytes, detail_csv_bytes, stats_dict) for the given year/month,
    same cash-basis / every-staging-payment-regardless-of-signature / SWJ-only rules as
    tax-report.html — signature status is a flag per line, never a filter (fix spec, Nov 2026)."""
    period_start = f'{year:04d}-{month:02d}-01'
    last_day = calendar.monthrange(year, month)[1]
    period_end = f'{year:04d}-{month:02d}-{last_day:02d}'
    period_label = f'{MONTH_NAMES[month-1]} {year}'

    invoices = _tax_list_invoices()

    city_map = {}  # city -> {rates:set, taxable:float, tax:float}
    gross_receipts = tax_collected = retail_total = service_total = extension_total = card_fees = 0.0
    detail_rows = []
    unparsed = []
    unsigned = []
    exempt_rows = []  # (invnum, client, amount, reason, cert)

    for inv in invoices:
        d = inv.get('data') or {}
        client = inv.get('client') or d.get('client') or 'Client'
        loc = tax_resolve_location(inv, d)
        city_key = loc['city'] or TAX_JURISDICTION_UNCONFIRMED
        is_exempt = bool(d.get('exempt_reason'))
        # Signature is a flag on the line, never a filter — see _tax_list_invoices() above.
        signed_flag = d.get('signed') is True
        signed_label = 'Signed' if signed_flag else 'UNSIGNED — needs signature'
        # Only flag unsigned/unconfirmed-jurisdiction for a job with receipts THIS period.
        period_received_total = 0.0

        events = [e for e in tax_payment_events(d) if period_start <= e['date'] <= period_end]
        if events:
            split = tax_invoice_split(d)
            tax_rate = float(d.get('taxrate', 0) or 0)
            tax_fraction = (split['tax'] / split['grand']) if split['grand'] > 0.009 else 0.0

            period_received = sum(e['amount'] for e in events)
            period_tax = 0.0 if is_exempt else period_received * tax_fraction
            gross_receipts += period_received
            tax_collected += period_tax
            period_received_total += period_received
            for e in events:
                if e['method'] == 'Stripe (Card)':
                    card_fees += e['amount'] * STRIPE_FEE_PCT + STRIPE_FEE_FLAT

            if is_exempt:
                exempt_rows.append((inv.get('invnum'), client, period_received, d.get('exempt_reason'), d.get('exempt_cert') or ''))
            else:
                bucket = city_map.setdefault(city_key, {'rates': set(), 'taxable': 0.0, 'tax': 0.0})
                bucket['rates'].add(f'{tax_rate:.2f}%')
                bucket['taxable'] += (period_received - period_tax)
                bucket['tax'] += period_tax

            net_received = period_received - period_tax
            service_total += net_received * (1 - split['rental_share'])
            retail_total += net_received * split['rental_share']

            due_date = tax_compute_due_date(d)
            methods = ', '.join(sorted(set(e['method'] for e in events)))
            rooms_n = len([r for r in (d.get('baserooms') or '').split(',') if r.strip()])
            base = float(d.get('base_price', 2500) or 2500)
            addons = d.get('addons') or []

            line_items = [{'desc': f'Base Package ({rooms_n} rooms)', 'price': base, 'type': 'rental' if d.get('price_led_tax') else None}]
            line_items += [{'desc': a.get('desc', ''), 'price': float(a.get('price', 0) or 0), 'type': a.get('type')} for a in addons]

            for i, li in enumerate(line_items):
                category = 'exempt' if is_exempt else (li['type'] or tax_categorize(li['desc']))
                exemption = (d.get('exempt_reason', '') + (f" ({d.get('exempt_cert')})" if d.get('exempt_cert') else '')) if is_exempt else ''
                detail_rows.append([
                    inv.get('invnum'), d.get('invdate', inv.get('invdate', '')), due_date, client, signed_label,
                    loc['city'], loc['zip'], li['desc'], category,
                    f"{li['price']:.2f}", 'No' if is_exempt else 'Yes', exemption,
                    f'{tax_rate:.2f}%' if i == 0 else '', f"{split['tax']:.2f}" if i == 0 else '',
                    f"{split['subtotal']:.2f}" if i == 0 else '', f"{split['grand']:.2f}" if i == 0 else '',
                    f'{period_received:.2f}' if i == 0 else '', methods if i == 0 else '',
                    (f'{(period_received*STRIPE_FEE_PCT+STRIPE_FEE_FLAT):.2f}' if i == 0 and 'Stripe' in methods else ('0.00' if i == 0 else '')),
                ])

            pet_deposit = d.get('pet_deposit') or {}
            if pet_deposit.get('enabled'):
                detail_rows.append([
                    inv.get('invnum'), d.get('invdate', inv.get('invdate', '')), due_date, client, signed_label,
                    loc['city'], loc['zip'], 'Refundable Pet Deposit', 'deposit (not revenue)',
                    f"{float(pet_deposit.get('amount', 0) or 0):.2f}", 'No', 'Refundable deposit — not a sale',
                    '', '', '', '', '', '', '',
                ])

        # Extensions (Phase 5.3) — see supabase_update_extension_payment; these were invisible
        # to this report entirely before that existed, since they're not their own row.
        for ext in (d.get('extensions') or []):
            if not ext.get('paid') or not ext.get('paid_date'):
                continue
            if not (period_start <= ext['paid_date'] <= period_end):
                continue
            amt = float(ext.get('amount', 0) or 0)
            if amt <= 0.009:
                continue
            tax_rate = float(d.get('taxrate', 0) or 0)
            r = tax_rate / 100
            ext_tax = 0.0 if is_exempt else (amt - amt / (1 + r) if r > 0 else 0.0)
            ext_subtotal = amt - ext_tax
            gross_receipts += amt
            tax_collected += ext_tax
            period_received_total += amt
            retail_total += ext_subtotal
            extension_total += ext_subtotal
            method = 'Stripe (Card)' if (ext.get('payment_id') or ext.get('manual_method') == 'stripe') else 'Other/Manual'
            if method == 'Stripe (Card)':
                card_fees += amt * STRIPE_FEE_PCT + STRIPE_FEE_FLAT
            if is_exempt:
                exempt_rows.append((ext.get('invnum'), client, amt, d.get('exempt_reason'), d.get('exempt_cert') or ''))
            else:
                bucket = city_map.setdefault(city_key, {'rates': set(), 'taxable': 0.0, 'tax': 0.0})
                bucket['rates'].add(f'{tax_rate:.2f}%')
                bucket['taxable'] += ext_subtotal
                bucket['tax'] += ext_tax
            exemption = (d.get('exempt_reason', '') + (f" ({d.get('exempt_cert')})" if d.get('exempt_cert') else '')) if is_exempt else ''
            detail_rows.append([
                ext.get('invnum'), ext.get('created', ''), '', client, signed_label, loc['city'], loc['zip'],
                f"Extension ({ext.get('days', '?')} days, of #{inv.get('invnum')})", 'exempt' if is_exempt else 'extension',
                f'{ext_subtotal:.2f}', 'No' if is_exempt else 'Yes', exemption,
                f'{tax_rate:.2f}%', f'{ext_tax:.2f}', f'{ext_subtotal:.2f}', f'{amt:.2f}',
                f'{amt:.2f}', method,
                f'{(amt*STRIPE_FEE_PCT+STRIPE_FEE_FLAT):.2f}' if method == 'Stripe (Card)' else '0.00',
            ])

        # Flag unsigned/unconfirmed-jurisdiction once per job, only when it actually has
        # receipts counted THIS period — never silently, since both are still counted in full.
        if period_received_total > 0.009:
            if not signed_flag:
                unsigned.append(f"#{inv.get('invnum')} — {client} ({period_received_total:,.2f})")
            if not loc['ok']:
                blank_rate_suspect = abs(float(d.get('taxrate', 0) or 0) - 10.25) < 0.005
                note = f"#{inv.get('invnum')} — {client} — {inv.get('address') or d.get('address') or '(no address)'}"
                if blank_rate_suspect:
                    note += ' — rate shown is the 10.25% blank-form default, not a confirmed jurisdiction rate'
                unparsed.append(note)

    bo_retail = retail_total * BO_RETAIL_RATE
    bo_service = service_total * BO_SERVICE_RATE
    net_revenue = gross_receipts - card_fees

    # Summary CSV
    summary_buf = io.StringIO()
    w = csv.writer(summary_buf)
    w.writerow(['City', 'Tax Rate(s)', 'Taxable Sales', 'Tax Collected'])
    for city in sorted(city_map.keys()):
        b = city_map[city]
        w.writerow([city, '; '.join(sorted(b['rates'])), f"{b['taxable']:.2f}", f"{b['tax']:.2f}"])
    w.writerow([])
    w.writerow(['Period', period_label])
    w.writerow(['Gross Receipts', f'{gross_receipts:.2f}'])
    w.writerow(['Total Sales Tax Collected', f'{tax_collected:.2f}'])
    w.writerow(['Retailing (Rental) Receipts', f'{retail_total:.2f}'])
    w.writerow(['Service & Other Receipts', f'{service_total:.2f}'])
    w.writerow(['Extension Fees (within Retailing)', f'{extension_total:.2f}'])
    w.writerow(['Card Processing Fees (benchmark est.)', f'{card_fees:.2f}'])
    w.writerow(['Net Revenue', f'{net_revenue:.2f}'])
    w.writerow(['Retailing B&O (0.471%)', f'{bo_retail:.2f}'])
    w.writerow(['Service & Other B&O (1.5%)', f'{bo_service:.2f}'])
    w.writerow(['Total B&O', f'{(bo_retail+bo_service):.2f}'])
    w.writerow(['Exempt Sales Count', str(len(exempt_rows))])
    w.writerow(['Exempt Sales Total', f"{sum(r[2] for r in exempt_rows):.2f}"])
    w.writerow(['Unsigned Jobs Count (counted in totals, flagged for paperwork)', str(len(unsigned))])

    # Detail CSV
    detail_buf = io.StringIO()
    w2 = csv.writer(detail_buf)
    w2.writerow(['Invoice #', 'Invoice Date', 'Due Date', 'Customer', 'Signature', 'City', 'Zip', 'Line Item',
                 'Category', 'Item Amount', 'Taxable', 'Exemption Reason', 'Tax Rate',
                 'Invoice Tax Total', 'Invoice Subtotal', 'Invoice Grand Total',
                 'Received This Period', 'Payment Method', 'Processing Fee (benchmark est.)'])
    w2.writerows(detail_rows)

    stats = {
        'period_label': period_label, 'gross_receipts': gross_receipts, 'tax_collected': tax_collected,
        'retail_total': retail_total, 'service_total': service_total, 'extension_total': extension_total,
        'card_fees': card_fees, 'net_revenue': net_revenue, 'bo_retail': bo_retail, 'bo_service': bo_service,
        'bo_total': bo_retail + bo_service, 'exempt_count': len(exempt_rows),
        'exempt_total': sum(r[2] for r in exempt_rows),
        'invoice_count': len({r[0] for r in detail_rows}), 'unparsed': unparsed, 'unsigned': unsigned,
    }
    return summary_buf.getvalue().encode(), detail_buf.getvalue().encode(), stats


def send_monthly_tax_report_email(summary_csv, detail_csv, stats):
    password = os.environ.get('EMAIL_PASSWORD', '')
    if not password:
        print('[tax_report] EMAIL_PASSWORD not set')
        return False
    try:
        msg = MIMEMultipart('mixed')
        msg['From'] = FROM_EMAIL
        msg['To'] = JASMINE_EMAIL
        msg['Subject'] = f"Monthly Tax Report — {stats['period_label']}"

        unsigned_note = ''
        if stats['unsigned']:
            unsigned_note = ("\n\n⚠ " + str(len(stats['unsigned'])) + " job(s) this period are paid but have "
                              "no signed contract on file — counted in full above, not dropped. Follow up for "
                              "paperwork:\n" + '\n'.join(stats['unsigned']))
        unparsed_note = ''
        if stats['unparsed']:
            unparsed_note = ("\n\n⚠ " + str(len(stats['unparsed'])) + " job(s) — \"" + TAX_JURISDICTION_UNCONFIRMED
                              + "\" — counted in full above, but grouped together in the city breakdown because "
                              "the jurisdiction isn't confirmed. Review and correct the property address/city "
                              "manually:\n" + '\n'.join(stats['unparsed']))
        body = (
            f"Monthly tax report for {stats['period_label']} (cash basis — every Styling With Jas staging job "
            f"with a payment received this month, signature status flagged per line, not filtered out).\n\n"
            f"Gross Receipts: ${stats['gross_receipts']:,.2f}\n"
            f"Sales Tax Collected: ${stats['tax_collected']:,.2f}\n"
            f"Retailing (Rental, incl. extensions ${stats['extension_total']:,.2f}): ${stats['retail_total']:,.2f}\n"
            f"Service & Other: ${stats['service_total']:,.2f}\n"
            f"Retailing B&O (0.471%): ${stats['bo_retail']:,.2f}\n"
            f"Service & Other B&O (1.5%): ${stats['bo_service']:,.2f}\n"
            f"Total B&O: ${stats['bo_total']:,.2f}\n"
            f"Card Processing Fees (benchmark est.): ${stats['card_fees']:,.2f}\n"
            f"Net Revenue: ${stats['net_revenue']:,.2f}\n"
            f"Exempt sales: {stats['exempt_count']} (${stats['exempt_total']:,.2f})\n"
            f"Invoices with activity this period: {stats['invoice_count']}\n\n"
            f"Full breakdown attached as CSV. Category tags and B&O amounts are a best-effort "
            f"allocation per the method documented in TAX_METHODOLOGY.md, not CPA-certified — "
            f"have this reviewed before filing. This has NOT been marked reviewed — do that from "
            f"/tax-report.html before treating it as final (reconciling actual bank/Stripe deposits "
            f"against this total is required before that gate opens)."
            f"{unsigned_note}{unparsed_note}\n\n"
            f"See the full interactive report (including reconciliation and the pre-filing pull) "
            f"anytime at /tax-report.html."
        )
        msg.attach(MIMEText(body, 'plain'))

        for content, filename in (
            (summary_csv, f"SWJ_Tax_Summary_{stats['period_label'].replace(' ', '_')}.csv"),
            (detail_csv, f"SWJ_Tax_Invoice_Detail_{stats['period_label'].replace(' ', '_')}.csv"),
        ):
            att = MIMEApplication(content, _subtype='csv')
            att.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(att)

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.login(FROM_EMAIL, password)
            server.sendmail(FROM_EMAIL, [JASMINE_EMAIL], msg.as_string())
        return True
    except Exception as e:
        print(f'[tax_report] send error: {e}')
        return False


def _tax_list_all_rows():
    """Every row in the invoices table, no exclusions — used only for the full-table backup
    export below, which needs the config/review rows every other tax-report query in this
    file deliberately excludes (fix spec addendum, Nov 1 2026)."""
    key = os.environ.get('SUPABASE_KEY', '')
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?select=*&order=created_at.desc&limit=5000',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'}
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _csv_bytes(header, row_iter):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in row_iter:
        w.writerow(row)
    return buf.getvalue().encode()


def build_tax_backup_export():
    """Full-table export of tax-critical data to CSV, outside Supabase — the recovery copy
    behind the monthly audit packet (fix spec addendum, Nov 1 2026: 'an untested export is
    not a backup' — see the restore-test script kept alongside this fix, verified against a
    mock October fixture since this sandbox has no live Supabase access to test against the
    real data). No credentials, API keys, or card data ever appear here: this app never
    stores card numbers (Stripe tokenizes all of that) and env vars are never touched here —
    payment rows carry only amount, date, method and the Stripe reference id, never a secret."""
    rows = _tax_list_all_rows()

    real_invoices, config_rows, review_rows = [], [], []
    for inv in rows:
        invnum = str(inv.get('invnum', ''))
        if invnum.startswith('DRAFT-') or invnum.startswith('GS'):
            continue
        if invnum.startswith('TAX_REVIEW_'):
            review_rows.append(inv)
        elif invnum in TAX_SYSTEM_INVNUMS:
            config_rows.append(inv)
        else:
            real_invoices.append(inv)

    invoices_csv = _csv_bytes(
        ['invnum', 'client', 'address', 'city', 'zip', 'invdate', 'base_price', 'taxrate',
         'price_led_tax', 'signed', 'stage', 'exempt_reason', 'exempt_cert', 'created_at'],
        (
            [
                inv.get('invnum'), inv.get('client'), inv.get('address'),
                (inv.get('data') or {}).get('city', ''), (inv.get('data') or {}).get('zip', ''),
                inv.get('invdate'), (inv.get('data') or {}).get('base_price', ''),
                (inv.get('data') or {}).get('taxrate', ''), (inv.get('data') or {}).get('price_led_tax', False),
                (inv.get('data') or {}).get('signed', False), (inv.get('data') or {}).get('stage', ''),
                (inv.get('data') or {}).get('exempt_reason', ''), (inv.get('data') or {}).get('exempt_cert', ''),
                inv.get('created_at'),
            ] for inv in real_invoices
        )
    )

    payments_rows = []
    for inv in real_invoices:
        for p in ((inv.get('data') or {}).get('payments') or []):
            payments_rows.append([inv.get('invnum'), p.get('amount'), p.get('paid_date'), p.get('status'),
                                   p.get('payment_id', ''), p.get('manual_method', ''), p.get('label', '')])
    payments_csv = _csv_bytes(['invnum', 'amount', 'paid_date', 'status', 'payment_id', 'manual_method', 'label'], payments_rows)

    extensions_rows = []
    for inv in real_invoices:
        for ext in ((inv.get('data') or {}).get('extensions') or []):
            extensions_rows.append([inv.get('invnum'), ext.get('invnum', ''), ext.get('days', ''),
                                     ext.get('amount'), ext.get('paid', False), ext.get('paid_date', ''),
                                     ext.get('payment_id', ''), ext.get('manual_method', ''), ext.get('created', '')])
    extensions_csv = _csv_bytes(['parent_invnum', 'ext_invnum', 'days', 'amount', 'paid', 'paid_date',
                                  'payment_id', 'manual_method', 'created'], extensions_rows)

    signatures_rows = []
    for inv in real_invoices:
        for sig in ((inv.get('data') or {}).get('signatures') or []):
            signatures_rows.append([inv.get('invnum'), sig.get('label', sig.get('role', '')), sig.get('signed_by', ''),
                                     sig.get('signed_date', ''), sig.get('signed_time', ''), sig.get('ip_address', '')])
    signatures_csv = _csv_bytes(['invnum', 'role', 'signed_by', 'signed_date', 'signed_time', 'ip_address'], signatures_rows)

    reviews_csv = _csv_bytes(
        ['period_key', 'reviewed', 'reviewed_at', 'reconciled_actual', 'reconciled_diff',
         'reconciled_clean', 'reconciled_explain', 'dor_confirmation_number'],
        (
            [
                str(inv.get('invnum', '')).replace('TAX_REVIEW_', ''),
                (inv.get('data') or {}).get('reviewed', False), (inv.get('data') or {}).get('reviewed_at', ''),
                (inv.get('data') or {}).get('reconciled_actual', ''), (inv.get('data') or {}).get('reconciled_diff', ''),
                (inv.get('data') or {}).get('reconciled_clean', ''), (inv.get('data') or {}).get('reconciled_explain', ''),
                (inv.get('data') or {}).get('dor_confirmation_number', ''),
            ] for inv in review_rows
        )
    )

    config_csv = _csv_bytes(
        ['config_key', 'json_value'],
        ([inv.get('invnum'), json.dumps(inv.get('data') or {})] for inv in config_rows)
    )

    return (
        {
            'invoices.csv': invoices_csv, 'payments.csv': payments_csv, 'extensions.csv': extensions_csv,
            'signatures.csv': signatures_csv, 'tax_reviews.csv': reviews_csv, 'config.csv': config_csv,
        },
        {
            'invoice_count': len(real_invoices), 'payment_count': len(payments_rows),
            'extension_count': len(extensions_rows), 'exported_at': datetime.datetime.now().isoformat(),
        }
    )


def send_monthly_tax_backup_email(csvs, stats):
    password = os.environ.get('EMAIL_PASSWORD', '')
    if not password:
        print('[tax_backup] EMAIL_PASSWORD not set')
        return False
    try:
        msg = MIMEMultipart('mixed')
        msg['From'] = FROM_EMAIL
        msg['To'] = JASMINE_EMAIL
        msg['Subject'] = f"Monthly Tax Data Backup — {datetime.date.today().strftime('%B %Y')}"
        body = (
            f"Full off-app export of tax-critical data, generated {stats['exported_at']}.\n\n"
            f"{stats['invoice_count']} invoices, {stats['payment_count']} payment records, "
            f"{stats['extension_count']} extensions — attached as CSV, re-importable without "
            f"this app. No credentials, API keys, or card data are included.\n\n"
            f"This is the recovery copy for the tax reporting system, separate from the lighter "
            f"monthly audit packet (its own email) — keep this even if nothing seems wrong; "
            f"it's only useful on the day something goes wrong with the app's own database."
        )
        msg.attach(MIMEText(body, 'plain'))
        for filename, content in csvs.items():
            part = MIMEApplication(content, _subtype='csv')
            part.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(part)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(FROM_EMAIL, password)
            server.sendmail(FROM_EMAIL, [JASMINE_EMAIL], msg.as_string())
        return True
    except Exception as e:
        print(f'[tax_backup] send error: {e}')
        return False


def run_monthly_tax_backup():
    csvs, stats = build_tax_backup_export()
    return send_monthly_tax_backup_email(csvs, stats), stats


# Signed-contract links are meant to be permanent archive links, not short-lived download
# tokens — 10 years so they effectively never expire. Duplicated from api/sign.py's
# _sign_contract_url() rather than imported across files (that cross-file import pattern
# exists elsewhere in this app but is wrapped in a try/except as best-effort, not guaranteed
# — too fragile for an unattended monthly cron). Keep in sync with sign.py's version.
TAX_CONTRACT_LINK_TTL = 315360000  # 10 years, in seconds


def _tax_sign_contract_url(invnum, expires_in=TAX_CONTRACT_LINK_TTL):
    key = os.environ.get('SUPABASE_KEY', '')
    if not key:
        return None
    filename = f'{invnum}-signed.pdf'
    sign_url_endpoint = f'{SUPABASE_URL}/storage/v1/object/sign/signed-contracts/{filename}'
    sign_req = urllib.request.Request(
        sign_url_endpoint, data=json.dumps({'expiresIn': expires_in}).encode(), method='POST'
    )
    sign_req.add_header('apikey', key)
    sign_req.add_header('Authorization', f'Bearer {key}')
    sign_req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(sign_req, timeout=15) as r:
            sign_resp = json.loads(r.read())
        signed_path = sign_resp.get('signedURL', '') or sign_resp.get('signedUrl', '')
        if signed_path:
            if signed_path.startswith('/'):
                return f'{SUPABASE_URL}/storage/v1{signed_path}'
            return f'{SUPABASE_URL}/storage/v1/{signed_path.lstrip("/")}'
    except Exception as e:
        print(f'[audit_packet] contract link error for #{invnum}: {e}')
    return None


def _tax_get_review_record(period_key):
    """Reads the SAME TAX_REVIEW_<period> record tax-report.html's Monthly Review writes to,
    so the packet's reconciliation line and DOR confirmation slot never drift from what Gabe
    actually entered on-screen — one record, read by both."""
    key = os.environ.get('SUPABASE_KEY', '')
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/invoices?invnum=eq.TAX_REVIEW_{period_key}&select=data",
            headers={'apikey': key, 'Authorization': f'Bearer {key}'}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = json.loads(r.read())
        return (rows[0].get('data') or {}) if rows else {}
    except Exception as e:
        print(f'[audit_packet] review lookup error: {e}')
        return {}


def build_monthly_audit_packet(year, month):
    """Assembles the monthly audit packet (fix spec addendum, Nov 1 2026): that month's
    staging invoices with tax stated, links to that month's signed contracts, payment
    records, the corrected tax report totals, the reconciliation line, and a slot for the
    DOR filing confirmation number. Reuses build_monthly_tax_report()'s own figures so the
    packet can never disagree with the tax report or its email — one source of truth."""
    summary_csv, detail_csv, stats = build_monthly_tax_report(year, month)
    period_start = f'{year:04d}-{month:02d}-01'
    last_day = calendar.monthrange(year, month)[1]
    period_end = f'{year:04d}-{month:02d}-{last_day:02d}'
    period_key = f'{year:04d}-{month:02d}'

    invoices = _tax_list_invoices()
    contract_rows = []
    for inv in invoices:
        d = inv.get('data') or {}
        signed_date = d.get('signed_date')
        if not signed_date or not (period_start <= signed_date <= period_end):
            continue
        contract_rows.append([
            inv.get('invnum'), inv.get('client') or d.get('client') or 'Client',
            signed_date, d.get('signed_by', ''), _tax_sign_contract_url(inv.get('invnum')) or '(link unavailable — see signed-contracts storage bucket)',
        ])
    contracts_csv = _csv_bytes(['invnum', 'client', 'signed_date', 'signed_by', 'contract_link'], contract_rows)

    review = _tax_get_review_record(period_key)

    return summary_csv, detail_csv, contracts_csv, stats, contract_rows, review, period_key


def send_monthly_audit_packet_email(summary_csv, detail_csv, contracts_csv, stats, contract_rows, review, period_key):
    password = os.environ.get('EMAIL_PASSWORD', '')
    if not password:
        print('[audit_packet] EMAIL_PASSWORD not set')
        return False
    try:
        msg = MIMEMultipart('mixed')
        msg['From'] = FROM_EMAIL
        msg['To'] = JASMINE_EMAIL
        msg['Subject'] = f"Monthly Audit Packet — {stats['period_label']}"

        if review.get('reconciled_actual') is not None:
            recon_line = (
                f"Reconciliation: app total {stats['gross_receipts']:,.2f} vs. actual deposits "
                f"{review['reconciled_actual']:,.2f} — "
                + ('clean.' if review.get('reconciled_clean') else f"diverged by {abs(review.get('reconciled_diff') or 0):,.2f}, explained as: \"{review.get('reconciled_explain', '')}\".")
            )
        else:
            recon_line = "Reconciliation: not yet entered — do that from the Reconciliation card on /tax-report.html."
        dor_line = (
            f"DOR filing confirmation number: {review['dor_confirmation_number']}"
            if review.get('dor_confirmation_number')
            else "DOR filing confirmation number: (not yet filed — enter it on /tax-report.html once the return is filed in My DOR)."
        )

        contracts_line = (
            f"{len(contract_rows)} contract(s) fully executed this period — links attached in contracts.csv."
            if contract_rows else "No contracts were fully executed this period."
        )

        body = (
            f"Monthly audit packet for {stats['period_label']} — the business record for this month's "
            f"close, assembled automatically. Nothing in this email goes to a client.\n\n"
            f"Gross Receipts: ${stats['gross_receipts']:,.2f}\n"
            f"Sales Tax Collected: ${stats['tax_collected']:,.2f}\n"
            f"Total B&O: ${stats['bo_total']:,.2f}\n"
            f"Invoices with activity this period: {stats['invoice_count']}\n\n"
            f"{contracts_line}\n\n"
            f"{recon_line}\n"
            f"{dor_line}\n\n"
            f"Full tax report (invoices.csv-equivalent detail) and contract links attached. This "
            f"packet is also the minimum monthly off-app copy of this month's figures — the "
            f"separate Monthly Tax Data Backup email is the full recovery copy of all tax-critical "
            f"data, not just this one month."
        )
        msg.attach(MIMEText(body, 'plain'))

        for content, filename in (
            (summary_csv, f"SWJ_Tax_Summary_{stats['period_label'].replace(' ', '_')}.csv"),
            (detail_csv, f"SWJ_Tax_Invoice_Detail_{stats['period_label'].replace(' ', '_')}.csv"),
            (contracts_csv, f"SWJ_Signed_Contracts_{stats['period_label'].replace(' ', '_')}.csv"),
        ):
            part = MIMEApplication(content, _subtype='csv')
            part.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(part)

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(FROM_EMAIL, password)
            server.sendmail(FROM_EMAIL, [JASMINE_EMAIL], msg.as_string())
        return True
    except Exception as e:
        print(f'[audit_packet] send error: {e}')
        return False


def run_monthly_audit_packet():
    """Assembles on the month that JUST ENDED, same as run_monthly_tax_report()."""
    today = datetime.date.today()
    prev_last_day = today.replace(day=1) - datetime.timedelta(days=1)
    summary_csv, detail_csv, contracts_csv, stats, contract_rows, review, period_key = build_monthly_audit_packet(
        prev_last_day.year, prev_last_day.month
    )
    sent = send_monthly_audit_packet_email(summary_csv, detail_csv, contracts_csv, stats, contract_rows, review, period_key)
    return sent, stats


def run_monthly_tax_report():
    """Reports on the month that JUST ENDED, since this fires on the 1st of the new month."""
    today = datetime.date.today()
    prev_last_day = today.replace(day=1) - datetime.timedelta(days=1)
    summary_csv, detail_csv, stats = build_monthly_tax_report(prev_last_day.year, prev_last_day.month)
    return send_monthly_tax_report_email(summary_csv, detail_csv, stats), stats


def run_monthly_close():
    """The real monthly close, fired by the ONE monthly cron entry this project has (Vercel
    Hobby plan caps a project at 2 cron jobs; both are already spoken for — see the comment
    on the daily job dispatch below). Sends all three monthly emails in one run: the tax
    report, the audit packet, and the full backup export — mirroring exactly how the daily
    no-job-param path already bundles multiple jobs under one shared firing instead of
    adding more cron entries. Each piece's own ?job= value still exists below for isolated
    on-demand testing of just that one piece, same relationship as weekly_digest/quote_sweep
    have to the bundled daily path."""
    report_sent, report_stats = run_monthly_tax_report()
    packet_sent, packet_stats = run_monthly_audit_packet()
    backup_sent, backup_stats = run_monthly_tax_backup()
    return {
        'report_emailed': report_sent, 'packet_emailed': packet_sent, 'backup_emailed': backup_sent,
        'period': report_stats['period_label'],
    }


# ════════════════════════════════════════════════════════════════
# WEBHOOK HANDLER
# ════════════════════════════════════════════════════════════════

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Vercel Cron Jobs hit this via GET (see vercel.json — two schedules share this same
        # path/handler: the daily destage-reminder job, and the monthly tax-report job
        # distinguished by a ?job= query param). Guarded by CRON_SECRET if it's set — Vercel
        # auto-sends it as a Bearer token when the env var exists. Worst case without it is
        # an extra email, not data loss, so this doesn't hard-fail while unconfigured.
        secret = os.environ.get('CRON_SECRET', '')
        if secret and self.headers.get('Authorization', '') != f'Bearer {secret}':
            self._respond(401, {'error': 'Unauthorized'})
            return
        query = urllib.parse.urlparse(self.path).query
        job = urllib.parse.parse_qs(query).get('job', [''])[0]
        try:
            if job == 'monthly_close':
                # This is what the one monthly cron entry in vercel.json actually calls — the
                # whole monthly close (tax report + audit packet + backup export) in one
                # firing, since there's no spare cron slot to schedule the other two
                # separately (Hobby plan caps a project at 2 cron jobs; both are already
                # spoken for — this monthly one and the daily one below).
                result = run_monthly_close()
                self._respond(200, {'ok': True, **result})
                return
            if job == 'monthly_tax_report':
                # Isolated on-demand trigger for testing just the tax report email, same
                # relationship weekly_digest/quote_sweep have to the bundled daily path below
                # — the real monthly cron fires monthly_close above, not this directly.
                sent, stats = run_monthly_tax_report()
                self._respond(200, {'ok': True, 'emailed': sent, 'period': stats['period_label']})
                return
            if job == 'monthly_audit_packet':
                sent, stats = run_monthly_audit_packet()
                self._respond(200, {'ok': True, 'emailed': sent, 'period': stats['period_label']})
                return
            if job == 'monthly_backup_export':
                sent, stats = run_monthly_tax_backup()
                self._respond(200, {'ok': True, 'emailed': sent})
                return
            if job == 'weekly_digest':
                # Manual on-demand trigger (Build 2 audit: the Monday-only digest had no way
                # to verify it live without waiting for an actual Monday) — bypasses the
                # day-of-week gate; the real cron still only calls this on Mondays.
                sent = run_weekly_digest()
                self._respond(200, {'ok': True, 'digest_sent': sent})
                return
            if job == 'quote_sweep':
                # Manual on-demand trigger for the 30-day silence sweep, independent of the
                # daily cron's own firing — same reasoning as weekly_digest above.
                archived = run_quote_lifecycle_sweep()
                self._respond(200, {'ok': True, 'quotes_archived': archived})
                return
            # Default (no ?job=) is the daily cron. Three jobs share this one daily firing
            # instead of adding separate vercel.json cron entries — this project is on the
            # Vercel Hobby plan, which caps a project at 2 cron jobs, and both are already
            # spoken for (this daily path + the monthly tax report above).
            sent = run_destage_reminders()
            archived = run_quote_lifecycle_sweep()
            digest_sent = False
            if datetime.date.today().weekday() == 0:   # Monday
                digest_sent = run_weekly_digest()
            self._respond(200, {
                'ok': True, 'reminders_sent': sent, 'quotes_archived': archived, 'weekly_digest_sent': digest_sent,
            })
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

            # Refundable pet deposit link — handled entirely separately from the normal
            # receipt/thank-you flow below (it's not a staging payment, so a formal
            # invoice receipt would be confusing). Just mark it paid and let Jasmine know.
            dep_match = DEPOSIT_PAY_RE.match(invnum)
            if dep_match:
                parent_invnum = dep_match.group(1)
                payment_info = {
                    'payment_id': payment_id,
                    'amount': amount / 100,
                    'paid_date': paid_date_iso,
                }
                supabase_update_pet_deposit(parent_invnum, payment_info)
                try:
                    send_email(
                        JASMINE_EMAIL,
                        f'Pet deposit received: {parent_invnum} ({client or "Client"})',
                        (f'The refundable pet deposit of {fmt_amount(amount)} for invoice '
                         f'{parent_invnum} ({client or "Client"}) has just been paid.\n\n'
                         f'Remember, this is fully refundable. Issue the refund from the '
                         f'board once staging items are picked up in good condition.'),
                    )
                except Exception as e:
                    print(f'Pet deposit notify error: {e}')
                self._respond(200, {'ok': True})
                return

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
