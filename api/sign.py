import json
import os
import urllib.request
import urllib.parse
import datetime
import smtplib
import base64
import io
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
    if not key:
        raise Exception('SUPABASE_KEY env var is missing or empty')
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}&select=*',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        raise Exception(f'[get_invoice] HTTP {e.code}: {err_body[:200]}')
    return rows[0] if rows else None


def supabase_update_invoice(invnum, updates):
    key = get_supabase_key()
    if not key:
        raise Exception('SUPABASE_KEY env var is missing or empty')
    body = json.dumps(updates).encode()
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/invoices?invnum=eq.{urllib.parse.quote(invnum)}',
        data=body, method='PATCH'
    )
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Prefer', 'return=minimal')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        raise Exception(f'[update_invoice] HTTP {e.code}: {err_body[:200]}')


def supabase_upload_pdf(invnum, pdf_bytes):
    """Upload signed PDF to Supabase Storage bucket 'signed-contracts'."""
    key = get_supabase_key()
    if not key:
        print('[upload_pdf] SUPABASE_KEY missing')
        return None
    filename = f'{invnum}-signed.pdf'
    url = f'{SUPABASE_URL}/storage/v1/object/signed-contracts/{filename}'
    req = urllib.request.Request(url, data=pdf_bytes, method='POST')
    req.add_header('apikey', key)
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', 'application/pdf')
    req.add_header('x-upsert', 'true')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            json.loads(r.read())
        return f'{SUPABASE_URL}/storage/v1/object/public/signed-contracts/{filename}'
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        print(f'[upload_pdf] HTTP {e.code}: {err_body[:300]}')
        return None
    except Exception as e:
        print(f'[upload_pdf] error: {e}')
        return None


def flatten_signature_image(sig_image_b64):
    """Decode base64 PNG and flatten any transparency onto white background.
    Returns PNG bytes ready to embed in PDF."""
    if ',' in sig_image_b64:
        sig_image_b64 = sig_image_b64.split(',', 1)[1]
    img_bytes = base64.b64decode(sig_image_b64)
    try:
        from PIL import Image
        pil_img = Image.open(io.BytesIO(img_bytes))
        if pil_img.mode in ('RGBA', 'LA') or (pil_img.mode == 'P' and 'transparency' in pil_img.info):
            bg = Image.new('RGB', pil_img.size, (255, 255, 255))
            if pil_img.mode != 'RGBA':
                pil_img = pil_img.convert('RGBA')
            bg.paste(pil_img, mask=pil_img.split()[3])
            pil_img = bg
        else:
            pil_img = pil_img.convert('RGB')
        out_buf = io.BytesIO()
        pil_img.save(out_buf, format='PNG')
        return out_buf.getvalue()
    except ImportError:
        return img_bytes


def build_full_signed_contract(inv, signed_by, signed_date, signed_time, ip_address,
                                sig_image_b64=None, sig_mode='drawn'):
    """Generate the full proposal PDF and stamp the client signature + record onto page 3.
    
    Strategy:
    1. Call generate_invoice_pdf() to build the full 3-page proposal
    2. Open it with PyMuPDF and stamp the signature line + electronic record on page 3
    3. Return the modified PDF as bytes
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print('[sign] PyMuPDF not available — falling back to standalone PDF')
        return build_standalone_record(inv, signed_by, signed_date, signed_time, ip_address,
                                       sig_image_b64, sig_mode)

    # Step 1: Build the full invoice PDF
    invoice_pdf_path = None
    try:
        # Import generate locally to avoid module load issues
        # generate.py expects data dict matching the format saved in Supabase
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from generate import generate_invoice_pdf
        except ImportError as e:
            print(f'[sign] could not import generate: {e}')
            return build_standalone_record(inv, signed_by, signed_date, signed_time, ip_address,
                                           sig_image_b64, sig_mode)

        d = inv.get('data', {})
        # Reconstruct the data payload generate.py expects
        data_payload = {
            **d,
            'invnum': inv.get('invnum', ''),
            'client': inv.get('client', ''),
            'address': inv.get('address', ''),
            'invdate': inv.get('invdate', ''),
        }
        invoice_pdf_path = generate_invoice_pdf(data_payload)

        if not invoice_pdf_path or not os.path.exists(invoice_pdf_path):
            print('[sign] generate_invoice_pdf did not return a valid path')
            return build_standalone_record(inv, signed_by, signed_date, signed_time, ip_address,
                                           sig_image_b64, sig_mode)

        # Step 2: Open the generated PDF
        doc = fitz.open(invoice_pdf_path)

        # Step 3: Stamp client signature, name, date on page 3 (terms page)
        # Locate the last page (which has the signature lines)
        # The terms page is page 3 (index 2) for invoices with cover page, page 2 (index 1) without
        # Look for "Client Signature:" on each page
        target_page_idx = None
        for i in range(len(doc)):
            if doc[i].search_for("Client Signature:"):
                target_page_idx = i
                break

        if target_page_idx is None:
            print('[sign] could not find Client Signature: in any page')
            doc.close()
            return build_standalone_record(inv, signed_by, signed_date, signed_time, ip_address,
                                           sig_image_b64, sig_mode)

        page = doc[target_page_idx]

        # Stamp the client signature image on the signature line
        # Line is at x=135 to x=332, y=621 (PDF coords, bottom of "Client Signature" label)
        # We want the sig to sit ABOVE that line — y_top of image ~590, y_bottom ~620
        if sig_image_b64:
            try:
                sig_bytes = flatten_signature_image(sig_image_b64)
                sig_rect = fitz.Rect(135, 588, 332, 620)
                page.insert_image(sig_rect, stream=sig_bytes, keep_proportion=True, overlay=True)
            except Exception as e:
                print(f'[sign] signature image embed error: {e}')

        # Stamp printed name on the "Printed Name:" line (x=120 to x=332, y=633)
        page.insert_text(
            fitz.Point(125, 631),
            signed_by,
            fontsize=10,
            fontname='Helvetica',
            color=(0.067, 0.067, 0.067),
        )

        # Stamp date on the "Date:" line (x=75 to x=332, y=645)
        # Format date as MM/DD/YY to match other dates in the doc
        try:
            dt = datetime.datetime.strptime(signed_date, '%Y-%m-%d')
            date_formatted = dt.strftime('%m/%d/%y')
        except Exception:
            date_formatted = signed_date
        page.insert_text(
            fitz.Point(80, 643),
            date_formatted,
            fontsize=10,
            fontname='Helvetica',
            color=(0.067, 0.067, 0.067),
        )

        # Step 4: Append electronic signature record as a NEW page
        # This keeps the existing 3 pages untouched and gives the audit trail its own page
        new_page = doc.new_page(width=612, height=792)  # letter size

        # Header bar
        new_page.draw_rect(fitz.Rect(0, 0, 612, 72), color=None, fill=(0.173, 0.161, 0.149))
        new_page.insert_text(
            fitz.Point(54, 28),
            'STYLING WITH JAS',
            fontsize=7,
            fontname='Helvetica',
            color=(0.941, 0.922, 0.890),
        )
        new_page.insert_text(
            fitz.Point(54, 44),
            'ELECTRONIC SIGNATURE RECORD',
            fontsize=10,
            fontname='Helvetica-Bold',
            color=(0.941, 0.922, 0.890),
        )
        new_page.insert_text(
            fitz.Point(515, 44),
            f"#{inv.get('invnum','')}",
            fontsize=9,
            fontname='Helvetica',
            color=(0.722, 0.604, 0.388),
        )

        # Body
        y = 110
        ink = (0.173, 0.161, 0.149)
        muted = (0.549, 0.518, 0.486)

        client = inv.get('client', '')
        address = inv.get('address', '')

        # Client block
        new_page.insert_text(fitz.Point(54, y), client, fontsize=14, fontname='Helvetica-Bold', color=ink)
        y += 18
        new_page.insert_text(fitz.Point(54, y), address, fontsize=9, fontname='Helvetica', color=muted)
        y += 30

        # Divider
        new_page.draw_line(fitz.Point(54, y), fitz.Point(558, y), color=(0.906, 0.882, 0.851), width=0.5)
        y += 24

        # Section header
        new_page.insert_text(fitz.Point(54, y), 'Signature Audit Trail', fontsize=11, fontname='Helvetica-Bold', color=ink)
        y += 22

        # Record rows
        rows = [
            ('Signed by', signed_by),
            ('Date', date_formatted),
            ('Time', f'{signed_time} PST'),
            ('IP Address', ip_address),
            ('Invoice', f"#{inv.get('invnum','')}"),
            ('Method', f'{"Drawn" if sig_mode=="drawn" else "Typed"} signature via stylingwithjas.com'),
            ('Legal basis', 'ESIGN Act (15 U.S.C. § 7001)'),
        ]
        for lbl, val in rows:
            new_page.insert_text(fitz.Point(54, y), lbl, fontsize=9, fontname='Helvetica', color=muted)
            new_page.insert_text(fitz.Point(180, y), str(val), fontsize=9, fontname='Helvetica-Bold', color=ink)
            y += 16

        y += 10
        new_page.draw_line(fitz.Point(54, y), fitz.Point(558, y), color=(0.906, 0.882, 0.851), width=0.5)
        y += 18

        # Legal disclaimer
        disclaimer = (
            f'By signing above, {signed_by} agrees to the staging services proposal '
            f'issued by Styling With Jas (Lux Ventures LLC). This electronic signature '
            f'is legally binding under the Electronic Signatures in Global and National '
            f'Commerce Act (ESIGN Act, 15 U.S.C. § 7001 et seq.).'
        )
        # Word-wrap manually
        words = disclaimer.split()
        line = []
        max_width = 504  # 612 - 54 - 54
        for word in words:
            test_line = ' '.join(line + [word])
            text_width = fitz.get_text_length(test_line, fontname='Helvetica', fontsize=8)
            if text_width > max_width:
                new_page.insert_text(fitz.Point(54, y), ' '.join(line), fontsize=8, fontname='Helvetica', color=muted)
                y += 12
                line = [word]
            else:
                line.append(word)
        if line:
            new_page.insert_text(fitz.Point(54, y), ' '.join(line), fontsize=8, fontname='Helvetica', color=muted)
            y += 24

        # Approved badge (green pill)
        badge_rect = fitz.Rect(54, y, 220, y + 26)
        new_page.draw_rect(badge_rect, color=None, fill=(0.435, 0.541, 0.447), radius=0.3)
        new_page.insert_text(
            fitz.Point(70, y + 17),
            '✓  APPROVED & SIGNED',
            fontsize=10,
            fontname='Helvetica-Bold',
            color=(1, 1, 1),
        )

        # Step 5: Output PDF as bytes
        pdf_bytes = doc.tobytes(garbage=4, deflate=True)
        doc.close()

        # Cleanup temp file
        try:
            os.unlink(invoice_pdf_path)
        except Exception:
            pass

        return pdf_bytes

    except Exception as e:
        print(f'[sign] full contract build failed: {e}')
        import traceback
        traceback.print_exc()
        return build_standalone_record(inv, signed_by, signed_date, signed_time, ip_address,
                                       sig_image_b64, sig_mode)


def build_standalone_record(inv, signed_by, signed_date, signed_time, ip_address,
                            sig_image_b64=None, sig_mode='drawn'):
    """Fallback: build standalone signature record PDF (old behavior).
    Only used if PyMuPDF or generate.py can't be loaded."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.colors import HexColor
        from reportlab.lib.utils import ImageReader

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        ink = HexColor('#2c2926')
        muted = HexColor('#8c847c')
        green = HexColor('#6f8a72')

        c.setFillColor(HexColor('#2c2926'))
        c.rect(0, h-72, w, 72, fill=1, stroke=0)
        c.setFillColor(HexColor('#f0ebe3'))
        c.setFont('Helvetica', 7)
        c.drawString(0.75*inch, h-20, 'STYLING WITH JAS')
        c.setFont('Helvetica-Bold', 10)
        c.drawString(0.75*inch, h-36, 'SIGNATURE RECORD (fallback)')

        y = h - 110
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 13)
        c.drawString(0.75*inch, y, inv.get('client', ''))
        y -= 30

        c.setFont('Helvetica', 9)
        c.setFillColor(muted)
        for lbl, val in [
            ('Signed by', signed_by),
            ('Date', signed_date),
            ('Time', f'{signed_time} PST'),
            ('IP', ip_address),
            ('Invoice', f"#{inv.get('invnum','')}"),
        ]:
            c.drawString(0.75*inch, y, f'{lbl}: {val}')
            y -= 14

        c.save()
        return buf.getvalue()
    except Exception as e:
        print(f'[sign] standalone fallback also failed: {e}')
        return None


def send_signed_email(to_email, client_name, invnum, pdf_bytes, contract_url, jasmine=False):
    try:
        password = os.environ.get('EMAIL_PASSWORD', '')
        if not password:
            return False

        msg = MIMEMultipart()
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = (
            f'Signed Staging Agreement — #{invnum}' if jasmine
            else f'Your Signed Staging Agreement — #{invnum}'
        )

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
                f'Hi {client_name.split()[0] if client_name else ""},\n\n'
                f'Thank you for approving your staging proposal with Styling With Jas.\n\n'
                f'Your signed contract (#{invnum}) is attached for your records.\n\n'
                f'Next step: Full payment is due 48 hours before your staging date.\n\n'
                f'Questions? Call or text Jasmine at 206-422-5618.\n\n'
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
        sig_image = body.get('sig_image', None)
        sig_mode = body.get('sig_mode', 'typed')

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

        ip_address = (
            self.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
            self.headers.get('X-Real-IP', '') or
            self.client_address[0]
        )
        signed_time = datetime.datetime.now().strftime('%I:%M %p')
        signed_at = datetime.datetime.now().isoformat()

        try:
            inv = supabase_get_invoice(invnum)
            if not inv:
                self._respond(404, {'error': 'Invoice not found'})
                return

            d = inv.get('data', {})
            client = inv.get('client', signed_by)
            client_email = d.get('client_email', '')

            # Build the full signed contract (full proposal + signature stamped on page 3)
            pdf_bytes = build_full_signed_contract(
                inv, signed_by, signed_date, signed_time, ip_address,
                sig_image_b64=sig_image, sig_mode=sig_mode
            )

            # Upload to Supabase Storage
            contract_url = None
            if pdf_bytes:
                contract_url = supabase_upload_pdf(invnum, pdf_bytes)

            # Update invoice with signature data + move to confirmed stage
            updated_data = {
                **d,
                'signed': True,
                'signed_by': signed_by,
                'signed_date': signed_date,
                'signed_time': signed_time,
                'signed_ip': ip_address,
                'signed_at': signed_at,
                'contract_url': contract_url,
                'stage': 'confirmed',
            }
            supabase_update_invoice(invnum, {
                'data': updated_data,
                'client': inv.get('client', ''),
                'address': inv.get('address', ''),
                'invdate': inv.get('invdate', ''),
            })

            # Email Jasmine
            send_signed_email(JASMINE_EMAIL, client, invnum, pdf_bytes, contract_url, jasmine=True)

            # Email client
            if client_email:
                send_signed_email(client_email, client, invnum, pdf_bytes, contract_url, jasmine=False)

            self._respond(200, {
                'signed': True,
                'invnum': invnum,
                'contract_url': contract_url,
                'stage': 'confirmed',
            })

        except Exception as e:
            print(f'Sign error: {e}')
            import traceback
            traceback.print_exc()
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
