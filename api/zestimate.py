import json
import os
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

# Looks up a property's estimated value and square footage from its address.
# Uses a Zillow RapidAPI endpoint. Until RAPIDAPI_KEY is set in Vercel this
# returns {"error": ...} (HTTP 200) so the form degrades to manual entry.
# RAPIDAPI_HOST defaults to zillow-com1; change it (and the endpoint below) to
# match whichever RapidAPI subscription is used.

RAPID_HOST = os.environ.get('RAPIDAPI_HOST', 'zillow-com1.p.rapidapi.com')


def _pick(d, keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get('value') or v.get('amount')
        if v not in (None, '', 0, '0'):
            return v
    return None


def lookup(address):
    key = os.environ.get('RAPIDAPI_KEY', '')
    if not key:
        return None, 'RAPIDAPI_KEY not set'

    url = 'https://' + RAPID_HOST + '/property?' + urllib.parse.urlencode({'address': address})
    req = urllib.request.Request(url, headers={
        'X-RapidAPI-Key': key,
        'X-RapidAPI-Host': RAPID_HOST,
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
    except Exception as e:
        return None, type(e).__name__ + ': ' + str(e)

    # Defensive: response may be a dict, a list, or wrapped in {"props": [...]}.
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get('props'), list) and data['props']:
        data = data['props'][0]

    value = _pick(data, ['zestimate', 'price', 'listPrice', 'priceForHDP'])
    sqft = _pick(data, ['livingArea', 'livingAreaValue', 'finishedSqFt'])
    beds = _pick(data, ['bedrooms', 'beds'])

    if value is None and sqft is None:
        return None, 'No value or square footage in response'

    out = {'source': 'Zillow'}
    try:
        if value is not None:
            out['value'] = float(value)
    except (TypeError, ValueError):
        pass
    try:
        if sqft is not None:
            out['sqft'] = float(sqft)
    except (TypeError, ValueError):
        pass
    try:
        if beds is not None:
            out['beds'] = int(beds)
    except (TypeError, ValueError):
        pass
    return out, None


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        address = (qs.get('address', [''])[0] or '').strip()
        if not address:
            return self._json({'error': 'address required'}, 400)
        result, err = lookup(address)
        if err:
            return self._json({'error': err}, 200)
        return self._json(result, 200)

    def _json(self, obj, code):
        body = json.dumps(obj).encode()
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
