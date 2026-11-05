"""Cookie-authenticated PostgREST bridge, restricted to existing owner UI tables.

No URL, credential, schema or arbitrary RPC is accepted from the browser. This is
one step in the RLS cutover; employee/client access must move before anon grants
can be revoked. The service credential stays on the server.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from lib.owner_guard import BASE_URL, require_owner
from lib.admin_http import response

TABLES = {'invoices', 'tasks', 'employees', 'time_entries', 'plaid_transactions',
          'plaid_card_summary'}
METHODS = {'GET', 'HEAD', 'POST', 'PATCH', 'DELETE'}
MAX_BODY = 2 * 1024 * 1024


def handle(handler, method):
    if not require_owner(handler, write=method not in {'GET', 'HEAD'}):
        return
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    resource = query.get('data', [''])[0]
    if resource not in TABLES and resource != 'rpc/mark_extension_paid':
        response(handler, 404, {'error': 'Unknown data resource.'})
        return
    if method not in METHODS or (resource == 'plaid_card_summary' and method not in {'GET', 'HEAD'}) or (resource.startswith('rpc/') and method != 'POST'):
        response(handler, 405, {'error': 'Method not allowed.'})
        return
    params = query.get('query', [''])[0]
    # Reject embedded relationship selections: an allowed table must not be a
    # doorway to server-only tables through a service-role join.
    selections = urllib.parse.parse_qs(params).get('select', [])
    if any('(' in value or ')' in value for value in selections):
        response(handler, 400, {'error': 'Related-table selections are not supported.'})
        return
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if length < 0 or length > MAX_BODY:
            response(handler, 413, {'error': 'Request too large.'})
            return
        raw = handler.rfile.read(length) if method in {'POST', 'PATCH', 'DELETE'} else None
        if raw:
            json.loads(raw)  # Only JSON payloads, not arbitrary upstream content.
    except (ValueError, TypeError):
        response(handler, 400, {'error': 'Invalid JSON request.'})
        return
    key = os.environ.get('SUPABASE_KEY', '')
    url = BASE_URL + '/rest/v1/' + resource + ('?' + params if params else '')
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    for name in ('Prefer', 'Range', 'Range-Unit'):
        value = handler.headers.get(name)
        if value:
            headers[name] = value
    accept = handler.headers.get('Accept', '')
    if accept in ('application/vnd.pgrst.object+json', 'application/json'):
        headers['Accept'] = accept
    request = urllib.request.Request(url, data=raw or None, headers=headers, method=method)
    try:
        upstream = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        upstream = error
    except Exception:
        response(handler, 502, {'error': 'Could not reach the database. Nothing is confirmed saved.'})
        return
    with upstream:
        body = upstream.read()
        handler.send_response(upstream.code)
        handler.send_header('Cache-Control', 'no-store, private')
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        for name in ('Content-Range', 'Preference-Applied'):
            value = upstream.headers.get(name)
            if value:
                handler.send_header(name, value)
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        if method != 'HEAD':
            handler.wfile.write(body)
