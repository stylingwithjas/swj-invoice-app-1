"""Narrow client/crew endpoints; never accept a table, recipient or invoice patch."""
import datetime
import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from lib import admin_auth, private_links
from lib.admin_http import response
from lib.owner_guard import BASE_URL, require_owner

# Only information rendered by the client proposal page. Operational notes,
# wages, costs, account details, signature images/IPs and credentials stay private.
PROPOSAL_FIELDS = set('active_client_payment_id address amount_paid base_price baserooms client extrate paid payment_url pet_deposit price_led_tax rental_service_split rental_share_base rental_total service_total signed signed_by signed_date startdate subtotal_override tax_override taxrate tier total_override voided'.split())
CREW = {
    'mover1':('startdate','truck_pickup_time','mover_name','Staging Crew'),
    'mover2':('startdate','truck_pickup_time','mover2_name','Staging Crew'),
    'dmover':('destage_date','destage_time','destage_mover','Destage Crew'),
    'photographer':('photo_date',None,'photographer_name','Photography'),
}


def project(data, keys):
    return {k:data[k] for k in keys if k in data}


def proposal_dto(row):
    data = row.get('data') or {}
    result = project(data, PROPOSAL_FIELDS)
    result['cosigner'] = project(data.get('cosigner') or {}, {'name','email','role','joint'})
    result['signatures'] = [project(s, {'role','signed_by','signed_date'}) for s in data.get('signatures') or []]
    result['payments'] = [project(p, {'id','status','payment_url','amount','label'}) for p in data.get('payments') or []]
    result['addons'] = [project(a, {'desc','price','type','split'}) for a in data.get('addons') or []]
    result['base_split'] = project(data.get('base_split') or {}, {'rental','service'})
    # Room scopes are client-facing descriptions, not operational job notes.
    result['baseroom_scopes'] = data.get('baseroom_scopes') or {}
    return {**project(row, {'invnum','client','address','invdate'}), 'data':result}


def crew_dto(row, role):
    date, time, name, _ = CREW[role]
    data = row.get('data') or {}
    result = project(data, {date, name, 'address'} | ({time} if time else set()))
    confirmation = (data.get('confirmations') or {}).get(role) or {}
    result['confirmations'] = {role:project(confirmation, {'status','name','confirmed_at'})}
    return {**project(row, {'invnum','address'}), 'data':result}


def db(method, query, body=None):
    key = os.environ.get('SUPABASE_KEY', '')
    req = urllib.request.Request(BASE_URL+'/rest/v1/invoices?'+urllib.parse.urlencode(query),
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={'apikey':key,'Authorization':'Bearer '+key,'Content-Type':'application/json','Prefer':'return=representation'})
    with urllib.request.urlopen(req, timeout=15) as upstream:
        return json.loads(upstream.read())


def notify_crew(row, role, status):
    """Existing notification, fixed recipient and server-derived content only."""
    date, _, name_field, label = CREW[role]
    data = row.get('data') or {}
    name = data.get(name_field) or 'Someone'
    address = row.get('address') or data.get('address') or ''
    verb = 'confirmed availability for' if status == 'confirmed' else 'is NOT available for'
    sender = 'info@stylingwithjas.com'
    message = MIMEText(f'{name} {verb} {label.lower()} at {address} on {data.get(date) or "Date TBD"}.\n\nInvoice #{row["invnum"]}.', 'plain', 'utf-8')
    message['From'] = sender
    message['To'] = sender
    message['Subject'] = f'{label} — {name} {status}'
    with smtplib.SMTP_SSL('smtp.hostinger.com',465,timeout=20) as server:
        server.login(sender,os.environ.get('EMAIL_PASSWORD',''))
        server.sendmail(sender,[sender],message.as_string())


def handle(handler, method):
    try:
        if method != 'POST':
            response(handler,405,{'error':'Use POST.'}); return
        if not admin_auth.same_origin(handler, required=True):
            response(handler,403,{'error':'Open this link on the SWJ website.'}); return
        action = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query).get('public',[''])[0]
        if action == 'mint' and not require_owner(handler):
            return
        length = int(handler.headers.get('Content-Length','0'))
        if not 0 < length <= 8192:
            response(handler,400,{'error':'Invalid request.'}); return
        body = json.loads(handler.rfile.read(length))
        invnum = body.get('invnum','')
        role = body.get('role') or 'client'
        kind = body.get('kind','proposal') if action == 'mint' else ('proposal' if action == 'invoice' else 'confirm')
        if not private_links.valid_target(invnum,role,kind):
            response(handler,403,{'error':'Please use the private link sent by Jasmine.'}); return
        origin = admin_auth.origin(handler)
        if action == 'mint':
            response(handler,200,{'url':private_links.link(origin,invnum,role,kind)}); return
        if action not in {'invoice','confirm','respond'}:
            response(handler,404,{'error':'Unknown action.'}); return
        allowed = private_links.verify(body.get('token'),invnum,role,kind,origin)
        # Jasmine can still view invoices from the Archive without a client link.
        if not allowed and action == 'invoice':
            allowed = admin_auth.authorized(handler,base_url=BASE_URL,key=os.environ.get('SUPABASE_KEY',''))
        if not allowed:
            response(handler,403,{'error':'Please ask Jasmine for a new private approval link.'}); return
        rows = db('GET', {'invnum':'eq.'+invnum,'select':'*','limit':1})
        if not rows:
            response(handler,404,{'error':'Invoice not found.'}); return
        row = rows[0]
        if action == 'invoice':
            response(handler,200,{'invoice':proposal_dto(row)}); return
        if action == 'confirm':
            response(handler,200,{'invoice':crew_dto(row,role)}); return
        status = body.get('status')
        if status not in {'confirmed','declined'}:
            response(handler,400,{'error':'Invalid response.'}); return
        data = row.get('data') or {}
        previous = (data.get('confirmations') or {}).get(role) or {}
        if previous.get('status'):
            response(handler,200,{'invoice':crew_dto(row,role)}); return
        # Optimistic comparison prevents replacing concurrent job edits. A conflict
        # asks for a retry; no caller-supplied financial/operational fields are used.
        updated = {**data,'confirmations':{**(data.get('confirmations') or {}),role:{
            'status':status,'name':data.get(CREW[role][2]) or '',
            'confirmed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}}}
        saved = db('PATCH',{'invnum':'eq.'+invnum,'data':'eq.'+json.dumps(data,separators=(',',':'))},{'data':updated})
        if len(saved) != 1:
            response(handler,409,{'error':'This job changed. Reload the page and try again.'}); return
        notified = True
        try:
            notify_crew(saved[0],role,status)
        except Exception:
            notified = False  # Preserve the reply if mail is unavailable.
        response(handler,200,{'invoice':crew_dto(saved[0],role),'notified':notified})
    except (ValueError, TypeError, AttributeError):
        response(handler,400,{'error':'Invalid request.'})
    except Exception:
        response(handler,503,{'error':'Could not complete this request. Please try again.'})
