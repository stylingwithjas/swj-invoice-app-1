import json
import os
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler

# Bonney Lake, WA — center point for distance searches
BONNEY_LAKE_ZIP = '98391'
SEARCH_RADIUS_MILES = 75

# Craigslist regions that cover Bonney Lake area
CRAIGSLIST_SITES = [
    'seattle',   # Seattle/Tacoma
    'tacoma',    # Tacoma (if separate — falls under seattle.craigslist.org)
]

# Craigslist category codes for furniture/home goods
CL_CATEGORIES = {
    'furniture': 'fua',
    'household': 'hsh',
    'general': 'foa',
}

def search_craigslist(query, max_price=None, min_price=None):
    """Search Craigslist Seattle (covers Tacoma/Bonney Lake) via RSS."""
    results = []
    params = {
        'query': query,
        'searchNearby': '1',
        'nearbyArea': '500',  # Expanded South Sound + surrounding areas
        'search_distance': str(SEARCH_RADIUS_MILES),
        'postal': BONNEY_LAKE_ZIP,
        'format': 'rss',
        'sort': 'rel',
        'srchType': 'T',
    }
    if max_price:
        params['max_price'] = str(int(max_price))
    if min_price:
        params['min_price'] = str(int(min_price))

    url = f"https://seattle.craigslist.org/search/sss?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; SWJ/1.0)',
            'Accept': 'application/rss+xml, application/xml, text/xml'
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            rss_content = r.read()

        root = ET.fromstring(rss_content)
        ns = {'cl': 'http://www.craigslist.org/about/sites'}
        
        for item in root.findall('.//item'):
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            description = item.findtext('description', '').strip()
            pub_date = item.findtext('pubDate', '').strip()
            
            # Extract price from title (usually "$XX" format)
            price = None
            import re
            price_match = re.search(r'\$[\d,]+', title)
            if price_match:
                price_str = price_match.group(0).replace('$', '').replace(',', '')
                try:
                    price = int(price_str)
                except:
                    pass
            
            # Extract location from title (usually "(location)" at end)
            location = ''
            loc_match = re.search(r'\(([^)]+)\)\s*$', title)
            if loc_match:
                location = loc_match.group(1)
            
            # Clean title
            clean_title = re.sub(r'\$[\d,]+', '', title).strip()
            clean_title = re.sub(r'\([^)]+\)\s*$', '', clean_title).strip()
            
            # Extract image if present
            image = ''
            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description)
            if img_match:
                image = img_match.group(1)
            
            if title and link:
                results.append({
                    'title': clean_title or title,
                    'price': price,
                    'location': location,
                    'link': link,
                    'image': image,
                    'posted': pub_date,
                    'source': 'Craigslist',
                    'query': query,
                })
        
        return results[:8]  # Max 8 per query to avoid overwhelming
    
    except Exception as e:
        print(f'Craigslist search error for "{query}": {e}')
        return []


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

        search_terms = body.get('terms', [])
        if not search_terms:
            self._respond(400, {'error': 'No search terms provided'})
            return

        all_results = []
        seen_links = set()

        for term in search_terms[:8]:  # Max 8 search terms per call
            query = term.get('query', '')
            max_price = term.get('max_price')
            min_price = term.get('min_price')
            
            if not query:
                continue
            
            results = search_craigslist(query, max_price=max_price, min_price=min_price)
            
            for r in results:
                if r['link'] not in seen_links:
                    seen_links.add(r['link'])
                    r['search_term'] = term.get('label', query)
                    r['job'] = term.get('job', '')
                    r['room'] = term.get('room', '')
                    all_results.append(r)

        # Sort by price (lowest first), nulls last
        all_results.sort(key=lambda x: (x['price'] is None, x['price'] or 999999))

        self._respond(200, {
            'results': all_results,
            'total': len(all_results),
            'terms_searched': len(search_terms),
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
