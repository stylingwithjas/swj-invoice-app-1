import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def module(name, path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

generate=module('invoice_generate','api/generate.py')
invoices=module('invoice_save','api/invoices.py')
webhook=module('invoice_webhook','api/webhook.py')
from lib.invoice_protection import is_paid

FIXTURE={'client':'Preview Test Only','address':'Synthetic property','invnum':'TEST-PR63',
         'invdate':'2026-11-02','startdate':'2026-11-10','baserooms':'Living room',
         'extrate':1372.67,'taxrate':10.55,'base_price':3933,
         'addons':[{'desc':'Logistics & Handling','price':185,'type':'rental'}],
         'promo1':False,'promo2':False,'promo3':False,'promo4':False,
         'subtotal_override':4118,'tax_override':226.98,'total_override':4344.98,
         'rental_service_split':True,'rental_total':2151.5,'service_total':1966.5,
         'base_split':{'rental':1966.5,'service':1966.5},'rental_term_days':45,
         'contract_terms_version':'v2_2026_11_ext_tax_on_top'}

class BackendTests(unittest.TestCase):
    def fake_handler(self, cls, body):
        h=cls.__new__(cls);raw=json.dumps(body).encode();h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw)
        h.result=None
        h._respond=lambda code,body:setattr(h,'result',(code,body))
        h._error=lambda code,body:setattr(h,'result',(code,body))
        return h

    def test_paid_status_includes_split_and_legacy_receipts(self):
        for d in ({'paid':True},{'amount_paid':1},{'stripe_amount':1},{'payments':[{'status':'paid'}]}):self.assertTrue(is_paid(d))
        self.assertFalse(is_paid({'paid':False,'payments':[{'status':'pending'}]}))

    def test_paid_generator_rejected_before_pdf(self):
        paid={**FIXTURE,'paid':True};before=copy.deepcopy(paid)
        h=self.fake_handler(generate.handler,{**FIXTURE,'generator_request':True,'total_override':10})
        with patch.object(generate,'stored_invoice',return_value=paid),patch.object(generate,'generate_invoice_pdf') as pdf:
            h.do_POST();self.assertEqual(h.result[0],409);pdf.assert_not_called()
        self.assertEqual(paid,before)

    def test_paid_save_rejected_before_any_write(self):
        h=self.fake_handler(invoices.handler,FIXTURE)
        with patch.object(invoices,'stored_invoice',return_value={**FIXTURE,'paid':True}),patch.object(invoices,'supabase_request') as db:
            h.do_POST();self.assertEqual(h.result[0],409);db.assert_not_called()

    def test_unavailable_guard_fails_closed(self):
        for mod in (generate,invoices):
            h=self.fake_handler(mod.handler,FIXTURE)
            with patch.object(mod,'stored_invoice',side_effect=TimeoutError()):h.do_POST();self.assertEqual(h.result[0],503)

    def test_invoice_save_returns_failure(self):
        h=self.fake_handler(invoices.handler,FIXTURE)
        with patch.object(invoices,'stored_invoice',return_value=None),patch.object(invoices,'supabase_request',return_value=(500,{})):
            h.do_POST();self.assertEqual(h.result[0],502)

    def test_concurrent_payment_does_not_overwrite(self):
        h=self.fake_handler(invoices.handler,FIXTURE)
        before={**FIXTURE,'paid':False,'payments':[]}
        with patch.object(invoices,'stored_invoice',return_value=before),patch.object(invoices,'supabase_request',side_effect=[(409,{}),(200,[])]) as db:
            h.do_POST();self.assertEqual(h.result[0],409)
            from urllib.parse import parse_qs, urlparse
            query=parse_qs(urlparse(db.call_args.args[1]).query)
            self.assertEqual(query['data->>paid'],['eq.false'])
            self.assertEqual(query['data->payments'],['eq.[]'])
            self.assertNotIn('data',query)

    def test_existing_unpaid_save(self):
        h=self.fake_handler(invoices.handler,FIXTURE)
        with patch.object(invoices,'stored_invoice',return_value=FIXTURE),patch.object(invoices,'supabase_request',side_effect=[(409,{}),(200,[{'invnum':'TEST-PR63'}])]):
            h.do_POST();self.assertEqual(h.result[0],200)

    def test_extension_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            generate.build_extension_pdf({'extension':True,'base_price':110,'taxrate':10,'extension_fee':100,'extension_tax':8,'extension_total':110})

    def test_historical_tax_cash_receipts_regression(self):
        # Synthetic paid amounts match the owner-specified acceptance totals. These
        # prove reader behavior, NOT the state of the live database.
        fixtures=[{'invnum':str(i),'data':{**FIXTURE,'paid':True,'paid_date':date,'amount_paid':amt}}
                  for i,(date,amt) in enumerate([('2026-10-15',3978),('2026-10-01',3527.28),('2026-10-31',4111.60),('2026-08-10',6888.24)])]
        for month,expected in [('2026-10',11616.88),('2026-08',6888.24),('2026-09',0)]:
            got=sum(p['amount'] for row in fixtures for p in webhook.tax_payment_events(row['data']) if p['date'].startswith(month))
            self.assertAlmostEqual(got,expected,places=2)

    def test_actual_pdfs_tax_dollars_and_black_totals(self):
        import fitz
        output=ROOT/'tests/artifacts';output.mkdir(exist_ok=True)
        # Tax-on-top and inclusive have the same explicit pretax/tax/total when
        # they represent the same charge. No client/Stripe calls are made.
        for label,fee,tax,total,rate in [('added',100,10.55,110.55,10.55),('inclusive',90.46,9.54,100,10.55),('zero',100,0,100,0)]:
            data={'extension':True,'client':'Preview Test Only','invnum':'TEST-EXT','invdate':'2026-11-02','extension_days':30,
                  'base_price':total,'taxrate':rate,'extension_fee':fee,'extension_tax':tax,'extension_total':total}
            path=generate.build_extension_pdf(data);doc=fitz.open(path);text=''.join(p.get_text() for p in doc)
            self.assertIn(f'Tax ({rate:.2f}%)',text);self.assertIn(f'${tax:,.2f}',text);self.assertIn(f'${total:,.2f}',text)
            doc[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(str(output/f'extension-{label}.png'));doc.close();os.unlink(path)
        data={**FIXTURE,'rental_term_days':90,'base_split':{'rental':3933,'service':0},'rental_total':4118,'service_total':0,
              'tax_override':434.45,'total_override':4552.45}
        path=generate.generate_invoice_pdf(data);doc=fitz.open(path)
        spans=[s for p in doc for b in p.get_text('dict')['blocks'] if 'lines' in b for l in b['lines'] for s in l['spans']]
        totals=[s for s in spans if 'Furniture Rental Total:' in s['text']]
        self.assertEqual(len(totals),1);self.assertEqual(totals[0]['color'],0)
        detail=[s for s in spans if 'Furniture rental:' in s['text']]
        self.assertTrue(detail);self.assertTrue(all(s['color']==0x111111 and 'Bold' not in s['font'] for s in detail))
        self.assertNotIn('Staging Design Total:',''.join(p.get_text() for p in doc))
        doc[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(str(output/'term90-invoice.png'));doc.close();os.unlink(path)

if __name__=='__main__':unittest.main()
