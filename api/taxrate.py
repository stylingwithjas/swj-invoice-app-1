import json
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

# Official Washington State Dept. of Revenue address-based sales-tax rate lookup.
# We proxy it server-side so the browser makes a same-origin call (no CORS) and so
# the exact, address-level rate is used instead of an approximate ZIP table.
#
# Endpoint:  https://webgis.dor.wa.gov/webapi/AddressRates.aspx
# Params:    output=text  addr=<street>  city=<city>  zip=<5-digit>
# Text reply example:  LocationCode=2724 Rate=0.102 ResultCode=0
#   - Rate is a DECIMAL fraction (0.102 == 10.2%)
#   - ResultCode 0 = exact address match; 1/2/3 = matched via ZIP+4 / ZIP (approximate)
DOR_URL = 'https://webgis.dor.wa.gov/webapi/AddressRates.aspx'


def lookup_rate(addr, city, zipcode):
    params = urllib.parse.urlencode({
        'output': 'text',
        'addr': addr or '',
        'city': city or '',
        'zip': zipcode or '',
    })
    req = urllib.request.Request(DOR_URL + '?' + params, method='GET')
    req.add_header('User-Agent', 'SWJ-Invoice/1.0')
    with urllib.request.urlopen(req, timeout=10) as r:
        text = r.read().decode('utf-8', 'replace')

    rate_m = re.search(r'Rate=([0-9]*\.?[0-9]+)', text)
    code_m = re.search(r'ResultCode=(-?[0-9]+)', text)
    loc_m = re.search(r'LocationCode=([0-9]+)', text)

    if not rate_m:
        return {'ok': False, 'error': 'No rate in DOR response', 'raw': text[:200]}

    rate_frac = float(rate_m.group(1))
    result_code = int(code_m.group(1)) if code_m else None
    location_code = loc_m.group(1) if loc_m else None

    # A rate of 0 (or a -1/9 result code) means the address/ZIP couldn't be resolved.
    if rate_frac <= 0 or result_code in (-1, 9):
        return {'ok': False, 'error': 'Address not found by WA DOR', 'resultCode': result_code}

    rate_pct = round(rate_frac * 100, 2)
    return {
        'ok': True,
        'rate': rate_pct,                 # e.g. 10.2  (a percentage)
        'locationCode': location_code,    # WA DOR location code
        'resultCode': result_code,        # 0 = exact address, >0 = approximate (ZIP-level)
        'exact': result_code == 0,
        'source': 'WA Dept. of Revenue',
    }


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
            zipcode = re.sub(r'\D', '', q.get('zip', [''])[0] or '')[:5]

            if not zipcode and not addr:
                self._respond(400, {'ok': False, 'error': 'Provide at least a ZIP or address'})
                return

            self._respond(200, lookup_rate(addr, city, zipcode))

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
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
