import json
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from lib.tax_verification import parse_response, attest
DOR_URL = 'https://webgis.dor.wa.gov/webapi/AddressRates.aspx'


def lookup_rate(addr, city, zipcode, invdate=None):
    params = urllib.parse.urlencode({'output':'xml','addr':addr or '', 'city':city or '', 'zip':zipcode or ''})
    req = urllib.request.Request(DOR_URL+'?'+params, headers={'User-Agent':'SWJ-Invoice/1.0'})
    with urllib.request.urlopen(req,timeout=10) as upstream:
        result = parse_response(upstream.read().decode('utf-8','replace'))
    if invdate and result.get('ok'):
        result = attest(result,addr,city,zipcode,invdate)
    return result


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            qs = urllib.parse.urlparse(self.path).query
            q = urllib.parse.parse_qs(qs)
            addr = (q.get('addr', [''])[0] or '').strip()
            city = (q.get('city', [''])[0] or '').strip()
            zipcode = (q.get('zip', [''])[0] or '').strip()
            invdate = (q.get('date', [''])[0] or '').strip()
            if not re.fullmatch(r'\d{5}',zipcode) or not addr or not city or not invdate:
                self._respond(400, {'ok':False,'error':'Enter street, city, five-digit ZIP and invoice date.'})
                return

            self._respond(200, lookup_rate(addr, city, zipcode, invdate))

        except urllib.error.URLError as e:
            # Network / upstream problem — tell the client so it keeps the manual field.
            self._respond(200, {'ok': False, 'error': 'WA DOR lookup unavailable: ' + str(getattr(e, 'reason', e))})
        except Exception as e:
            self._respond(200, {'ok': False, 'error': str(e)})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
