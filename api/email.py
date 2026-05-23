import json
import os
import smtplib
import base64
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from http.server import BaseHTTPRequestHandler

SMTP_HOST = 'smtp.hostinger.com'
SMTP_PORT = 465
FROM_EMAIL = 'info@stylingwithjas.com'
FROM_NAME = 'Jasmine Santana | Styling With Jas'


def build_html_email(plain_text, proposal_link=None, client_first_name=''):
    """Convert plain text email into a polished HTML email with styled CTA button."""
    # Auto-detect proposal link in the plain text if not provided
    if not proposal_link:
        url_match = re.search(r'https?://[^\s]+proposal\.html\?inv=[^\s]+', plain_text)
        if url_match:
            proposal_link = url_match.group(0)

    body_for_html = plain_text
    if proposal_link:
        # Remove the "To approve... please visit:\n<url>" block from plain text
        body_for_html = re.sub(
            r'To approve this proposal[^\n]*\n+' + re.escape(proposal_link) + r'\n*',
            '',
            body_for_html,
            flags=re.IGNORECASE
        )
        body_for_html = re.sub(
            r'(please visit|please review)[^\n]*\n+\s*' + re.escape(proposal_link) + r'\n*',
            '',
            body_for_html,
            flags=re.IGNORECASE
        )
        # Final pass: remove bare URL if still present
        body_for_html = body_for_html.replace(proposal_link, '')

    # Convert paragraphs to HTML, skip the signature block (handled separately)
    paragraphs = [p.strip() for p in body_for_html.split('\n\n') if p.strip()]
    paragraph_html = ''
    for p in paragraphs:
        if 'Jasmine Santana' in p and ('Owner' in p or 'stylingwithjas' in p.lower()):
            continue
        p_html = p.replace('\n', '<br>')
        paragraph_html += f'<p style="margin:0 0 16px;font-size:14px;color:#3d3833;line-height:1.7;">{p_html}</p>'

    # CTA button (only if link exists)
    cta_html = ''
    if proposal_link:
        cta_html = f'''
        <div style="background:#f6f4f1;border:1px solid #e7e1d9;border-radius:11px;padding:22px 22px 20px;margin:24px 0;text-align:center;">
          <div style="font-size:11px;font-weight:600;color:#8c847c;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px;">Review your proposal</div>
          <div style="font-size:13px;color:#3d3833;margin-bottom:18px;line-height:1.6;">View the full scope and approve electronically &mdash; one tap from your phone.</div>
          <a href="{proposal_link}" style="display:inline-block;padding:14px 32px;background:#6f8a72;color:#ffffff;text-decoration:none;border-radius:9px;font-family:Georgia,'Cormorant Garamond',serif;font-size:15px;letter-spacing:2px;text-transform:uppercase;font-weight:400;">
            &#10003; Approve &amp; Sign
          </a>
          <div style="margin-top:14px;font-size:10px;color:#b8ada1;">
            Or copy this link: <a href="{proposal_link}" style="color:#7a8574;text-decoration:none;">{proposal_link}</a>
          </div>
        </div>
        '''

    signature_html = '''
    <div style="border-top:1px solid #e7e1d9;padding-top:18px;margin-top:24px;">
      <div style="font-family:Georgia,'Cormorant Garamond',serif;font-size:20px;font-weight:400;color:#2c2926;margin-bottom:4px;">Jasmine Santana</div>
      <div style="font-size:11px;color:#8c847c;line-height:1.9;">
        Owner &amp; Creative Director &mdash; Styling With Jas<br>
        <a href="tel:2064225618" style="color:#7a8574;text-decoration:none;">206-422-5618</a> &nbsp;&middot;&nbsp;
        <a href="mailto:info@stylingwithjas.com" style="color:#7a8574;text-decoration:none;">info@stylingwithjas.com</a>
      </div>
    </div>
    '''

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Styling With Jas</title>
</head>
<body style="margin:0;padding:24px 16px;background:#e8e4df;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
    <div style="background:#2c2926;padding:22px 28px;text-align:left;">
      <div style="font-family:Georgia,'Cormorant Garamond',serif;font-size:28px;font-weight:400;letter-spacing:3px;color:#f0ebe3;line-height:1;">SWJ</div>
      <div style="font-size:9px;letter-spacing:2.5px;color:#b89a63;text-transform:uppercase;margin-top:6px;">Styling With Jas</div>
    </div>
    <div style="padding:28px 28px 24px;">
      {paragraph_html}
      {cta_html}
      {signature_html}
    </div>
  </div>
  <div style="text-align:center;margin-top:14px;font-size:10px;color:#8c847c;">
    Lux Ventures LLC &nbsp;&middot;&nbsp; &copy; 2026 Styling With Jas
  </div>
</body>
</html>'''
    return html


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

        password = os.environ.get('EMAIL_PASSWORD', '')
        if not password:
            self._respond(500, {'error': 'Email password not configured'})
            return

        to_email   = body.get('to', '')
        subject    = body.get('subject', 'Your Staging Proposal — Styling With Jas')
        message    = body.get('message', '')
        pdf_base64 = body.get('pdf', '')
        filename   = body.get('filename', 'SWJ_Proposal.pdf')
        proposal_link = body.get('proposal_link', None)
        client_first_name = body.get('client_first_name', '')

        if not to_email:
            self._respond(400, {'error': 'Recipient email required'})
            return

        try:
            msg = MIMEMultipart('mixed')
            msg['From'] = f'{FROM_NAME} <{FROM_EMAIL}>'
            msg['To'] = to_email
            msg['Subject'] = subject
            msg['Reply-To'] = FROM_EMAIL
            msg['Cc'] = FROM_EMAIL

            # alternative part: plain text + HTML
            body_part = MIMEMultipart('alternative')
            body_part.attach(MIMEText(message, 'plain', 'utf-8'))
            html_content = build_html_email(message, proposal_link, client_first_name)
            body_part.attach(MIMEText(html_content, 'html', 'utf-8'))
            msg.attach(body_part)

            # PDF attachment
            if pdf_base64:
                pdf_bytes = base64.b64decode(pdf_base64)
                part = MIMEBase('application', 'pdf')
                part.set_payload(pdf_bytes)
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                msg.attach(part)

            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.login(FROM_EMAIL, password)
                server.sendmail(FROM_EMAIL, [to_email, FROM_EMAIL], msg.as_string())

            self._respond(200, {'sent': True, 'to': to_email})

        except smtplib.SMTPAuthenticationError:
            self._respond(500, {'error': 'Email authentication failed — check password'})
        except smtplib.SMTPException as e:
            self._respond(500, {'error': f'SMTP error: {str(e)}'})
        except Exception as e:
            self._respond(500, {'error': f'Send failed: {str(e)}'})

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
