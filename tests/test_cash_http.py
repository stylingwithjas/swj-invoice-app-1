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
