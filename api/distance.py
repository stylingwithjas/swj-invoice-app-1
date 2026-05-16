import json
import os
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

WAREHOUSE = "9602 214th Ave E, Bonney Lake, WA 98391"

def get_distance(destination):
    key = os.environ.get('GOOGLE_MAPS_KEY', '')
    if not key:
        return None, 'Google Maps key not configured'
    
    params = urllib.parse.urlencode({
        'origins': WAREHOUSE,
        'destinations': destination,
        'units': 'imperial',
        'key': key
    })
    
    url = f'https://maps.googleapis.com/maps/api/distancematrix/json?{params}'
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        
        if data.get('status') != 'OK':
            return None, f"API error: {data.get('status')}"
        
        element = data['rows'][0]['elements'][0]
        if element.get('status') != 'OK':
            return None, f"Route error: {element.get('status')}"
        
        # Distance in miles
        meters = element['distance']['value']
        miles = round(meters * 0.000621371, 1)
        
        return miles, None
        
    except Exception as e:
        return None, str(e)

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

        address = body.get('address', '')
        if not address:
            self._respond(400, {'error': 'Address required'})
            return

        miles, error = get_distance(address)
        
        if error:
            # Fallback to ZIP-based estimate
            miles = estimate_by_zip(address)
            self._respond(200, {
                'miles': miles,
                'estimated': True,
                'note': 'ZIP-based estimate'
            })
            return

        # Calculate costs
        # 4 trips total: warehouse→site, site→warehouse (×2 for stage+destage)
        total_miles = round(miles * 4, 1)
        mileage_cost = round(total_miles * 0.27, 2)
        
        # Fuel: truck gets ~9mpg, $6/gal
        gallons = round(total_miles / 9, 1)
        fuel_cost = round(gallons * 6.00, 2)

        self._respond(200, {
            'miles_one_way': miles,
            'total_miles': total_miles,
            'mileage_cost': mileage_cost,
            'gallons': gallons,
            'fuel_cost': fuel_cost,
            'estimated': False
        })

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


def estimate_by_zip(address):
    """Fallback ZIP-based distance estimate from Bonney Lake."""
    import re
    m = re.search(r'9\d{4}', address)
    if not m:
        return 35  # default
    
    zip_code = m.group()
    
    # Distance zones from Bonney Lake 98391
    close = {'98390','98391','98387','98388','98338','98374','98375','98373','98372','98371','98354','98303','98321','98304','98328','98329','98330','98360','98396','98397','98398'}
    medium = {'98001','98002','98003','98004','98005','98006','98007','98008','98010','98011','98014','98019','98022','98023','98024','98027','98028','98029','98030','98031','98032','98033','98034','98038','98039','98040','98042','98045','98047','98050','98051','98052','98053','98055','98056','98057','98058','98059','98062','98063','98064','98065','98070','98072','98074','98075','98077','98092','98093','98101','98102','98103','98104','98105','98106','98107','98108','98109','98112','98115','98116','98117','98118','98119','98121','98122','98125','98126','98133','98134','98136','98144','98146','98148','98155','98166','98168','98177','98178','98188','98198','98199'}
    far = {'98201','98203','98204','98205','98207','98208','98270','98271','98272','98273','98274','98275','98276','98277','98278','98279','98280','98281','98282','98283','98284','98290','98291','98292','98293','98294','98295','98296','98297'}
    
    if zip_code in close:
        return 20
    elif zip_code in medium:
        return 45
    elif zip_code in far:
        return 65
    else:
        return 35
