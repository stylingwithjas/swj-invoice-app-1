import json
import os
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
FROM_NAME = 'Jasmine Santana | Styling With Jas'

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

        if not to_email:
            self._respond(400, {'error': 'Recipient email required'})
            return

        try:
            # Build email
            msg = MIMEMultipart()
            msg['From'] = f'{FROM_NAME} <{FROM_EMAIL}>'
            msg['To'] = to_email
            msg['Subject'] = subject
            msg['Reply-To'] = FROM_EMAIL
    msg['Cc'] = FROM_EMAIL  # CC Jasmine so she has a record in her inbox

            # Body
            msg.attach(MIMEText(message, 'plain'))

            # Attach PDF if provided
            if pdf_base64:
                pdf_bytes = base64.b64decode(pdf_base64)
                part = MIMEBase('application', 'pdf')
                part.set_payload(pdf_bytes)
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                msg.attach(part)

            # Send via Hostinger SMTP SSL
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
