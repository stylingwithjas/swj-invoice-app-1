import io
import json
import unittest
from unittest.mock import patch
from lib import cash_http
class Handler:
    path='/api/webhook?cash=sync'
    headers={'Content-Type':'application/json','Content-Length':'2'}
    def __init__(self):self.rfile=io.BytesIO(b'{}');self.wfile=io.BytesIO();self.status=None;self.outheaders={}
    def send_response(self,status):self.status=status
    def send_header(self,k,v):self.outheaders[k]=v
    def end_headers(self):pass
class HTTPTests(unittest.TestCase):
    def test_rejects_unauthorized_before_sources(self):
        h=Handler()
        with patch.object(cash_http.auth,'same_origin',return_value=True),patch.object(cash_http.auth,'authorized',return_value=False),patch.object(cash_http,'Store') as db:
            cash_http.handle(h,'POST','https://example.test','private',None,None)
            self.assertEqual(h.status,401);db.assert_not_called()
        self.assertEqual(h.outheaders['Cache-Control'],'no-store, private')
    def test_cross_origin_cannot_access_or_mutate(self):
        h=Handler()
        with patch.object(cash_http.auth,'same_origin',return_value=False),patch.object(cash_http,'Store') as db:
            cash_http.handle(h,'POST','https://example.test','private',None,None)
            self.assertEqual(h.status,401);db.assert_not_called()
    def test_missing_correction_schema_blocks_review_before_evidence_or_write(self):
        from unittest.mock import Mock
        db=Mock()
        db.rows.side_effect=cash_http.SetupRequired('Pending checking review schema.')
        stripe=Mock()
        with self.assertRaises(cash_http.SetupRequired):
            cash_http.confirm_review(db,{},stripe)
        db.rpc.assert_not_called()
        stripe.assert_not_called()
        db.rows.assert_called_once_with('cash_review_history',select='revision_id',order='revision_id.asc')
    def bank_assignment(self, **changes):
        from unittest.mock import Mock
        from lib.cash_ledger import fingerprint
        tx={'transaction_id':'venmo-bank-1','account_id':'checking','date':'2026-11-03','amount':75,'pending':False}
        payload={'transaction_id':tx['transaction_id'],'fingerprint':fingerprint(tx),'confirmed_payment':True,
                 'note':'Confirmed direct mover payments','allocations':[{'mover_name':'Mover A','job_invnum':'JOB-A','amount':'25.00'},
                                                                          {'mover_name':'Mover B','job_invnum':'JOB-B','amount':'50.00'}]}
        payload.update(changes)
        db=Mock(url='https://example.test',key='private')
        db.rows.side_effect=lambda table,**kw:[tx] if table=='cash_bank_transactions' else []
        with patch.object(cash_http,'scan_invoices',return_value=([{'invnum':'JOB-A'},{'invnum':'JOB-B'}],{})):
            result=cash_http.assign_bank_movers(db,payload)
        return db,result
    def test_checking_mover_split_saves_once_atomically_and_retry_uses_same_ids(self):
        db,result=self.bank_assignment()
        self.assertEqual(result['allocations_recorded'],2)
        db.rpc.assert_called_once()
        name,payload=db.rpc.call_args.args
        self.assertEqual(name,'cash_assign_bank_movers')
        self.assertEqual([p['amount'] for p in payload['p_rows']],['25.00','50.00'])
        self.assertTrue(all(p['paid_date']=='2026-11-03' for p in payload['p_rows']))
        retry,_=self.bank_assignment()
        self.assertEqual(retry.rpc.call_args.args[1]['p_rows'],payload['p_rows'])
    def test_wallet_funding_requires_explicit_mover_payment_confirmation(self):
        with self.assertRaisesRegex(ValueError,'wallet funding'):
            self.bank_assignment(confirmed_payment=False)
    def test_mover_allocation_cannot_exceed_checking_debit(self):
        with self.assertRaisesRegex(ValueError,'exactly'):
            self.bank_assignment(allocations=[{'mover_name':'A','job_invnum':'JOB-A','amount':'100'}])
    def test_changed_checking_payment_cannot_create_mover_costs(self):
        with self.assertRaisesRegex(ValueError,'changed'):
            self.bank_assignment(fingerprint='old')
    def test_partial_read_fails_closed(self):
        db=cash_http.Store('https://example.test','private')
        with patch.object(db,'request',return_value=([{'id':1}],{'Content-Range':'0-0/2'})):
            with self.assertRaises(ValueError):db.rows('test')
    def test_errors_do_not_echo_tokens(self):
        h=Handler()
        with patch.object(cash_http.auth,'same_origin',return_value=True),patch.object(cash_http.auth,'authorized',return_value=True),patch.object(cash_http,'Store',side_effect=RuntimeError('secret-token')):
            cash_http.handle(h,'POST','https://example.test','private',None,None)
        self.assertEqual(h.status,503);self.assertNotIn(b'secret-token',h.wfile.getvalue())
if __name__=='__main__':unittest.main()
