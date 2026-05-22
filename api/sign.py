import json
import os
import urllib.request
import urllib.parse
import datetime
import smtplib
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from http.server import BaseHTTPRequestHandler

SMTP_HOST = 'smtp.hostinger.com'
SMTP_PORT = 465
FROM_EMAIL = 'info@stylingwithjas.com'
JASMINE_EMAIL = 'info@stylingwithjas.com'
SUPABASE_URL = 'https://cwvdfgrjlrpsdfwduwvn.supabase.co'

def get_supabase_key():
    return os.environ.get('SUPABASE_KEY', '')

def supabase_get_invoice(invnum):
    key = get_supabase_key()
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}&select=*',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.loads(r.read())
    return rows[0] if rows else None

def supabase_update_invoice(invnum, updates):
    key = get_supabase_key()
    body = json.dumps(updates).encode()
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}',
        data=body, method='PATCH'
    )
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Prefer', 'return=minimal')
    with urllib.request.urlopen(req, timeout=10) as r:
        return True

def supabase_upload_pdf(invnum, pdf_bytes):
    """Upload signed PDF to Supabase Storage bucket 'signed-contracts'."""
    key = get_supabase_key()
    filename = f'{invnum}-signed.pdf'
    # Try insert first, then upsert if exists
    url = f'{SUPABASE_URL}/storage/v1/object/signed-contracts/{filename}'
    req = urllib.request.Request(url, data=pdf_bytes, method='POST')
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/pdf')
    req.add_header('x-upsert', 'true')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        # Build public URL for download
        contract_url = f'{SUPABASE_URL}/storage/v1/object/signed-contracts/{filename}'
        return contract_url
    except Exception as e:
        print(f'Storage upload error: {e}')
        return None

def generate_signed_pdf(inv, signed_by, signed_date, signed_time, ip_address):
    """Call the generate endpoint to produce a PDF, then overlay signature block."""
    # We'll call the Vercel generate endpoint with a flag to include signature
    try:
        base_url = os.environ.get('VERCEL_URL', '')
        if not base_url:
            # Fallback: build minimal PDF with reportlab
            return build_signature_pdf(inv, signed_by, signed_date, signed_time, ip_address)

        protocol = 'https'
        gen_url = f'{protocol}://{base_url}/api/generate'
        d = inv.get('data', {})
        payload = json.dumps({
            **d,
            'invnum': inv['invnum'],
            'client': inv.get('client', ''),
            'address': inv.get('address', ''),
            'invdate': inv.get('invdate', ''),
            'signed_by': signed_by,
            'signed_date': signed_date,
            'signed_time': signed_time,
            'signed_ip': ip_address,
            'generate_signed': True,
        }).encode()

        req = urllib.request.Request(gen_url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except Exception as e:
        print(f'PDF generation error: {e}')
        return build_signature_pdf(inv, signed_by, signed_date, signed_time, ip_address)

def build_signature_pdf(inv, signed_by, signed_date, signed_time, ip_address, sig_image=None, sig_mode='typed'):
    """Build a signed-contract PDF using reportlab, with real signature image embedded."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
        from reportlab.lib.colors import HexColor
        from reportlab.lib.utils import ImageReader
        import io, base64

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        ink = HexColor('#2c2926')
        muted = HexColor('#8c847c')
        green = HexColor('#6f8a72')
        line_color = HexColor('#e7e1d9')
        bg_light = HexColor('#f6f4f1')

        d = inv.get('data', {})
        client = inv.get('client', '')
        address = inv.get('address', '')
        invnum = inv.get('invnum', '')

        # Header bar
        c.setFillColor(HexColor('#2c2926'))
        c.rect(0, h-72, w, 72, fill=1, stroke=0)
        c.setFillColor(HexColor('#f0ebe3'))
        c.setFont('Helvetica', 7)
        c.drawString(0.75*inch, h-20, 'STYLING WITH JAS')
        c.setFont('Helvetica-Bold', 10)
        c.drawString(0.75*inch, h-36, 'SIGNED STAGING AGREEMENT')
        c.setFillColor(HexColor('#b89a63'))
        c.setFont('Helvetica', 9)
        c.drawRightString(w-0.75*inch, h-36, f'#{invnum}')

        # Client + address
        y = h - 92
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 13)
        c.drawString(0.75*inch, y, client)
        y -= 16
        c.setFillColor(muted)
        c.setFont('Helvetica', 9)
        c.drawString(0.75*inch, y, address)
        y -= 24

        def draw_rule(ypos, weight=0.4, color=line_color):
            c.setStrokeColor(color)
            c.setLineWidth(weight)
            c.line(0.75*inch, ypos, w-0.75*inch, ypos)

        draw_rule(y); y -= 18

        # Signature block header
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(0.75*inch, y, 'Client Signature')
        y -= 14

        # Embed signature image if provided
        sig_img_height = 70
        if sig_image:
            try:
                # Strip data URL prefix
                if ',' in sig_image:
                    sig_data = sig_image.split(',', 1)[1]
                else:
                    sig_data = sig_image
                img_bytes = base64.b64decode(sig_data)
                img_buf = io.BytesIO(img_bytes)
                img_reader = ImageReader(img_buf)
                # Draw signature image
                sig_box_w = 3.5*inch
                c.drawImage(img_reader, 0.75*inch, y - sig_img_height,
                           width=sig_box_w, height=sig_img_height,
                           preserveAspectRatio=True, anchor='sw')
            except Exception as e:
                print(f'Sig image embed error: {e}')
                # Fallback: draw name in italic
                c.setFillColor(HexColor('#1a1816'))
                c.setFont('Helvetica-Oblique', 28)
                c.drawString(0.75*inch, y - 40, signed_by)
        else:
            # No image — render name in italic as fallback
            c.setFillColor(HexColor('#1a1816'))
            c.setFont('Helvetica-Oblique', 28)
            c.drawString(0.75*inch, y - 40, signed_by)

        # Signature line under image
        y -= sig_img_height + 4
        draw_rule(y)
        y -= 14

        # Signed by + date side by side
        c.setFillColor(muted)
        c.setFont('Helvetica', 8)
        c.drawString(0.75*inch, y, f'Signed by: {signed_by}')
        c.drawString(3.5*inch, y, f'Date: {signed_date}')
        y -= 28

        draw_rule(y); y -= 16

        # Electronic record details
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(0.75*inch, y, 'Electronic Signature Record')
        y -= 14

        record_rows = [
            ('Signed by', signed_by),
            ('Date', signed_date),
            ('Time', f'{signed_time} PST'),
            ('IP Address', ip_address),
            ('Invoice', f'#{invnum}'),
            ('Method', f'{"Drawn" if sig_mode=="drawn" else "Typed"} signature via stylingwithjas.com'),
            ('Legal basis', 'ESIGN Act (15 U.S.C. § 7001)'),
        ]
        for lbl, val in record_rows:
            c.setFillColor(muted)
            c.setFont('Helvetica', 8)
            c.drawString(0.75*inch, y, lbl)
            c.setFillColor(ink)
            c.setFont('Helvetica', 8)
            c.drawString(2.5*inch, y, str(val))
            y -= 14

        y -= 8
        draw_rule(y); y -= 16

        # Agreement text
        c.setFillColor(muted)
        c.setFont('Helvetica', 7.5)
        agreement = (
            f'By signing above, {signed_by} agrees to the staging services proposal issued by '
            f'Styling With Jas (Lux Ventures LLC). This electronic signature is legally binding '
            f'under the Electronic Signatures in Global and National Commerce Act (ESIGN Act, '
            f'15 U.S.C. § 7001 et seq.).'
        )
        words = agreement.split()
        line_buf = []
        for word in words:
            line_buf.append(word)
            if c.stringWidth(' '.join(line_buf), 'Helvetica', 7.5) > (w - 1.5*inch):
                c.drawString(0.75*inch, y, ' '.join(line_buf[:-1]))
                y -= 12
                line_buf = [word]
        if line_buf:
            c.drawString(0.75*inch, y, ' '.join(line_buf))
            y -= 20

        # Confirmed badge
        c.setFillColor(green)
        c.roundRect(0.75*inch, y-24, 2.4*inch, 22, 6, fill=1, stroke=0)
        c.setFillColor(HexColor('#ffffff'))
        c.setFont('Helvetica-Bold', 9)
        c.drawString(0.75*inch + 12, y-9, '✓  APPROVED & SIGNED')

        c.save()
        return buf.getvalue()
    except Exception as e:
        print(f'reportlab PDF error: {e}')
        return None
    """Build a minimal signed-contract PDF using reportlab."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
        from reportlab.lib.colors import HexColor
        import io

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        ink = HexColor('#2c2926')
        muted = HexColor('#8c847c')
        green = HexColor('#6f8a72')
        line_color = HexColor('#e7e1d9')

        d = inv.get('data', {})
        client = inv.get('client', '')
        address = inv.get('address', '')
        invnum = inv.get('invnum', '')

        # Header bar
        c.setFillColor(HexColor('#2c2926'))
        c.rect(0, h-72, w, 72, fill=1, stroke=0)
        c.setFillColor(HexColor('#f0ebe3'))
        c.setFont('Helvetica', 8)
        c.drawString(0.75*inch, h-22, 'STYLING WITH JAS')
        c.setFont('Helvetica', 9)
        c.drawString(0.75*inch, h-38, 'ELECTRONIC SIGNATURE RECORD')
        c.setFillColor(HexColor('#b89a63'))
        c.setFont('Helvetica', 9)
        c.drawRightString(w-0.75*inch, h-38, f'#{invnum}')

        # Body
        y = h - 100
        def line_sep(ypos):
            c.setStrokeColor(line_color)
            c.line(0.75*inch, ypos, w-0.75*inch, ypos)

        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 13)
        c.drawString(0.75*inch, y, 'Signed Staging Agreement')
        y -= 18

        c.setFillColor(muted)
        c.setFont('Helvetica', 9)
        c.drawString(0.75*inch, y, f'{client}  ·  {address}')
        y -= 28
        line_sep(y); y -= 18

        # Signature details
        rows = [
            ('Signed by', signed_by),
            ('Date', signed_date),
            ('Time', signed_time + ' PST'),
            ('IP Address', ip_address),
            ('Invoice', f'#{invnum}'),
            ('Method', 'Electronic signature via stylingwithjas.com'),
            ('Legal basis', 'ESIGN Act (15 U.S.C. § 7001)'),
        ]
        for lbl, val in rows:
            c.setFillColor(muted)
            c.setFont('Helvetica', 9)
            c.drawString(0.75*inch, y, lbl)
            c.setFillColor(ink)
            c.setFont('Helvetica-Bold', 9)
            c.drawString(2.5*inch, y, str(val))
            y -= 18

        y -= 10
        line_sep(y); y -= 20

        # Agreement text
        c.setFillColor(muted)
        c.setFont('Helvetica', 8)
        agreement = (
            f'By signing above, {signed_by} agrees to the staging services proposal issued by Styling With Jas '
            f'(Lux Ventures LLC). This electronic signature is legally binding under the ESIGN Act.'
        )
        # Word wrap
        words = agreement.split()
        line_buf = []
        for word in words:
            line_buf.append(word)
            if c.stringWidth(' '.join(line_buf), 'Helvetica', 8) > (w - 1.5*inch):
                c.drawString(0.75*inch, y, ' '.join(line_buf[:-1]))
                y -= 13
                line_buf = [word]
        if line_buf:
            c.drawString(0.75*inch, y, ' '.join(line_buf))
            y -= 20

        # Green confirmation badge
        c.setFillColor(green)
        c.roundRect(0.75*inch, y-24, 2.2*inch, 22, 6, fill=1, stroke=0)
        c.setFillColor(HexColor('#ffffff'))
        c.setFont('Helvetica-Bold', 9)
        c.drawString(0.75*inch + 0.15*inch, y-8, '✓  APPROVED & SIGNED')

        c.save()
        return buf.getvalue()
    except Exception as e:
        print(f'reportlab PDF error: {e}')
        return None

def send_signed_email(to_email, client_name, invnum, pdf_bytes, contract_url, jasmine=False):
    """Email the signed contract PDF to client and Jasmine."""
    try:
        password = os.environ.get('EMAIL_PASSWORD', '')
        if not password:
            return False

        msg = MIMEMultipart()
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = f'Signed Staging Agreement — #{invnum}' if jasmine else f'Your Signed Staging Agreement — #{invnum}'

        if jasmine:
            body = (
                f'A staging proposal has been approved and signed.\n\n'
                f'Client: {client_name}\n'
                f'Invoice: #{invnum}\n\n'
                f'The signed contract is attached and stored in Supabase Storage.\n'
                f'The project has been moved to Confirmed on your Board.\n\n'
                f'— SWJ System'
            )
        else:
            body = (
                f'Hi {client_name.split()[0]},\n\n'
                f'Thank you for approving your staging proposal with Styling With Jas.\n\n'
                f'Your signed contract (#{invnum}) is attached to this email for your records.\n\n'
                f'Next step: Payment of the invoice total is due 48 hours before your staging date.\n\n'
                f'Questions? Call or text Jasmine at 206-422-5618.\n\n'
                f'We look forward to staging your home beautifully.\n\n'
                f'— Jasmine Santana\n'
                f'Styling With Jas\n'
                f'206-422-5618 · info@stylingwithjas.com'
            )

        msg.attach(MIMEText(body, 'plain'))

        if pdf_bytes:
            part = MIMEBase('application', 'pdf')
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{invnum}-signed-contract.pdf"')
            msg.attach(part)

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(FROM_EMAIL, password)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f'Email error to {to_email}: {e}')
        return False


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

        invnum = body.get('invnum', '').strip()
        signed_by = body.get('signed_by', '').strip()
        signed_date = body.get('signed_date', '').strip()
        agreed = body.get('agreed', False)
        sig_image = body.get('sig_image', None)   # base64 PNG data URL
        sig_mode = body.get('sig_mode', 'typed')  # 'drawn' or 'typed'

        # Validate
        if not invnum:
            self._respond(400, {'error': 'Missing invnum'})
            return
        if not signed_by:
            self._respond(400, {'error': 'Missing signature name'})
            return
        if not signed_date:
            self._respond(400, {'error': 'Missing signed date'})
            return
        if not agreed:
            self._respond(400, {'error': 'Agreement not confirmed'})
            return

        # Get IP
        ip_address = (
            self.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
            self.headers.get('X-Real-IP', '') or
            self.client_address[0]
        )
        signed_time = datetime.datetime.now().strftime('%I:%M %p')
        signed_at = datetime.datetime.now().isoformat()

        try:
            # 1. Fetch invoice
            inv = supabase_get_invoice(invnum)
            if not inv:
                self._respond(404, {'error': 'Invoice not found'})
                return

            d = inv.get('data', {})
            client = inv.get('client', signed_by)
            client_email = d.get('client_email', '')

            # 2. Generate signed PDF
            pdf_bytes = build_signature_pdf(inv, signed_by, signed_date, signed_time, ip_address, sig_image=sig_image, sig_mode=sig_mode)

            # 3. Upload to Supabase Storage
            contract_url = None
            if pdf_bytes:
                contract_url = supabase_upload_pdf(invnum, pdf_bytes)

            # 4. Update invoice in Supabase
            updated_data = {
                **d,
                'signed': True,
                'signed_by': signed_by,
                'signed_date': signed_date,
                'signed_time': signed_time,
                'signed_ip': ip_address,
                'signed_at': signed_at,
                'contract_url': contract_url,
                'stage': 'confirmed',  # Move to confirmed on Board
            }
            supabase_update_invoice(invnum, {
                'data': updated_data,
                'client': inv.get('client', ''),
                'address': inv.get('address', ''),
                'invdate': inv.get('invdate', ''),
            })

            # 5. Email Jasmine
            send_signed_email(
                JASMINE_EMAIL, client, invnum,
                pdf_bytes, contract_url, jasmine=True
            )

            # 6. Email client if we have their email
            if client_email:
                send_signed_email(
                    client_email, client, invnum,
                    pdf_bytes, contract_url, jasmine=False
                )

            self._respond(200, {
                'signed': True,
                'invnum': invnum,
                'contract_url': contract_url,
                'stage': 'confirmed',
            })

        except Exception as e:
            print(f'Sign error: {e}')
            self._respond(500, {'error': f'Signing failed: {str(e)}'})

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
