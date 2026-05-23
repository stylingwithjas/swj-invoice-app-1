import json
import os
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler

# Bonney Lake, WA — center point for distance searches
BONNEY_LAKE_ZIP = '98391'
SEARCH_RADIUS_MILES = 75

# Atom namespace used by Craigslist feeds
NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'rss': 'http://purl.org/rss/1.0/',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'cl': 'http://www.craigslist.org/about/sites',
}


def search_craigslist(query, max_price=None, min_price=None):
    """Search Craigslist (Seattle/Tacoma region) via the Atom feed.

    Craigslist's RSS feeds became unreliable in late 2024 — Atom feeds work better.
    We try Atom first, then fall back to HTML parsing if Atom returns no results.
    """
    results = []
    params = {
        'query': query,
        'searchNearby': '2',  # 2 = include surrounding areas
        'postal': BONNEY_LAKE_ZIP,
        'search_distance': str(SEARCH_RADIUS_MILES),
        'sort': 'rel',
        'format': 'rss',
    }
    if max_price:
        params['max_price'] = str(int(max_price))
    if min_price:
        params['min_price'] = str(int(min_price))

    # Search the "for sale" category (sss). Try Tacoma site for closer matches.
    sites = ['seattle.craigslist.org', 'tacoma.craigslist.org']
    
    for site in sites:
        url = f"https://{site}/search/sss?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
                'Accept-Language': 'en-US,en;q=0.9',
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read()
            
            site_results = _parse_feed(content, query)
            if site_results:
                results.extend(site_results)
                # If we got results from Seattle (covers Tacoma), don't double-search
                break
        except urllib.error.HTTPError as e:
            print(f'CL http error {e.code} from {site} for "{query}": {e.reason}')
            continue
        except Exception as e:
            print(f'CL error from {site} for "{query}": {e}')
            continue

    # Dedupe by link
    seen = set()
    unique = []
    for r in results:
        if r['link'] not in seen:
            seen.add(r['link'])
            unique.append(r)
    return unique[:8]


def _parse_feed(content, query):
    """Try to parse content as Atom or RSS feed. Returns list of result dicts."""
    results = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        print(f'XML parse error: {e}')
        return results

    # Atom feed: <feed xmlns="http://www.w3.org/2005/Atom"><entry>...</entry></feed>
    if root.tag.endswith('}feed') or root.tag == 'feed':
        entries = root.findall('.//{http://www.w3.org/2005/Atom}entry') or root.findall('.//entry')
        for entry in entries:
            r = _extract_atom_entry(entry, query)
            if r:
                results.append(r)
    # RSS feed: <rss><channel><item>...</item></channel></rss>
    elif root.tag in ('rss', 'rdf:RDF') or root.tag.endswith('}RDF'):
        items = root.findall('.//item')
        for item in items:
            r = _extract_rss_item(item, query)
            if r:
                results.append(r)
    return results


def _atom_text(element, tag):
    """Get text from an Atom child element, handling both with and without namespace."""
    el = element.find(f'{{http://www.w3.org/2005/Atom}}{tag}')
    if el is None:
        el = element.find(tag)
    return (el.text or '').strip() if el is not None else ''


def _extract_atom_entry(entry, query):
    """Extract listing data from an Atom <entry>."""
    title = _atom_text(entry, 'title')
    # Atom uses <link href="..."/>
    link_el = entry.find('{http://www.w3.org/2005/Atom}link') or entry.find('link')
    link = link_el.attrib.get('href', '') if link_el is not None else ''
    summary = _atom_text(entry, 'summary') or _atom_text(entry, 'content')
    published = _atom_text(entry, 'published') or _atom_text(entry, 'updated')

    # Price + location parsing — Craigslist sometimes includes them in <enc:enclosure>
    # or in the title. The price is in a separate <enc:price> element sometimes.
    # We extract from title as the most reliable fallback.
    return _build_result(title, link, summary, published, query)


def _extract_rss_item(item, query):
    """Extract listing data from an RSS <item>."""
    title = (item.findtext('title') or '').strip()
    link = (item.findtext('link') or '').strip()
    desc = (item.findtext('description') or '').strip()
    pub = (item.findtext('pubDate') or '').strip()
    return _build_result(title, link, desc, pub, query)


def _build_result(title, link, description, posted, query):
    """Parse title/description for price, location, image — return result dict."""
    if not title or not link:
        return None

    # Price extraction — try title first, then description
    price = None
    price_match = re.search(r'\$([\d,]+)', title)
    if not price_match:
        price_match = re.search(r'\$([\d,]+)', description)
    if price_match:
        try:
            price = int(price_match.group(1).replace(',', ''))
        except ValueError:
            pass

    # Location extraction — usually "(City Name)" at end of title
    location = ''
    loc_match = re.search(r'\(([^)]+)\)\s*$', title)
    if loc_match:
        location = loc_match.group(1).strip()

    # Clean title — remove price and trailing location
    clean = re.sub(r'\$[\d,]+', '', title).strip()
    clean = re.sub(r'\([^)]+\)\s*$', '', clean).strip()
    clean = re.sub(r'\s+', ' ', clean)

    # Image extraction
    image = ''
    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description)
    if img_match:
        image = img_match.group(1)

    return {
        'title': clean or title,
        'price': price,
        'location': location,
        'link': link,
        'image': image,
        'posted': posted,
        'source': 'Craigslist',
        'query': query,
    }


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
        errors = []

        for term in search_terms[:8]:
            query = term.get('query', '').strip()
            if not query:
                continue
            try:
                items = search_craigslist(
                    query,
                    max_price=term.get('max_price'),
                    min_price=term.get('min_price'),
                )
                for r in items:
                    if r['link'] not in seen_links:
                        seen_links.add(r['link'])
                        r['search_term'] = term.get('label', query)
                        r['job'] = term.get('job', '')
                        r['room'] = term.get('room', '')
                        all_results.append(r)
            except Exception as e:
                errors.append(f'{query}: {str(e)}')

        # Sort by price (lowest first), nulls last
        all_results.sort(key=lambda x: (x['price'] is None, x['price'] or 999999))

        self._respond(200, {
            'results': all_results,
            'total': len(all_results),
            'terms_searched': len(search_terms),
            'errors': errors,  # surface errors so the UI can show them
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
