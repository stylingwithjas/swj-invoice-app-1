import json
import os
from http.server import BaseHTTPRequestHandler

COUNTER_FILE = '/tmp/swj_counter.txt'
START = 202092

def get_next():
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE) as f:
                val = int(f.read().strip())
            next_val = max(val + 1, START + 1)
        else:
            next_val = START + 1
        with open(COUNTER_FILE, 'w') as f:
            f.write(str(next_val))
        return next_val
    except:
        return START + 1

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        val = get_next()
        body = json.dumps({'value': val}).encode()
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            val = int(data.get('value', START + 1))
            with open(COUNTER_FILE, 'w') as f:
                f.write(str(val))
            resp = json.dumps({'value': val}).encode()
        except Exception as e:
            resp = json.dumps({'error': str(e)}).encode()
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
