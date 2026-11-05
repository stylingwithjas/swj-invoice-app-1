"""Address-rate evidence, separate from invoice prices and tax-allocation formulas."""
import base64
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
import time
import xml.etree.ElementTree as ET

LIFETIME = 24 * 3600
ADDRESS_CODES = {0, 2, 4}
DESCRIPTIONS = {0:'Address found',1:'ZIP+4 only',2:'Address updated and found',3:'Updated address; ZIP+4 only',4:'Address corrected and found',5:'Five-digit ZIP only',6:'Address not found',7:'Invalid coordinates',9:'DOR internal error'}


def normalized(value):
    return ' '.join(str(value or '').strip().upper().split())


def quarter(date):
    day = dt.date.fromisoformat(date)
    return f'Q{(day.month-1)//3+1}{day.year}'


def key():
    secret = os.environ.get('SUPABASE_KEY','')
    if len(secret)<20:
        raise ValueError('Tax verification is not configured.')
    return hmac.new(secret.encode(), b'swj-tax-evidence/v1',hashlib.sha256).digest()


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def parse_response(text):
    root = ET.fromstring(text)
    rate = Decimal(root.attrib.get('rate','0'))
    code = int(root.attrib.get('code','-1'))
    loc = root.attrib.get('loccode','')
    if not rate.is_finite() or not 0 < rate < 1 or code not in DESCRIPTIONS or code in {6,7,9}:
        return {'ok':False,'resultCode':code,'error':'WA DOR could not resolve this property.'}
    address = root.find('addressline')
    place = root.find('rate')
    attrs = address.attrib if address is not None else {}
    low,high = attrs.get('houselow',''),attrs.get('househigh','')
    number = low if low == high else ('–'.join(filter(None,[low,high])))
    return {'ok':True,'rate':float((rate*100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),
        'locationCode':loc,'resultCode':code,'exact':code==0,'addressMatch':code in ADDRESS_CODES,
        'needsConfirmation':code in {2,4},'matchDescription':DESCRIPTIONS[code],
        'matchedAddress':' '.join(filter(None,[number,attrs.get('street',''),place.attrib.get('name','') if place is not None else '',attrs.get('state',''),attrs.get('zip','')])),
        'period':attrs.get('period',''),'source':'WA Dept. of Revenue'}


def attest(result, address, city, zipcode, invdate, now=None):
    if not result.get('addressMatch') or not result.get('locationCode') or not result.get('period'):
        return result
    if quarter(invdate) != result['period']:
        return {**result,'ok':False,'error':'DOR returned a different tax period. Current rates cannot verify this invoice date.'}
    stamp = int(time.time() if now is None else now)
    evidence = {k:result[k] for k in ('rate','locationCode','resultCode','period','matchedAddress','source')}
    evidence.update({'v':1,'address':address.strip(),'city':city.strip(),'zip':zipcode,'invdate':invdate,'checkedAt':stamp,'expiresAt':stamp+LIFETIME})
    payload = encode(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode())
    evidence['proof'] = payload+'.'+encode(hmac.new(key(),payload.encode(),hashlib.sha256).digest())
    return {**result,'verification':evidence}


def composed_address(address, city, zipcode):
    value = address.strip()
    if city and city.lower() not in value.lower(): value += (', ' if value else '')+city
    if zipcode and zipcode not in value: value += ' '+zipcode
    return value


def require_verified(data, now=None, payment=False):
    """Reject stale/mismatched evidence; never replace a submitted financial value."""
    evidence = data.get('tax_verification') or {}
    try:
        payload,signature=evidence['proof'].split('.')
        decode=lambda s:base64.b64decode(s+'='*(-len(s)%4),altchars=b'-_',validate=True)
        if not hmac.compare_digest(hmac.new(key(),payload.encode(),hashlib.sha256).digest(),decode(signature)):raise ValueError()
        signed=json.loads(decode(payload))
        stamp=int(time.time() if now is None else now)
        if any(evidence.get(field) != value for field,value in signed.items()):raise ValueError()
        if not signed['checkedAt'] <= stamp < signed['expiresAt'] or signed['expiresAt']-signed['checkedAt'] != LIFETIME:raise ValueError()
        if signed['v'] != 1 or signed['resultCode'] not in ADDRESS_CODES:raise ValueError()
        if signed['resultCode'] in {2,4} and evidence.get('addressConfirmed') is not True:raise ValueError()
        if quarter(data.get('invdate','')) != signed['period'] or data.get('invdate') != signed['invdate']:raise ValueError()
        if normalized(data.get('address')) != normalized(composed_address(signed['address'],signed['city'],signed['zip'])):raise ValueError()
        if normalized(data.get('city')) != normalized(signed['city']) or data.get('zip') != signed['zip']:raise ValueError()
        if Decimal(str(data.get('taxrate'))) != Decimal(str(signed['rate'])):raise ValueError()
        if not payment and 'tax' in (data.get('manual_totals_fields') or []) and data.get('tax_override') not in (None,''):
            base = data.get('rental_total') if data.get('rental_service_split') else data.get('subtotal_override')
            if base is not None:
                expected=(Decimal(str(base))*Decimal(str(signed['rate']))/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                actual=Decimal(str(data['tax_override']))
                if abs(actual-expected)>Decimal('.02'):
                    review=data.get('manual_tax_review') or {}
                    if review.get('confirmed') is not True or len(str(review.get('reason','')).strip())<10:
                        raise ValueError('Manual tax differs from verified-rate tax; record the review or correct it.')
        return True
    except (KeyError,TypeError,ValueError,ArithmeticError) as error:
        raise ValueError(str(error) if str(error).startswith('Manual tax') else 'Verify the property tax rate and matched address again before continuing.') from None
