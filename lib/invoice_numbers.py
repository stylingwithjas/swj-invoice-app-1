"""Owner-side database reservations; no counter state in a serverless /tmp file."""
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
import uuid

BASE_URL='https://cwvdfgrjlrpsdfwduwvn.supabase.co'


def signature(request_id,invnum):
    secret=os.environ.get('SUPABASE_KEY','')
    if len(secret)<20:raise ValueError('Invoice number verification is not configured.')
    key=hmac.new(secret.encode(),b'swj-invoice-number/v1',hashlib.sha256).digest()
    return hmac.new(key,(request_id+'\0'+invnum).encode(),hashlib.sha256).hexdigest()


def reserve(request_id,requested=None):
    request_id=str(uuid.UUID(str(request_id)))
    key=os.environ.get('SUPABASE_KEY','')
    req=urllib.request.Request(BASE_URL+'/rest/v1/rpc/reserve_invoice_number',
        data=json.dumps({'p_request_id':request_id,'p_requested_invnum':requested}).encode(),method='POST',
        headers={'apikey':key,'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as upstream: result=json.loads(upstream.read())
    if result.get('request_id')!=request_id or not isinstance(result.get('invnum'),str):
        raise ValueError('Invalid number reservation response.')
    return {**result,'mode':'allocated','proof':signature(request_id,result['invnum'])}


def require_number(data,saved=None,payment=False):
    metadata=data.get('invoice_number') or {}
    target=str(data.get('invnum') or '')
    base=str(metadata.get('invnum') or '')
    if not base or not (target==base or (payment and target.startswith(base+'-'))):
        raise ValueError('Reserve this invoice number before continuing.')
    if metadata.get('mode')=='existing':
        if saved is None or base.startswith('DRAFT-'):
            raise ValueError('Could not verify the existing invoice. No new number was assigned.')
        return True
    try:
        request_id=str(uuid.UUID(metadata['request_id']))
        expected=signature(request_id,base)
        if metadata.get('mode')!='allocated' or not hmac.compare_digest(expected,metadata.get('proof','')):raise ValueError()
    except (KeyError,ValueError,TypeError):
        raise ValueError('Invoice number reservation is invalid. Reserve a new number.') from None
    return True


def existing_payment_parent(invnum):
    """Keep existing Board balance/extension links, reject unreserved legacy creates."""
    from lib.invoice_protection import stored_invoice
    candidate=str(invnum or '')
    for _ in range(8):
        if not candidate:return None
        saved=stored_invoice(candidate)
        if saved is not None:return saved
        if '-' not in candidate:return None
        candidate=candidate.rsplit('-',1)[0]
    return None
