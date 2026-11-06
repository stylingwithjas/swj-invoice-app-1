"""Offline tax-evidence and output-boundary regression tests; zero external effects."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib import tax_verification as tax
from lib.downloads import content_disposition

ROOT=Path(__file__).resolve().parents[1]
XML='<response loccode="1726" rate=".1055" code="{code}"><addressline street="4TH AVE" houselow="600" househigh="600" state="WA" zip="98104" period="Q42026"/><rate name="SEATTLE"/></response>'


def module(name):
    spec=importlib.util.spec_from_file_location('tax_test_'+name,ROOT/'api'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def handler(mod,body):
    h=mod.handler.__new__(mod.handler);raw=json.dumps(body).encode();h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw)
    h._respond=lambda code,data:setattr(h,'result',(code,data));h._error=lambda code,data:setattr(h,'result',(code,data))
    return h


class TaxEvidenceTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'SUPABASE_KEY':'fixture-service-key-long-enough-for-HMAC'});env.start();self.addCleanup(env.stop)

    def fixture(self,code=0):
        result=tax.attest(tax.parse_response(XML.format(code=code)),'600 4th Ave','Seattle','98104','2026-11-05',now=1000)
        return {'address':'600 4th Ave, Seattle 98104','city':'Seattle','zip':'98104','invdate':'2026-11-05','taxrate':10.55,'tax_verification':{**result['verification'],'addressConfirmed':code==0}}

    def test_official_address_codes_are_distinct_from_zip_fallbacks(self):
        for code in (0,1,2,3,4,5,6,7,9):
            result=tax.parse_response(XML.format(code=code))
            if code in (6,7,9):self.assertFalse(result['ok']);continue
            self.assertEqual(result['addressMatch'],code in (0,2,4))
            self.assertEqual(result['needsConfirmation'],code in (2,4))
            self.assertEqual(result['rate'],10.55)
            self.assertEqual(result['period'],'Q42026')
            evidence=tax.attest(result,'600 4th Ave','Seattle','98104','2026-11-05',now=1000)
            self.assertEqual('verification' in evidence,code in (0,2,4))

    def test_positive_proof_and_address_confirmation(self):
        self.assertTrue(tax.require_verified(self.fixture(),now=1001))
        for code in (2,4):
            data=self.fixture(code)
            with self.assertRaises(ValueError):tax.require_verified(data,now=1001)
            data['tax_verification']['addressConfirmed']=True
            self.assertTrue(tax.require_verified(data,now=1001))

    def test_changed_address_city_zip_date_rate_expiry_and_tampering_rejected(self):
        for field,value in [('address','Other property'),('city','Tacoma'),('zip','98402'),('invdate','2026-11-06'),('taxrate',9.6)]:
            data=self.fixture();data[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):tax.require_verified(data,now=1001)
        data=self.fixture();data['tax_verification']['proof']+='x'
        with self.assertRaises(ValueError):tax.require_verified(data,now=1001)
        with self.assertRaises(ValueError):tax.require_verified(self.fixture(),now=1000+tax.LIFETIME)
        with self.assertRaises(ValueError):tax.require_verified({},now=1001)

    def test_current_rate_never_attests_another_quarter(self):
        result=tax.attest(tax.parse_response(XML.format(code=0)),'600 4th Ave','Seattle','98104','2027-01-05',now=1000)
        self.assertFalse(result['ok']);self.assertNotIn('verification',result)

    def test_manual_tax_mismatch_needs_documented_review_without_recalculation(self):
        data={**self.fixture(),'rental_service_split':True,'rental_total':1325,'manual_totals_fields':['tax'],'tax_override':100}
        original=copy.deepcopy(data)
        with self.assertRaises(ValueError):tax.require_verified(data,now=1001)
        self.assertEqual(data,original)
        data['manual_tax_review']={'confirmed':True,'reason':'Synthetic reviewed exception, fixture only'}
        self.assertTrue(tax.require_verified(data,now=1001));self.assertEqual(data['tax_override'],100)

    def test_no_or_bad_evidence_rejected_before_pdf_save_or_stripe_calls(self):
        generate=module('generate');h=handler(generate,{'invnum':'TEST','generator_request':True})
        with patch.object(generate,'require_owner',return_value=True),patch.object(generate,'require_number',return_value=True),patch.object(generate,'stored_invoice',return_value=None),patch.object(generate,'generate_invoice_pdf') as pdf:
            generate.handler.do_POST(h);pdf.assert_not_called()
        self.assertEqual(h.result[0],422)
        save=module('invoices');h=handler(save,{'invnum':'TEST','tax_guard_version':1})
        with patch.object(save,'require_owner',return_value=True),patch.object(save,'require_number',return_value=True),patch.object(save,'stored_invoice',return_value=None),patch.object(save,'supabase_request') as db:
            save.handler.do_POST(h);db.assert_not_called()
        self.assertEqual(h.result[0],422)
        stripe=module('stripe');h=handler(stripe,{'source':'invoice_generator','tax_context':{}})
        with patch.object(stripe,'require_owner',return_value=True),patch.object(stripe,'require_number',return_value=True),patch('urllib.request.urlopen') as call:
            stripe.handler.do_POST(h);call.assert_not_called()
        self.assertEqual(h.result[0],422)

    def test_paid_stored_invoice_uses_existing_data_without_current_rate_lookup(self):
        mod=module('generate');stored={'paid':True,'client':'Historical','taxrate':9.6,'invnum':'TEST','generator_request':True};h=handler(mod,{'invnum':'TEST'})
        with patch.object(mod,'require_owner',return_value=True),patch.object(mod,'stored_invoice',return_value=stored),patch.object(mod,'require_verified') as evidence,patch.object(mod,'generate_invoice_pdf',side_effect=RuntimeError('sentinel')) as pdf:
            mod.handler.do_POST(h);evidence.assert_not_called();self.assertEqual(pdf.call_args.args[0],stored)

    def test_unicode_pdf_name_produces_latin1_safe_http_header(self):
        value=content_disposition('SWJ_José — 家族_2020132.pdf')
        value.encode('latin1');self.assertIn("filename*=UTF-8''",value);self.assertIn('%E5%AE%B6',value)
        self.assertNotIn('\r',content_disposition('bad\r\nInjected: yes.pdf'))
        self.assertNotIn('\n',content_disposition('bad\r\nInjected: yes.pdf'))
        mod=module('generate');h=handler(mod,{'invnum':'TEST','client':'José — 家族','gs_invoice':True})
        h.wfile=io.BytesIO();h.out_headers={};h.send_response=lambda code:setattr(h,'status',code);h.send_header=lambda k,v:h.out_headers.update({k:v});h.end_headers=lambda:None
        with tempfile.NamedTemporaryFile(delete=False) as f:f.write(b'%PDF fixture');filename=f.name
        with patch.object(mod,'require_owner',return_value=True),patch.object(mod,'stored_invoice',return_value=None),patch.object(mod,'generate_invoice_pdf',return_value=filename):mod.handler.do_POST(h)
        self.assertEqual(h.status,200);h.out_headers['Content-Disposition'].encode('latin1')

    def test_legacy_address_parser_does_not_read_street_or_unit_number_as_zip(self):
        mod=module('webhook')
        for address,city,zipcode in [('13410 Main St, Tacoma WA 98402','Tacoma','98402'),('21628 Main St, Seattle WA 98104-1234','Seattle','98104'),('13410 Main St, Tacoma WA','Tacoma',''),('123 Main St Unit 13410','',''),('123 Main St, Tacoma 98402','Tacoma','98402')]:
            result=mod.tax_parse_address(address);self.assertEqual(result['city'],city);self.assertEqual(result['zip'],zipcode)
        self.assertEqual(mod.tax_resolve_location({'address':'bad'},{'city':'Seattle','zip':'98104','price_led_tax':False}),{'city':'Seattle','zip':'98104','ok':True})


if __name__=='__main__':unittest.main()
