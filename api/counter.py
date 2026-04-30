import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

COUNTER_FILE = '/tmp/swj_counter.txt'
START = 202092

def read_current():
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE) as f:
                return int(f.read().strip())
    except:
        pass
    return START

def write_value(val):
    try:
        with open(COUNTER_FILE, 'w') as f:
            f.write(str(val))
    except:
        pass

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Return current value — page will show current + 1
        current = read_current()
        self._respond(200, {'value': current})

    def do_POST(self):
        # Save whatever number was actually used
        length = int(self.headers.get('Content-Length', 0))
        try:
            body = json.loads(self.rfile.read(length))
            used_val = int(body.get('value', START))
            # Save exactly what was used — next will be used_val + 1
            write_value(used_val)
            self._respond(200, {'value': used_val})
        except Exception as e:
            self._respond(400, {'error': str(e)})

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
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
