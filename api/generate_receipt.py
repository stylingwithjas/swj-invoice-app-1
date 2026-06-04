"""
SWJ Payment Receipt Generator
=============================
POST /api/generate_receipt with JSON:
{
  "client": "Becky Bard",
  "address": "3602 Broadmoor Dr NE, Tacoma 98422",
  "invnum": "202100",
  "amount": 3278.45,
  "payment_date": "June 2, 2026",
  "payment_time": "2:34 PM",
  "payment_method": "Visa ending in 4242",
  "stripe_ref": "pi_xxx",
  "installation_date": "June 6, 2026"
}

Returns: PDF bytes (application/pdf)

Design matches the SWJ proposal — Cormorant Garamond aesthetic, ink + gold,
generous whitespace. Single page, designed for luxury clients.
"""

import json
import io
import os
from http.server import BaseHTTPRequestHandler
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register Parisienne font (placed next to this file in api/)
_SIGNATURE_FONT = 'Times-BoldItalic'  # fallback
try:
    _font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Parisienne-Regular.ttf')
    if os.path.exists(_font_path):
        pdfmetrics.registerFont(TTFont('Parisienne', _font_path))
        _SIGNATURE_FONT = 'Parisienne'
except Exception as e:
    print(f'Parisienne font load failed: {e}')


def build_receipt_pdf(data):
    """Build the receipt PDF and return bytes."""

    client = data.get('client', 'Client')
    address = data.get('address', '')
    invnum = data.get('invnum', '')
    amount = float(data.get('amount', 0))
    payment_date = data.get('payment_date', '')
    payment_time = data.get('payment_time', '')
    payment_method = data.get('payment_method', 'Credit Card')
    stripe_ref = data.get('stripe_ref', '')
    installation_date = data.get('installation_date', '')

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    PW, PH = letter
    ML, MR = 50, 50
    CW = PW - ML - MR

    # ── Palette ──
    INK       = colors.HexColor('#1a1814')
    WARM      = colors.HexColor('#faf8f5')
    TAUPE     = colors.HexColor('#a29c96')
    TAUPE_DK  = colors.HexColor('#7a6f69')
    GOLD      = colors.HexColor('#c9a96e')
    GOLD_DK   = colors.HexColor('#9d7a44')
    GREEN     = colors.HexColor('#4a7c50')
    GREEN_LT  = colors.HexColor('#edf4ed')
    GREEN_DK  = colors.HexColor('#2d6a0a')
    INK_SOFT  = colors.HexColor('#3d3830')

    # ── Top dark header band ──
    c.setFillColor(INK)
    c.rect(0, PH - 110, PW, 110, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont('Times-Roman', 32)
    c.drawCentredString(PW / 2, PH - 60, 'SWJ')

    c.setFont('Helvetica', 7.5)
    c.setFillColor(TAUPE)
    sub = 'STYLING WITH JAS'
    sx = PW / 2 - (c.stringWidth(sub, 'Helvetica', 7.5) + 2.4 * (len(sub) - 1)) / 2
    for ch in sub:
        c.drawString(sx, PH - 78, ch)
        sx += c.stringWidth(ch, 'Helvetica', 7.5) + 2.4

    # ── PAYMENT RECEIPT title ──
    y = PH - 175
    c.setFillColor(TAUPE)
    c.setFont('Helvetica', 9)
    title_text = 'PAYMENT RECEIPT'
    spacing = 3.5
    total_w = c.stringWidth(title_text, 'Helvetica', 9) + spacing * (len(title_text) - 1)
    sx = PW / 2 - total_w / 2
    for ch in title_text:
        c.drawString(sx, y, ch)
        sx += c.stringWidth(ch, 'Helvetica', 9) + spacing

    y -= 14
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(PW / 2 - 28, y, PW / 2 + 28, y)

    # ── Thank you headline ──
    y -= 46
    first_name = client.split()[0] if client else 'there'
    # Strip 'Estate of' prefix to avoid weirdness
    if first_name.lower() == 'estate':
        parts = client.split()
        if len(parts) > 2:
            first_name = parts[2]
    c.setFillColor(INK)
    c.setFont('Times-Roman', 30)
    c.drawCentredString(PW / 2, y, f'Thank you, {first_name}.')

    y -= 22
    c.setFont('Times-Italic', 14)
    c.setFillColor(TAUPE_DK)
    c.drawCentredString(PW / 2, y, 'Your payment has been received.')

    # ── Amount card ──
    y -= 54
    card_h = 100
    c.setFillColor(GREEN_LT)
    c.roundRect(ML + 40, y - card_h, CW - 80, card_h, 14, stroke=0, fill=1)

    c.setFillColor(GREEN)
    c.setFont('Helvetica', 9)
    amt_lbl = 'AMOUNT PAID'
    spacing = 2.5
    total_w = c.stringWidth(amt_lbl, 'Helvetica', 9) + spacing * (len(amt_lbl) - 1)
    sx = PW / 2 - total_w / 2
    for ch in amt_lbl:
        c.drawString(sx, y - 28, ch)
        sx += c.stringWidth(ch, 'Helvetica', 9) + spacing

    c.setFillColor(GREEN_DK)
    c.setFont('Times-Roman', 46)
    c.drawCentredString(PW / 2, y - 68, f'${amount:,.2f}')

    received_line = ''
    if payment_date and payment_time:
        received_line = f'Received {payment_date} at {payment_time}'
    elif payment_date:
        received_line = f'Received {payment_date}'
    if received_line:
        c.setFillColor(GREEN)
        c.setFont('Helvetica', 9)
        c.drawCentredString(PW / 2, y - 86, received_line)

    y -= card_h + 32

    # ── Detail rows ──
    def detail_row(label, value, y_pos):
        if not value:
            return False
        c.setFillColor(TAUPE)
        c.setFont('Helvetica', 8)
        sp = 1.5
        sx_local = ML + 6
        for ch in label.upper():
            c.drawString(sx_local, y_pos, ch)
            sx_local += c.stringWidth(ch, 'Helvetica', 8) + sp
        c.setFillColor(INK)
        c.setFont('Helvetica', 11)
        c.drawRightString(PW - MR - 6, y_pos, str(value))
        c.setStrokeColor(colors.HexColor('#e5e0d8'))
        c.setLineWidth(0.4)
        c.line(ML, y_pos - 9, PW - MR, y_pos - 9)
        return True

    rows = [
        ('Client', client),
        ('Property', address),
        ('Invoice', f'#{invnum}' if invnum else ''),
        ('Payment Method', payment_method),
        ('Reference', stripe_ref),
    ]
    for label, value in rows:
        if detail_row(label, value, y):
            y -= 20
    y -= 6

    # ── What happens next box (only if we have installation date) ──
    if installation_date:
        box_h = 82
        c.setFillColor(WARM)
        c.setStrokeColor(colors.HexColor('#e5e0d8'))
        c.setLineWidth(0.8)
        c.roundRect(ML, y - box_h, CW, box_h, 12, stroke=1, fill=1)

        c.setFillColor(GOLD_DK)
        c.setFont('Helvetica-Bold', 9)
        nxt = 'WHAT HAPPENS NEXT'
        sp = 2.2
        sx_local = ML + 18
        for ch in nxt:
            c.drawString(sx_local, y - 20, ch)
            sx_local += c.stringWidth(ch, 'Helvetica-Bold', 9) + sp

        c.setFillColor(INK_SOFT)
        c.setFont('Times-Roman', 11.5)
        c.drawString(ML + 18, y - 40, f'Your staging is confirmed for {installation_date}.')
        c.setFont('Times-Italic', 10.5)
        c.setFillColor(TAUPE_DK)
        c.drawString(ML + 18, y - 55, 'I\'ll be in touch as we get closer to coordinate final details,')
        c.drawString(ML + 18, y - 68, 'access, and any last-minute design preferences.')
        y -= box_h + 26
    else:
        y -= 6

    # ── Signature block ──
    c.setFillColor(INK)
    c.setFont('Times-Italic', 12)
    c.drawCentredString(PW / 2, y, 'With gratitude,')

    y -= 30
    c.setFont(_SIGNATURE_FONT, 22)
    c.setFillColor(INK)
    c.drawCentredString(PW / 2, y, 'Jasmine Santana')

    y -= 18
    c.setFont('Helvetica', 8)
    c.setFillColor(TAUPE)
    role = 'OWNER & CREATIVE DIRECTOR'
    sp = 2
    sx_local = PW / 2 - (c.stringWidth(role, 'Helvetica', 8) + sp * (len(role) - 1)) / 2
    for ch in role:
        c.drawString(sx_local, y, ch)
        sx_local += c.stringWidth(ch, 'Helvetica', 8) + sp

    # ── Dark footer ──
    c.setFillColor(INK)
    c.rect(0, 0, PW, 58, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont('Times-Roman', 14)
    c.drawCentredString(PW / 2, 36, 'SWJ')
    c.setFillColor(TAUPE_DK)
    c.setFont('Helvetica', 8)
    c.drawCentredString(PW / 2, 21, 'Jasmine Santana  \u00b7  206-422-5618  \u00b7  info@stylingwithjas.com')
    c.drawCentredString(PW / 2, 10, 'Lux Ventures LLC  \u00b7  \u00a9 2026 Styling With Jas')

    c.save()
    buf.seek(0)
    return buf.read()


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            pdf_bytes = build_receipt_pdf(body)

            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Length', len(pdf_bytes))
            self.send_header(
                'Content-Disposition',
                f'attachment; filename="SWJ_Receipt_{body.get("invnum", "receipt")}.pdf"'
            )
            self.end_headers()
            self.wfile.write(pdf_bytes)
        except Exception as e:
            err = json.dumps({'error': str(e)}).encode()
            self.send_response(500)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(err))
            self.end_headers()
            self.wfile.write(err)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
