import json
import os
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        key = os.environ.get('STRIPE_SECRET_KEY', '')
        body = json.dumps({
            'key_set': bool(key),
            'key_length': len(key),
            'key_prefix': key[:12] + '...' if key else 'NOT SET',
            'all_env_keys': [k for k in os.environ.keys() if 'STRIPE' in k.upper()]
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
