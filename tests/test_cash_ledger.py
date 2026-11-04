import datetime as dt
import unittest
from lib.cash_ledger import snapshot, receipt_events, fingerprint, validate_review, mover_record
from lib.cash_http import checking_sync

NOW=dt.datetime(2026,11,4,18,tzinfo=dt.timezone.utc)
STAMP=NOW.isoformat()
def invoice():
    return {'invnum':'2020101','client':'Example','data':{'base_price':1000,'taxrate':0,'addons':[],'paid':True,'paid_date':'2026-10-30','amount_paid':1000}}
def inputs():
    return dict(invoices=[invoice()],cards=[{'current_balance':200,'balance_synced_at':STAMP}],
      card_transactions=[{'transaction_id':'purchase','date':'2026-11-02','amount':100,'job_invnum':'2020101','category':'Storage'}],
      bank_accounts=[{'account_id':'checking','currency':'USD','current_balance':1000,'available_balance':1000,'balance_synced_at':STAMP,'transactions_synced_at':STAMP,'sync_pending':False}],
      bank_transactions=[],reviews=[],settings={'account_id':'checking','review_from':'2026-11-01','tax_reserve':100,'operating_buffer':50,'confirmed_at':STAMP},
      movers=[],time_entries=[],time_links=[],stripe_rows=[],stripe_complete=True,period='2026-11',now=NOW)
class CashTests(unittest.TestCase):
    def test_october_receipt_remains_cash_in_november(self):
        result=snapshot(**inputs())
        self.assertEqual(result['monthly']['collected_cents'],0)
        self.assertEqual(result['cash']['bank_cents'],100000)
        self.assertEqual(result['cash']['after_commitments_cents'],65000)
        self.assertEqual(result['jobs'][0]['contribution_cents'],90000)
        self.assertIsNone(result['monthly']['margin_percent'])
    def test_partials_extensions_and_signature(self):
        i=invoice();i['data'].update(paid=False,signature=None,payments=[{'id':'PAY1','status':'paid','paid_date':'2026-11-02','amount':300},{'id':'PAY2','status':'pending','amount':700},{'id':'PAY3','status':'paid','paid_date':'2026-11-03','amount':400,'is_test':True}],extensions=[{'invnum':'2020101-EXT1','paid':True,'paid_date':'2026-11-03','amount':50}])
        events,_=receipt_events([i]);self.assertEqual(sum(x['amount_cents'] for x in events),35000)
    def test_bank_transfers_do_not_duplicate_receipts_or_costs(self):
        d=inputs();t={'transaction_id':'pay','account_id':'checking','date':'2026-11-03','amount':200,'pending':False};credit={'transaction_id':'credit','date':'2026-11-03','amount':-200,'pending':False}
        d['bank_transactions']=[t];d['card_transactions'].append(credit)
        r=validate_review(t,'card_payment',['amex:credit'],'Verified posted credit',{'credit':credit},{})
        d['reviews']=[dict(r,transaction_id='pay')]
        out=snapshot(**d);self.assertEqual(out['monthly']['costs_cents'],10000);self.assertEqual(out['unresolved_bank_count'],0)
    def test_pending_changed_stale_or_unknown_blocks_cash(self):
        for case in ['pending','unknown','stale','changed']:
            d=inputs();t={'transaction_id':'x','account_id':'checking','date':'2026-11-03','amount':20,'pending':case=='pending'}
            if case=='stale':d['bank_accounts'][0]['transactions_synced_at']='2026-11-01T00:00:00Z'
            else:d['bank_transactions']=[t]
            if case=='changed':d['reviews']=[{'transaction_id':'x','fingerprint':'old','kind':'venmo_funding','source_refs':[]}]
            self.assertIsNone(snapshot(**d)['cash']['after_commitments_cents'],case)
    def test_mover_only_counted_once_and_hours_cleared(self):
        d=inputs();d['movers']=[{'event_id':'venmo1','paid_date':'2026-11-03','job_invnum':'2020101','amount':75}];d['time_entries']=[{'id':'h','amount':75,'approved':True,'paid':False}];d['time_links']=[{'time_entry_id':'h','event_id':'venmo1'}]
        t={'transaction_id':'x','account_id':'checking','date':'2026-11-03','amount':75,'pending':False};d['bank_transactions']=[t];d['reviews']=[dict(validate_review(t,'mover_payment',['mover:venmo1'],'Actual mover payment',{}, {'venmo1':d['movers'][0]}),transaction_id='x')]
        r=snapshot(**d);self.assertEqual(r['monthly']['mover_spend_cents'],7500);self.assertEqual(r['cash']['mover_commitments_cents'],0)
    def test_actual_stripe_fees_not_charge_minus_payout(self):
        d=inputs();d['stripe_rows']=[{'date':'2026-11-03','type':'charge','amount':1000,'fee':29.3,'invoice_ref':'2020101'},{'date':'2026-11-03','type':'payout','amount':-900,'fee':0}]
        self.assertEqual(snapshot(**d)['monthly']['stripe_fees_cents'],2930)
    def test_invalid_evidence_rejected(self):
        t={'transaction_id':'x','account_id':'checking','date':'2026-11-03','amount':200}
        for refs in [[{}],['amex:x','amex:x'],['amex:missing']]:
            with self.assertRaises(ValueError):validate_review(t,'card_payment',refs,'Checked',{}, {})
    def test_import_requires_existing_job_exact_amount(self):
        p={'event_id':'v1','paid_date':'2026-01-01','mover_name':'Mover','job_invnum':'job','amount':'75.001'}
        with self.assertRaises(ValueError):mover_record(p,{'job'}, {})
        p['amount']='75.00';self.assertEqual(mover_record(p,{'job'}, {})['amount'],'75.00')
        with self.assertRaises(ValueError):mover_record(p,{'other'}, {})
    def test_sync_cursor_commits_only_complete_pages(self):
        class DB:
            deadline=10**20
            def __init__(self):self.commits=[]
            def rows(self,*a,**kw):return [{'item_id':'item','access_token':'private','cursor':'old'}]
            def put(self,*a):pass
            def rpc(self,*a):self.commits.append(a)
        def plaid(path,payload,**kw):
            if 'balance' in path:return {'accounts':[{'account_id':'checking','type':'depository','subtype':'checking','balances':{'current':100,'available':100,'iso_currency_code':'USD'}}]}
            return {'added':[],'modified':[],'removed':[],'next_cursor':'old','has_more':False,'transactions_update_status':'HISTORICAL_UPDATE_COMPLETE'}
        db=DB();checking_sync(db,plaid);self.assertEqual(len(db.commits),1)
        def broken(path,payload,**kw):
            if 'balance' in path:return plaid(path,payload)
            return {'added':[],'modified':[],'removed':[],'next_cursor':'old','has_more':True}
        db=DB()
        with self.assertRaises(ValueError):checking_sync(db,broken)
        self.assertEqual(db.commits,[])
if __name__=='__main__':unittest.main()
