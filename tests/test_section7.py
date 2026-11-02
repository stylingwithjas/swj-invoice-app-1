import base64
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lib import section7 as audit, section7_auth as auth, section7_http as api

CLEAN={'base_price':3933,'taxrate':10.55,'addons':[{'desc':'Logistics & Handling','price':185,'type':'rental'}],
       'subtotal_override':4118,'tax_override':226.98,'total_override':4344.98,'extrate':1372.67,
       'rental_service_split':True,'rental_total':2151.5,'service_total':1966.5,
       'base_split':{'rental':1966.5,'service':1966.5},'manual_totals_fields':[]}

def invoice(d=None, invnum='2020131', **fields):
    return {'invnum':invnum,'client':'Synthetic Test Only','invdate':'2026-10-01','created_at':'2026-10-01T00:00:00Z','data':copy.deepcopy(CLEAN if d is None else d),**fields}

def fake_key(role='service_role'):
    payload=base64.urlsafe_b64encode(json.dumps({'role':role}).encode()).decode().rstrip('=')
    return 'test.'+payload+'.not-a-real-signature'

class CalculationTests(unittest.TestCase):
    def codes(self,d):return [x['rule'] for x in audit.check_invoice(invoice(d),'2026-10')['issues']]

    def test_reconstructed_values_match_actual_pdf_reader(self):
        import random,textwrap
        source=(ROOT/'api/generate.py').read_text()
        start=source.index("    base_price = float(data.get('base_price', 2500))")
        stop=source.index('    # Part of the total',start)
        block=textwrap.dedent(source[start:stop])
        rng=random.Random(7)
        cases=[CLEAN,{'base_price':0,'taxrate':10,'addons':[{'price':100,'type':'rental'}],'subtotal_override':100,'tax_override':10,'total_override':110,'extrate':33.33}]
        for index in range(100):
            cases.append({'base_price':rng.randrange(50000,900000)/100,'taxrate':rng.choice([0,9.6,10.55]),'addons':[{'price':rng.randrange(0,50000)/100,'type':'service' if index%2 else 'rental'}],'price_led_tax':bool(index%3),'extrate':0})
        for d in cases:
            env={'data':d,'addons':d['addons'],'taxrate':d['taxrate']};exec(block,env)
            r=audit.check_invoice(invoice(d),'2026-10')
            for key,field in [('subtotal','subtotal'),('tax','tax'),('grand','grand')]:
                self.assertEqual(r['values'][field],format(env[key],'.2f'))

    def test_clean_stored_split(self):
        self.assertEqual(self.codes(CLEAN),[])

    def test_every_arithmetic_check_even_with_overrides(self):
        cases=[({'subtotal_override':4100},'lines_subtotal'),({'tax_override':227.98,'total_override':4345.98},'tax_base'),
               ({'total_override':4354.98},'subtotal_tax_total'),({'extrate':1500},'extension_rate')]
        for changes,code in cases:
            with self.subTest(code=code):self.assertIn(code,self.codes({**CLEAN,**changes}))

    def test_one_cent_not_silently_ignored(self):
        self.assertIn('tax_base',self.codes({**CLEAN,'tax_override':226.99,'total_override':4344.99}))

    def test_legacy_bug_reconstructed_without_new_split(self):
        d={'base_price':3933,'taxrate':10.55,'addons':[{'price':185,'type':'rental'}],'price_led_tax':True,'rental_share_base':.5,'extrate':1517.48}
        result=audit.check_invoice(invoice(d),'2026-10')
        self.assertEqual(result['pricing_mode'],'legacy_price_led')
        self.assertIn('lines_subtotal',[f['rule']for f in result['issues']]);self.assertNotIn('tax_base',[f['rule']for f in result['issues']])
        self.assertNotEqual(result['values']['subtotal'],'4118.00')

    def test_rate_card_zero_base_and_context(self):
        d={'base_price':0,'taxrate':10,'addons':[{'price':1200,'type':'rental'},{'price':300,'type':'service'}],
           'pricing_mode':'rate_card','subtotal_override':1500,'tax_override':120,'total_override':1620,'extrate':800}
        r=audit.check_invoice(invoice(d),'2026-10');self.assertEqual(r['values']['visible_lines'],'1500.00');self.assertEqual(r['values']['taxable_base'],'1200.00')
        self.assertEqual([f['rule']for f in r['issues']],['extension_rate']);self.assertIn('Rate-card',r['issues'][0]['detail'])

    def test_missing_input_does_not_hide_independent_total_failure(self):
        d={**CLEAN,'base_price':None,'total_override':10}
        codes=self.codes(d);self.assertIn('missing_input',codes);self.assertIn('subtotal_tax_total',codes)
        r=audit.check_invoice(invoice(d),'2026-10');self.assertIsNone(r['values']['visible_lines'])

    def test_malformed_addon_does_not_crash_scan(self):
        for bad in (None,'bad',{},[None],[{'price':'NaN'}],[{'price':False}]):
            with self.subTest(bad=bad):self.assertIn('missing_input',self.codes({**CLEAN,'addons':bad}))

    def test_missing_all_data_is_unverifiable_not_clean(self):
        r=audit.build_report([invoice(d=None),invoice(d={},invnum='EMPTY')],'2026-10')
        self.assertEqual(r['unverifiable_count'],1);self.assertEqual(r['invoices_checked'],2)
        row=invoice();row['data']=None;r=audit.build_report([row],'2026-10');self.assertEqual(r['unverifiable_count'],1)

    def test_filters_only_exclude_known_non_invoice_rows(self):
        rows=[invoice(invnum=n) for n in ['DRAFT-1','GS20261','FINANCE_CONFIG','TAX_REVIEW_2026-10','2020131','2020132']]
        rows[-2]['data']['stage']='closed_lost';rows[-1]['data']['stage']='denied'
        r=audit.build_report(rows,'2026-10');self.assertEqual(r['invoices_checked'],2);self.assertEqual(r['excluded_records'],4)

    def test_record_never_mutated(self):
        rows=[invoice({**CLEAN,'paid':True,'paid_date':'2026-10-15','total_override':10})];before=copy.deepcopy(rows)
        r=audit.build_report(rows,'2026-10');audit.csv_report(r);self.assertEqual(rows,before);self.assertEqual(r['paid_period_discrepancy_count'],1)

    def test_test_payments_not_live_october(self):
        d={**CLEAN,'total_override':10,'paid':True,'payments':[{'status':'paid','paid_date':'2026-10-15','is_test':True}]}
        r=audit.build_report([invoice(d)],'2026-10');self.assertEqual(r['paid_period_discrepancy_count'],0);self.assertTrue(r['results'][0]['test_paid'])

    def test_partial_payments_use_recorded_receipt_date(self):
        d={**CLEAN,'total_override':10,'payments':[{'status':'paid','paid_date':'2026-10-31','amount':100},{'status':'pending','amount':100}]}
        self.assertEqual(audit.build_report([invoice(d)],'2026-10')['paid_period_discrepancy_count'],1)
        self.assertEqual(audit.build_report([invoice(d)],'2026-09')['paid_period_discrepancy_count'],0)

    def test_missing_paid_date_is_incomplete(self):
        r=audit.build_report([invoice({**CLEAN,'paid':True})],'2026-10')
        self.assertEqual(r['paid_date_unknown_count'],1);self.assertEqual(r['unverifiable_count'],1)
        self.assertEqual(r['paid_period_discrepancy_count'],0)

    def test_hidden_split_totals_are_flagged(self):
        r=self.codes({**CLEAN,'rental_service_split':False,'tax_override':434.45,'total_override':4552.45})
        self.assertIn('split_presentation',r);self.assertIn('tax_base',r)

    def test_nested_inclusive_and_tax_added_extensions(self):
        for ext in ({'fee':100,'tax':9.54,'amount':100,'tax_inclusive':True},
                    {'fee':90.46,'tax':9.54,'amount':100,'tax_inclusive':True},
                    {'fee':100,'tax':10.55,'amount':110.55,'tax_inclusive':False}):
            self.assertEqual(self.codes({**CLEAN,'extensions':[ext]}),[])
        self.assertIn('extension_total',self.codes({**CLEAN,'extensions':[{'fee':100,'tax':10.55,'amount':100}]}))

    def test_no_missing_extension_amount_is_invented(self):
        self.assertIn('extension_breakdown',self.codes({**CLEAN,'extensions':[{'amount':100}]}))

    def test_exports_same_rows_and_safe_text(self):
        synthetic_sensitive=' '.join(str(1234+1111*i)for i in range(4))
        row=invoice({**CLEAN,'total_override':10,'paid':True,'paid_date':'2026-10-02'},client='=HYPERLINK("evil") '+synthetic_sensitive)
        r=audit.build_report([row],'2026-10');r['fingerprint']='test-snapshot'
        csv=audit.csv_report(r,'paid_period')
        self.assertIn("'=HYPERLINK",csv);self.assertIn('[masked]',csv);self.assertNotIn(synthetic_sensitive,csv);self.assertIn('test-snapshot',csv)
        encoded=json.dumps(r);self.assertNotIn('addons',encoded);self.assertNotIn('payment_url',encoded)

    def test_period_validation(self):
        for p in ('2026-00','2026-13','whatever','2026-10-01'):
            with self.assertRaises(ValueError):audit.build_report([],p)

class FakeResponse:
    def __init__(self, rows, count):self.rows=rows;self.headers={'Content-Range':('*' if not rows else '0-'+str(len(rows)-1))+'/'+str(count)}
    def read(self):return json.dumps(self.rows).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass

class ScanTests(unittest.TestCase):
    def opener(self, rows, cap=250):
        calls=[]
        def open(request,timeout):
            self.assertEqual(request.get_method(),'GET');self.assertNotIn('/stripe',request.full_url);calls.append(request.full_url)
            q=parse_qs(urlparse(request.full_url).query);after=q.get('invnum',['gt.'])[0][3:]
            rest=[r for r in rows if r['invnum']>after]
            return FakeResponse(rest[:cap],len(rest))
        return open,calls

    def test_non_privileged_key_cannot_claim_complete_inventory(self):
        open,calls=self.opener([])
        with self.assertRaises(api.IncompleteScan):api.scan('https://example.test',fake_key('anon'),open)
        self.assertEqual(calls,[])

    def test_deadline_and_duplicates_fail_closed(self):
        open,calls=self.opener([invoice()])
        with self.assertRaises(api.IncompleteScan):api.read_pass('https://example.test',fake_key(),0,open)
        def repeated(request,timeout):return FakeResponse([invoice()],2)
        with self.assertRaises(api.IncompleteScan):api.scan('https://example.test',fake_key(),repeated)

    def test_full_keyset_coverage_beyond_default_limits(self):
        rows=[invoice(invnum=f'{i:07d}')for i in range(1205)];open,calls=self.opener(rows,cap=173)
        got,coverage=api.scan('https://example.test',fake_key(),open)
        self.assertEqual(len(got),1205);self.assertTrue(coverage['complete']);self.assertGreater(len(calls),10)

    def test_empty_database_is_verified(self):
        open,calls=self.opener([]);got,c=api.scan('https://example.test',fake_key(),open);self.assertEqual(got,[]);self.assertTrue(c['complete']);self.assertEqual(len(calls),2)

    def test_matching_counts_but_changed_values_rejected(self):
        calls=0
        def open(request,timeout):
            nonlocal calls;calls+=1
            return FakeResponse([invoice({**CLEAN,'total_override':10 if calls==1 else 20})],1)
        with self.assertRaises(api.IncompleteScan):api.scan('https://example.test',fake_key(),open)

    def test_silent_truncation_rejected(self):
        calls=0
        def open(request,timeout):
            nonlocal calls;calls+=1;return FakeResponse([invoice()]if calls==1 else [],2)
        with self.assertRaises(api.IncompleteScan):api.scan('https://example.test',fake_key(),open)

    def test_coverage_header_required(self):
        def open(request,timeout):
            r=FakeResponse([],0);r.headers={};return r
        with self.assertRaises(api.IncompleteScan):api.scan('https://example.test',fake_key(),open)

class FakeHandler:
    def __init__(self,path='/api/webhook?section7=report&period=2026-10',body=None,headers=None):
        raw=json.dumps(body or {}).encode();self.path=path;self.headers={'Host':'preview.example.test','Content-Type':'application/json','Content-Length':str(len(raw)),**(headers or {})}
        self.rfile=io.BytesIO(raw);self.wfile=io.BytesIO();self.out_headers={}
    def send_response(self,code):self.code=code
    def send_header(self,k,v):self.out_headers[k]=v
    def end_headers(self):pass
    def data(self):return json.loads(self.wfile.getvalue())

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'SWJ_REPORT_ADMIN_PASSWORD':'synthetic-admin-password-only','SUPABASE_KEY':fake_key(),'VERCEL_ENV':'preview'})
        self.env.start();auth._ATTEMPTS.clear()
    def tearDown(self):self.env.stop()
    def authorized(self):return {'Cookie':auth.COOKIE+'='+auth.issue_session('https://preview.example.test')}

    def test_actual_webhook_dispatch_never_runs_cron_or_stripe(self):
        spec=importlib.util.spec_from_file_location('section7_webhook',ROOT/'api/webhook.py');webhook=importlib.util.module_from_spec(spec);spec.loader.exec_module(webhook)
        h=FakeHandler('/api/webhook?section7=report&job=monthly_close',headers=self.authorized())
        coverage={'complete':True,'fingerprint':'test-fingerprint','completed_at':'2026-11-02T18:00:00Z'}
        with patch.object(api,'scan',return_value=([invoice()],coverage)),patch.object(webhook,'run_monthly_close') as close,patch.object(webhook,'run_destage_reminders') as remind,patch.object(webhook,'verify_signature') as stripe:
            webhook.handler.do_GET(h);self.assertEqual(h.code,200);close.assert_not_called();remind.assert_not_called();stripe.assert_not_called()
        h=FakeHandler('/api/webhook?section7=login',{'password':'synthetic-admin-password-only'},{'Origin':'https://preview.example.test','Stripe-Signature':'synthetic-notice'})
        with patch.object(webhook,'verify_signature') as stripe:
            webhook.handler.do_POST(h);self.assertEqual(h.code,200);stripe.assert_not_called()
        h=FakeHandler('/api/webhook?section7=report')
        webhook.handler.do_OPTIONS(h);self.assertEqual(h.code,405);self.assertNotIn('Access-Control-Allow-Origin',h.out_headers)
        h=FakeHandler('/api/webhook?job=monthly_close',headers={'Authorization':'Bearer fixture-cron-secret'})
        h._respond=lambda code,data:api.response(h,code,data)
        with patch.dict(os.environ,{'CRON_SECRET':'fixture-cron-secret'}),patch.object(webhook,'run_monthly_close',return_value={'test':True}) as close:
            webhook.handler.do_GET(h);self.assertEqual(h.code,200);close.assert_called_once()

    def test_anonymous_and_browser_gate_cannot_read_data(self):
        for headers in ({},{'Cookie':'swj_auth_v1=ok'},{'Authorization':'Bearer pretend-anon-key'}):
            h=FakeHandler(headers=headers)
            with patch.object(api,'scan') as scanner:
                api.handle(h,'GET','https://example.test',fake_key(),scanner);scanner.assert_not_called();self.assertEqual(h.code,401)

    def test_login_cookie_and_report(self):
        h=FakeHandler('/api/webhook?section7=login',{'password':'synthetic-admin-password-only'},{'Origin':'https://preview.example.test'})
        api.handle(h,'POST','https://example.test',fake_key());self.assertEqual(h.code,200)
        cookie=h.out_headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=Strict',cookie)
        h=FakeHandler(headers={'Cookie':cookie.split(';')[0]})
        rows=[invoice({**CLEAN,'total_override':10})];coverage={'complete':True,'fingerprint':'synthetic-fingerprint','completed_at':'2026-11-02T18:00:00Z'}
        api.handle(h,'GET','https://example.test',fake_key(),lambda *args:(rows,coverage));self.assertEqual(h.code,200);self.assertEqual(h.data()['flagged_count'],1)
        self.assertEqual(h.out_headers['Cache-Control'],'no-store, private');self.assertNotIn('Access-Control-Allow-Origin',h.out_headers)
        self.assertNotIn('synthetic-admin-password-only',h.wfile.getvalue().decode())

    def test_cross_origin_login_rejected(self):
        h=FakeHandler('/api/webhook?section7=login',{'password':'synthetic-admin-password-only'},{'Origin':'https://evil.test'})
        api.handle(h,'POST','https://example.test',fake_key());self.assertEqual(h.code,403);self.assertNotIn('Set-Cookie',h.out_headers)

    def test_missing_configuration_is_closed(self):
        with patch.dict(os.environ,{'SWJ_REPORT_ADMIN_PASSWORD':''}):
            h=FakeHandler();api.handle(h,'GET','https://example.test',fake_key());self.assertEqual(h.code,503)

    def test_password_failure_expiry_tamper_host_and_environment(self):
        self.assertFalse(auth.password_matches('wrong'))
        valid=auth.issue_session('https://preview.example.test',now=1000)
        h=FakeHandler(headers={'Cookie':auth.COOKIE+'='+valid});self.assertTrue(auth.authorized(h,now=1001));self.assertFalse(auth.authorized(h,now=2800))
        h.headers['Host']='production.example.test';self.assertFalse(auth.authorized(h,now=1001))
        h.headers['Host']='preview.example.test';h.headers['Cookie']=auth.COOKIE+'='+valid[:-1]+('a'if valid[-1]!='a'else'b');self.assertFalse(auth.authorized(h,now=1001))
        h.headers['Cookie']=auth.COOKIE+'='+valid
        with patch.dict(os.environ,{'VERCEL_ENV':'production'}):self.assertFalse(auth.authorized(h,now=1001))
        with patch.dict(os.environ,{'SWJ_REPORT_ADMIN_PASSWORD':'another-synthetic-password'}):self.assertFalse(auth.authorized(h,now=1001))

    def test_scan_failure_never_looks_clean_or_leaks_secrets(self):
        h=FakeHandler(headers=self.authorized())
        def scanner(*args):raise RuntimeError('SECRET-response-body-should-not-appear')
        api.handle(h,'GET','https://example.test',fake_key(),scanner);self.assertEqual(h.code,503);self.assertFalse(h.data()['coverage']['complete']);self.assertNotIn('SECRET',h.wfile.getvalue().decode())

    def test_unrecognized_route_cannot_fall_into_cron(self):
        h=FakeHandler('/api/webhook?section7=unknown&job=monthly_close',headers=self.authorized())
        self.assertTrue(api.handle(h,'GET','https://example.test',fake_key()));self.assertEqual(h.code,404)

    def test_rate_limit(self):
        for i in range(8):self.assertTrue(auth.allow_login(now=10+i))
        self.assertFalse(auth.allow_login(now=20));self.assertTrue(auth.allow_login(now=80))

    def test_default_webhook_not_captured(self):
        self.assertFalse(api.handle(FakeHandler('/api/webhook?job=monthly_close'),'GET','https://example.test',fake_key()))

class CommandTests(unittest.TestCase):
    def runner(self):
        spec=importlib.util.spec_from_file_location('section7_command',ROOT/'scripts/run_section7_report.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

    def test_cli_uses_existing_environment_and_writes_only_report_outputs(self):
        import tempfile
        m=self.runner();rows=[invoice({**CLEAN,'paid':True,'paid_date':'2026-10-15','total_override':10})]
        coverage={'complete':True,'fingerprint':'synthetic-report-fingerprint'}
        before=copy.deepcopy(rows)
        with tempfile.TemporaryDirectory()as folder,patch.dict(os.environ,{'SUPABASE_KEY':fake_key()}),patch('sys.stdout',new=io.StringIO())as output:
            self.assertEqual(m.main(['--output-dir',folder],scanner=lambda *args:(rows,coverage)),0)
            files=list(Path(folder).iterdir());self.assertEqual(len(files),3)
            self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in files))
            snapshot=json.loads(next(p for p in files if p.suffix=='.json').read_text())
            self.assertEqual(snapshot['paid_period_discrepancy_count'],1);self.assertEqual(rows,before)
            self.assertNotIn(fake_key(),output.getvalue())

    def test_cli_missing_key_never_scans(self):
        m=self.runner()
        with patch.dict(os.environ,{'SUPABASE_KEY':''}),patch('sys.stderr',new=io.StringIO()),patch.object(m,'scan')as scanner:
            self.assertEqual(m.main([]),2);scanner.assert_not_called()

if __name__=='__main__':unittest.main()
