import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'

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

        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            self._respond(500, {'error': 'ANTHROPIC_API_KEY not configured'})
            return

        prompt = body.get('prompt', '')
        if not prompt:
            self._respond(400, {'error': 'No prompt provided'})
            return

        try:
            payload = json.dumps({
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': prompt}]
            }).encode()

            req = urllib.request.Request(
                ANTHROPIC_API_URL,
                data=payload,
                method='POST'
            )
            req.add_header('Content-Type', 'application/json')
            req.add_header('x-api-key', api_key)
            req.add_header('anthropic-version', '2023-06-01')

            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())

            text = ''
            for block in result.get('content', []):
                if block.get('type') == 'text':
                    text += block.get('text', '')

            self._respond(200, {'text': text})

        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            self._respond(500, {'error': f'Anthropic API error {e.code}: {error_body}'})
        except Exception as e:
            self._respond(500, {'error': f'Agent failed: {str(e)}'})

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
