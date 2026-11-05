"""Authenticated cash/checking API. Isolated tables, no invoice writes and no payment sends.
Plaid checking uses Transactions only; existing Amex Items are never reused or changed.
"""
import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from . import admin_auth as auth
from .cash_ledger import cents, dollars, fingerprint, iso_date, safe_label, mover_record, validate_review, snapshot, fresh, review_valid
from .section7_http import scan as scan_invoices
from .stripe_recon import parse_metadata_invoice
from .stripe_recon_http import stripe_list_all

LA = ZoneInfo('America/Los_Angeles')


class SetupRequired(Exception):
    pass


class Store:
    def __init__(self, url, key, opener=urllib.request.urlopen):
        self.url, self.key, self.opener = url, key, opener
        self.deadline = time.monotonic()+48

    def request(self, path, method='GET', body=None, headers=None):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0: raise ValueError('The complete read timed out. Retry; no complete cash result is available.')
        request = urllib.request.Request(self.url+'/rest/v1/'+path,
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={'apikey':self.key,'Authorization':'Bearer '+self.key,'Content-Type':'application/json', **(headers or {})})
        try:
            with self.opener(request, timeout=min(12,remaining)) as response:
                payload=response.read(5000001)
                if len(payload)>5000000: raise ValueError('Response limit exceeded; no complete result is available.')
                return json.loads(payload) if payload else None, response.headers
        except urllib.error.HTTPError as error:
            # Never relay database response bodies, tokens, SQL text or imported raw data.
            if error.code==404 and path=='rpc/cash_assign_bank_movers':
                raise SetupRequired('Direct mover assignment needs the checking_bank_mover_assignment.sql follow-up. No payment or review was saved.') from None
            if error.code in (404,400) and method=='GET':
                raise SetupRequired('Checking schema is incomplete. Review the applied migration and pending follow-up SQL; do not rerun the original migration.') from None
            raise ValueError('Database action was not completed. Check the migration, permissions or conflicting evidence; no success was assumed.') from None

    def rows(self, table, filters=None, select='*', order='created_at.asc'):
        result, expected = [], None
        for offset in range(0,100001,500):
            params={'select':select,'order':order,'offset':offset,'limit':500,**(filters or {})}
            batch, headers=self.request(table+'?'+urllib.parse.urlencode(params),headers={'Prefer':'count=exact'})
            count=str(headers.get('Content-Range','')).rsplit('/',1)[-1]
            if not count.isdigit() or not isinstance(batch,list): raise ValueError('Database coverage is unverified. No complete result is available.')
            if expected is None: expected=int(count)
            if expected!=int(count) or expected>100000: raise ValueError('Records changed during the read, or exceeded the scan limit. Retry.')
            result.extend(batch)
            if len(result)==expected: return result
            if len(batch)<500: raise ValueError('Database scan was incomplete. Retry.')
        raise ValueError('Scan limit exceeded; no complete result is available.')

    def put(self, table, rows, conflict):
        return self.request(table+'?'+urllib.parse.urlencode({'on_conflict':conflict}), 'POST', rows,
                            {'Prefer':'resolution=merge-duplicates,return=minimal'})[0]

    def rpc(self, name, payload):
        return self.request('rpc/'+name,'POST',payload)[0]


def accounts(db):
    raw=db.rows('cash_bank_accounts',order='account_id.asc')
    items={r['item_id']:r for r in db.rows('cash_bank_items',select='item_id,synced_at,sync_pending',order='item_id.asc')}
    return [{**r,'name':safe_label(r.get('name')),'transactions_synced_at':items.get(r['item_id'],{}).get('synced_at'),
             'sync_pending':items.get(r['item_id'],{}).get('sync_pending',True)} for r in raw]


def card_data(db):
    summaries=db.rows('plaid_card_summary',order='account_id.asc')
    transactions=db.rows('plaid_transactions',order='transaction_id.asc')
    # Select ONE complete connection per displayed physical card; don't add duplicate Items.
    counts={}
    for row in transactions: counts[row.get('account_id')]=counts.get(row.get('account_id'),0)+1
    chosen={}
    for row in summaries:
        group=(row.get('institution_name') or 'Amex',row.get('mask') or row['account_id'])
        score=(counts.get(row['account_id'],0),row.get('balance_synced_at') or '',row['account_id'])
        if group not in chosen or score>chosen[group][0]: chosen[group]=(score,row)
    cards=[r for _,r in chosen.values()]
    allowed={r['account_id'] for r in cards}
    return cards,[r for r in transactions if r.get('account_id') in allowed]


def load_data(db):
    settings=db.rows('cash_settings',order='id.asc')
    bank_accounts=accounts(db)
    cards,card_transactions=card_data(db)
    invoices,verification=scan_invoices(db.url,db.key)
    data={'settings':settings[0] if settings else None,'bank_accounts':bank_accounts,
          'cards':cards,'card_transactions':card_transactions,'invoices':invoices,
          'bank_transactions':db.rows('cash_bank_transactions',order='transaction_id.asc'),
          'reviews':db.rows('cash_bank_reviews',order='transaction_id.asc'),
          'movers':db.rows('cash_mover_payments',order='event_id.asc'),
          'time_entries':db.rows('time_entries',select='id,invnum,employee_name,amount,approved,paid,clock_in',order='id.asc'),
          'time_links':db.rows('cash_mover_time_links',order='time_entry_id.asc'),
          'stripe_rows':db.rows('cash_stripe_balance',order='transaction_id.asc')}
    data['verification']=verification
    return data


def public_snapshot(db, period, *, tax_report=None):
    data=load_data(db)
    marker=db.rows('cash_source_sync',filters={'source':'eq.stripe:'+period},order='source.asc')
    result=snapshot(**{k:v for k,v in data.items() if k!='verification'},stripe_complete=bool(marker) and fresh(marker[0].get('completed_at'),dt.datetime.now(dt.timezone.utc)),period=period)
    result['manual_checking_balance'] = manual_checking_balance(data['settings'])
    if tax_report is not None:
        try:
            result['wa_tax_estimate'] = home_tax_estimate(period, data['invoices'], tax_report)
        except (ArithmeticError, KeyError, TypeError, ValueError):
            result['wa_tax_estimate'] = None
    result['verification']=data['verification']
    result['accounts']=[{k:r.get(k) for k in ('account_id','name','mask','current_balance','available_balance','currency','balance_synced_at','transactions_synced_at','sync_pending')} for r in data['bank_accounts']]
    result['settings']=data['settings']
    result['stripe_synced_at']=marker[0]['completed_at'] if marker else None
    card_map={r['transaction_id']:r for r in data['card_transactions']}
    mover_map={r['event_id']:r for r in data['movers']}
    valid={t['transaction_id'] for t in data['bank_transactions'] if review_valid(t,next((r for r in data['reviews'] if r['transaction_id']==t['transaction_id']),None),card_map,mover_map)}
    result['bank_transactions']=[{**r,'name':safe_label(r.get('name')),'fingerprint':fingerprint(r),'review_valid':r['transaction_id'] in valid,
                                  'review':next((v for v in data['reviews'] if v['transaction_id']==r['transaction_id']),None)}
                                 for r in data['bank_transactions'] if not r.get('removed') and r['account_id']==(data['settings'] or {}).get('account_id')]
    result['cards']=[{k:r.get(k) for k in ('account_id','name','mask','current_balance','statement_balance','next_payment_due_date','balance_synced_at')} for r in data['cards']]
    result['card_credits']=[{**r,'name':safe_label(r.get('name')),'merchant_name':safe_label(r.get('merchant_name'))} for r in data['card_transactions'] if not r.get('pending') and cents(r['amount'])<0]
    result['mover_payments']=data['movers']
    result['hours']=[{**r,'employee_name':safe_label(r.get('employee_name'))} for r in data['time_entries'] if r.get('approved') and not r.get('paid')]
    result['jobs_options']=[{'invnum':r['invnum'],'client':safe_label(r.get('client'))} for r in data['invoices'] if not str(r.get('invnum','')).startswith(('DRAFT-','GS','TAX_REVIEW_')) and isinstance(r.get('data'),dict) and r.get('invnum') not in {'FINANCE_CONFIG','FINANCE_OVERRIDES','PRICING_DEFAULTS','TIER_PRICING'}]
    result['stripe_coverage']=[r['period'] for r in db.rows('cash_source_sync',order='source.asc')]
    return result


def checking_link(plaid_request):
    payload={'user':{'client_user_id':'swj-business-checking'},'client_name':'Styling With Jas',
             'products':['transactions'],'country_codes':['US'],'language':'en',
             'account_filters':{'depository':{'account_subtypes':['checking']}},
             'transactions':{'days_requested':730}}
    redirect=os.environ.get('PLAID_REDIRECT_URI','')
    if redirect: payload['redirect_uri']=redirect
    response=plaid_request('/link/token/create',payload)
    if not response.get('link_token'): raise ValueError('Plaid did not create a checking connection session.')
    return {'link_token':response['link_token']}


def refresh_bank_accounts(db, item, plaid_request):
    payload=plaid_request('/accounts/balance/get',{'access_token':item['access_token']},timeout=12)
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    rows=[]
    for account in payload.get('accounts',[]):
        if account.get('type')!='depository' or account.get('subtype')!='checking': continue
        balances=account.get('balances') or {}
        rows.append({'account_id':account['account_id'],'item_id':item['item_id'],
                     'name':safe_label(account.get('name')),'mask':str(account.get('mask') or '')[-4:],
                     'current_balance':balances.get('current'),'available_balance':balances.get('available'),
                     'currency':balances.get('iso_currency_code'),'balance_synced_at':stamp})
    if not rows: raise ValueError('No checking account was returned. Existing Amex connections were not changed.')
    db.put('cash_bank_accounts',rows,'account_id')
    return {r['account_id'] for r in rows}


def checking_exchange(db, payload, plaid_request):
    token=payload.get('public_token')
    if not isinstance(token,str) or not 1<=len(token)<=2048: raise ValueError('A valid Plaid connection token is required.')
    exchanged=plaid_request('/item/public_token/exchange',{'public_token':token},timeout=15)
    if not exchanged.get('access_token') or not exchanged.get('item_id'): raise ValueError('Plaid did not confirm the checking connection.')
    # Only stored in the new server-only table; never added to the existing Amex Item loop.
    row={'item_id':exchanged['item_id'],'access_token':exchanged['access_token'],'sync_pending':True}
    db.put('cash_bank_items',row,'item_id')
    refresh_bank_accounts(db,row,plaid_request)
    return {'ok':True,'message':'Checking connected. Select the business account, then sync and review its activity.'}


def checking_sync(db, plaid_request):
    items=db.rows('cash_bank_items',order='item_id.asc')
    if not items: raise ValueError('Connect business checking first.')
    completed=0
    for item in items:
        db.put('cash_bank_items',{'item_id':item['item_id'],'access_token':item['access_token'],'sync_pending':True},'item_id')
        allowed=refresh_bank_accounts(db,item,plaid_request)
        original=item.get('cursor'); cursor=original; rows={}; removed=set()
        for page in range(30):
            if time.monotonic()>db.deadline-5: raise ValueError('Bank sync did not finish. Its previous cursor is retained; retry safely.')
            payload={'access_token':item['access_token'],'count':100}
            if cursor is not None: payload['cursor']=cursor
            response=plaid_request('/transactions/sync',payload,timeout=12)
            if not all(isinstance(response.get(k),list) for k in ('added','modified','removed')) or not isinstance(response.get('has_more'),bool):
                raise ValueError('Plaid returned an incomplete sync response. No cursor was advanced.')
            for tx in response['added']+response['modified']:
                if tx.get('account_id') not in allowed: continue
                txid=tx.get('transaction_id')
                if not txid: raise ValueError('A bank transaction is missing its identifier.')
                cents(tx.get('amount'))
                rows[txid]={'transaction_id':txid,'account_id':tx['account_id'],'date':iso_date(tx.get('date')),
                            'amount':str(tx['amount']),'pending':bool(tx.get('pending')),
                            'pending_transaction_id':tx.get('pending_transaction_id'),
                            'name':safe_label(tx.get('merchant_name') or tx.get('name'))}
                removed.discard(txid)
            for tx in response['removed']:
                if tx.get('transaction_id'): removed.add(tx['transaction_id']); rows.pop(tx['transaction_id'],None)
            next_cursor=response.get('next_cursor')
            if not isinstance(next_cursor,str) or (next_cursor==cursor and response['has_more']): raise ValueError('Bank pagination did not advance. Retry the full sync.')
            cursor=next_cursor
            if not response['has_more']: break
        else: raise ValueError('Bank sync exceeded its page limit. No cursor was advanced.')
        if response.get('transactions_update_status')!='HISTORICAL_UPDATE_COMPLETE':
            raise ValueError('Plaid is still preparing checking history. Retry Sync checking shortly; no complete sync was assumed.')
        db.rpc('cash_commit_bank_sync',{'p_item':item['item_id'],'p_expected_cursor':original,
            'p_next_cursor':cursor,'p_rows':list(rows.values()),'p_removed':sorted(removed)})
        completed+=1
    return {'ok':True,'connections_synced':completed}


def stripe_sync(db, period):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',period): raise ValueError('Choose a valid fee month.')
    key=os.environ.get('STRIPE_RECONCILE_KEY','')
    if not key.startswith('rk_live_'): raise ValueError('Actual Stripe fee reads require the existing live restricted reconciliation key.')
    year,month=map(int,period.split('-'));start=dt.datetime(year,month,1,tzinfo=LA)
    end=dt.datetime(year+int(month==12),1 if month==12 else month+1,1,tzinfo=LA)
    raw=stripe_list_all('/v1/balance_transactions',{'created[gte]':int(start.timestamp()),'created[lt]':int(end.timestamp()),'expand[]':'data.source'},key,time.monotonic()+35)
    rows=[]
    for tx in raw:
        if tx.get('currency')!='usd': raise ValueError('A non-USD Stripe balance transaction needs review; no complete fee result is available.')
        if not isinstance(tx.get('fee'),int) or not isinstance(tx.get('amount'),int): raise ValueError('A Stripe fee amount is unavailable.')
        source=tx.get('source') or {};metadata=source.get('metadata') or {} if isinstance(source,dict) else {}
        parsed=parse_metadata_invoice(str(metadata.get('invoice') or ''))
        invoice=parsed['base_invnum'] if parsed else None
        if invoice and invoice.startswith('GS'): continue
        rows.append({'transaction_id':tx['id'],'date':dt.datetime.fromtimestamp(tx['created'],LA).date().isoformat(),
                     'amount':dollars(tx['amount']),'fee':dollars(tx['fee']),'currency':'USD','type':str(tx.get('type') or 'unknown'),
                     'source_id':source.get('id') if isinstance(source,dict) else source,'invoice_ref':invoice})
    # A failed batch never gets a completion marker. Reruns are idempotent by Stripe ID.
    for start in range(0,len(rows),200): db.put('cash_stripe_balance',rows[start:start+200],'transaction_id')
    db.put('cash_source_sync',{'source':'stripe:'+period,'period':period,'completed_at':dt.datetime.now(dt.timezone.utc).isoformat()},'source')
    return {'ok':True,'balance_transactions':len(rows),'message':'Actual transaction fees recorded; no charge-minus-payout estimate used.'}


def save_settings(db, payload):
    available={a['account_id'] for a in accounts(db)}
    if payload.get('account_id') not in available: raise ValueError('Select a connected business checking account.')
    review_from=iso_date(payload.get('review_from'))
    if review_from>dt.datetime.now(LA).date().isoformat(): raise ValueError('The review start cannot be a future date.')
    tax,buffer=cents(payload.get('tax_reserve')),cents(payload.get('operating_buffer'))
    if tax<0 or buffer<0 or payload.get('confirmed') is not True: raise ValueError('Confirm nonnegative remaining reserves, including explicit zero values.')
    db.put('cash_settings',{'id':'business','account_id':payload['account_id'],'review_from':review_from,
                           'tax_reserve':dollars(tax),'operating_buffer':dollars(buffer),
                           'confirmed_at':dt.datetime.now(dt.timezone.utc).isoformat()},'id')
    return {'ok':True}


def add_movers(db, payload):
    raw=payload.get('payments')
    if not isinstance(raw,list) or not 1<=len(raw)<=200: raise ValueError('Review 1–200 mover payments per import.')
    invoices,_=scan_invoices(db.url,db.key)
    from .section7 import real_invoice
    jobs={str(i['invnum']) for i in invoices if real_invoice(i)}
    time_entries={str(r['id']):r for r in db.rows('time_entries',select='id,invnum,amount',order='id.asc')}
    rows=[mover_record(r,jobs,time_entries) for r in raw]
    if len({r['event_id'] for r in rows})!=len(rows): raise ValueError('A payment reference repeats in this import.')
    db.rpc('cash_add_mover_payments',{'p_rows':rows})
    return {'ok':True,'payments_reviewed':len(rows)}


def confirm_review(db, payload, stripe_get):
    # The recovered application includes audited corrections added after the first
    # migration. Fail clearly before accepting evidence if its follow-up is absent.
    db.rows('cash_review_history', select='revision_id', order='revision_id.asc')
    txid=str(payload.get('transaction_id') or '')
    transactions=db.rows('cash_bank_transactions',filters={'transaction_id':'eq.'+txid},order='transaction_id.asc')
    if len(transactions)!=1: raise ValueError('Choose an existing bank transaction.')
    tx=transactions[0]
    if payload.get('fingerprint')!=fingerprint(tx): raise ValueError('This bank transaction changed; refresh before matching.')
    existing=db.rows('cash_bank_reviews',filters={'transaction_id':'eq.'+txid},order='transaction_id.asc')
    if existing and any(str(ref).startswith('mover:bank-') for ref in existing[0].get('source_refs',[])):
        if payload.get('kind')!=existing[0]['kind'] or payload.get('source_refs')!=existing[0]['source_refs']:
            raise ValueError('This movement already created mover costs. Its allocation cannot be reclassified separately from those costs.')
    cards,txs=card_data(db); movers=db.rows('cash_mover_payments',order='event_id.asc')
    refs=payload.get('source_refs') or []; kind=payload.get('kind');payout=None
    if not isinstance(refs,list) or any(not isinstance(r,str) for r in refs): raise ValueError('Choose valid source references.')
    if kind=='stripe_settlement':
        if len(refs)!=1 or not re.fullmatch(r'stripe:po_[A-Za-z0-9]+',refs[0]): raise ValueError('Supply the exact Stripe payout reference.')
        payout=stripe_get('/v1/payouts/'+refs[0][7:])
    checked=validate_review(tx,kind,refs,payload.get('note'),{r['transaction_id']:r for r in txs},{r['event_id']:r for r in movers},payout)
    db.rpc('cash_confirm_review',{'p_transaction':txid,'p_fingerprint':checked['fingerprint'],'p_kind':kind,
                                'p_refs':refs,'p_note':safe_label(payload.get('note'),500),'p_expected_revision':payload.get('review_version')})
    return {'ok':True}


def assign_bank_movers(db, payload):
    """A checking debit is the payment evidence; allocations supply mover/job detail.
    One RPC records costs and confirms the bank movement atomically. No Venmo sync.
    """
    txid=str(payload.get('transaction_id') or '')
    transactions=db.rows('cash_bank_transactions',filters={'transaction_id':'eq.'+txid},order='transaction_id.asc')
    if len(transactions)!=1:
        raise ValueError('Choose an existing posted checking payment.')
    tx=transactions[0]
    if tx.get('pending') or tx.get('removed') or cents(tx.get('amount'))<=0:
        raise ValueError('Only a posted outgoing payment can be assigned to movers.')
    if payload.get('fingerprint')!=fingerprint(tx):
        raise ValueError('This checking payment changed. Refresh before assigning movers.')
    raw=payload.get('allocations')
    if not isinstance(raw,list) or not 1<=len(raw)<=30 or any(not isinstance(r,dict) for r in raw):
        raise ValueError('Assign this payment to 1–30 mover/job rows.')
    if payload.get('confirmed_payment') is not True:
        raise ValueError('Confirm this paid movers directly; wallet funding remains a transfer.')
    note=str(payload.get('note') or '').strip()
    if not note or len(note)>500:
        raise ValueError('Add a confirmation note (maximum 500 characters).')
    invoices,_=scan_invoices(db.url,db.key)
    from .section7 import real_invoice
    jobs={str(i['invnum']) for i in invoices if real_invoice(i)}
    hours={str(r['id']):r for r in db.rows('time_entries',select='id,invnum,amount',order='id.asc')}
    prefix='bank-'+hashlib.sha256(txid.encode()).hexdigest()[:40]
    rows=[mover_record({**r,'event_id':prefix+'-'+str(i+1),'paid_date':tx['date'],'source':'manual_venmo'},jobs,hours)
          for i,r in enumerate(raw)]
    if sum(cents(r['amount']) for r in rows)!=cents(tx['amount']):
        raise ValueError('Mover/job amounts must add up exactly to this checking payment.')
    links=[link for r in rows for link in r['time_entry_ids']]
    if len(links)!=len(set(links)):
        raise ValueError('The same hours cannot be assigned to two movers.')
    db.rpc('cash_assign_bank_movers',{'p_transaction':txid,'p_fingerprint':fingerprint(tx),
                                   'p_rows':rows,'p_note':safe_label(note,500),
                                   'p_expected_revision':payload.get('review_version')})
    return {'ok':True,'allocations_recorded':len(rows)}


def home_tax_estimate(period, invoices, tax_report):
    previous_month = dt.date.fromisoformat(period + '-01') - dt.timedelta(days=1)
    _, _, tax = tax_report(previous_month.year, previous_month.month, invoices=invoices)
    period_key = previous_month.strftime('%Y-%m')
    review = next((row.get('data') or {} for row in invoices
                   if row.get('invnum') == f'TAX_REVIEW_{period_key}'), {})
    return {
        'period': period_key,
        'label': tax['period_label'],
        'estimated_cents': cents(tax['tax_collected']) + cents(tax['bo_total']),
        'filing_recorded': bool(review.get('dor_confirmation_number')),
    }


def manual_checking_balance(settings):
    settings = settings or {}
    amount = settings.get('manual_balance_cents')
    as_of = settings.get('manual_balance_as_of')
    if amount is None or not as_of:
        return None
    return {'balance_cents': amount, 'as_of': as_of}


def save_manual_checking_balance(db, payload):
    amount = payload.get('amount')
    as_of = payload.get('as_of')
    if not isinstance(amount, str) or not re.fullmatch(r'-?\d{1,10}(?:\.\d{1,2})?', amount):
        raise ValueError('Enter a checking balance with up to two decimal places.')
    if not isinstance(as_of, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
        raise ValueError('Enter the date shown by your bank.')
    balance_date = dt.date.fromisoformat(as_of)
    if balance_date > dt.datetime.now(LA).date():
        raise ValueError('The balance date cannot be in the future.')
    balance = cents(amount)
    db.put('cash_settings', {'id': 'business', 'manual_balance_cents': balance,
                             'manual_balance_as_of': as_of}, 'id')
    return {'manual_checking_balance': {'balance_cents': balance, 'as_of': as_of}}


def response(handler, status, data):
    body=json.dumps(data,allow_nan=False,default=str).encode()
    if len(body)>4500000:
        status=503;body=json.dumps({'error':'The complete cash view is too large. Use an export; no partial result is certified.'}).encode()
    handler.send_response(status)
    for k,v in [('Content-Type','application/json'),('Cache-Control','no-store, private'),('X-Content-Type-Options','nosniff'),('Content-Length',str(len(body)))]: handler.send_header(k,v)
    handler.end_headers();handler.wfile.write(body)


def handle(handler, method, base_url, key, plaid_request, stripe_get, *, tax_report=None):
    query=urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
    action=query.get('cash',[''])[0]
    if not auth.same_origin(handler,required=method=='POST') or not auth.authorized(handler,base_url=base_url,key=key):
        response(handler,401,{'error':'Sign in to SWJ on this site before accessing business cash.'});return
    try:
        db=Store(base_url,key)
        if method=='GET' and action=='snapshot':
            period=query.get('period',[dt.datetime.now(LA).strftime('%Y-%m')])[0]
            response(handler,200,public_snapshot(db,period,tax_report=tax_report if query.get('home')==['1'] else None));return
        if method!='POST': response(handler,405,{'error':'Use a supported same-origin action.'});return
        if handler.headers.get('Content-Type','').split(';')[0]!='application/json': raise ValueError('JSON is required.')
        size=int(handler.headers.get('Content-Length','0'))
        if not 0<size<=150000: raise ValueError('Invalid request size.')
        payload=json.loads(handler.rfile.read(size))
        if not isinstance(payload,dict): raise ValueError('Invalid request body.')
        if action=='manual_balance':
            response(handler, 200, save_manual_checking_balance(db, payload));return
        # Check schema before exchanging/storing any new permanent Plaid credential.
        db.rows('cash_settings',order='id.asc')
        if action=='link': result=checking_link(plaid_request)
        elif action=='exchange': result=checking_exchange(db,payload,plaid_request)
        elif action=='sync': result=checking_sync(db,plaid_request)
        elif action=='settings': result=save_settings(db,payload)
        elif action=='movers': result=add_movers(db,payload)
        elif action=='bank_movers': result=assign_bank_movers(db,payload)
        elif action=='review': result=confirm_review(db,payload,stripe_get)
        elif action=='stripe_fees': result=stripe_sync(db,str(payload.get('period') or ''))
        else: raise ValueError('Unknown cash action.')
        response(handler,200,result)
    except SetupRequired as error:
        response(handler,503,{'error':str(error),'setup_required':True})
    except (ValueError,TypeError,json.JSONDecodeError) as error:
        response(handler,400,{'error':str(error) if isinstance(error,ValueError) and not isinstance(error,json.JSONDecodeError) else 'Invalid cash request.'})
    except Exception:
        response(handler,503,{'error':'The cash source could not be verified. Nothing is presented as complete; check connection permissions and retry.'})
