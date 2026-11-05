import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
from lib import invoice_numbers as numbers
from tests.test_admin_auth import FakeHandler

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location('number_test_'+name,ROOT/'api'/f'{name}.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class NumberTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'SUPABASE_KEY':'fixture-only-number-key-long-enough'})
        env.start();self.addCleanup(env.stop)

    def metadata(self):
        rid=str(uuid.uuid4());return {'mode':'allocated','invnum':'2020132','request_id':rid,'proof':numbers.signature(rid,'2020132')}

    def test_allocated_proof_binds_number_and_request(self):
        metadata=self.metadata();self.assertTrue(numbers.require_number({'invnum':'2020132','invoice_number':metadata}))
        self.assertTrue(numbers.require_number({'invnum':'2020132-PETDEP','invoice_number':metadata},payment=True))
        for target,patch_data in [('2020133',{}),('2020132',{'proof':'fake'}),('2020132',{'request_id':str(uuid.uuid4())})]:
            with self.assertRaises(ValueError):numbers.require_number({'invnum':target,'invoice_number':{**metadata,**patch_data}})

    def test_existing_requires_verified_saved_invoice(self):
        data={'invnum':'2020131','invoice_number':{'invnum':'2020131','mode':'existing'}}
        self.assertTrue(numbers.require_number(data,saved={'client':'Fixture'}))
        with self.assertRaises(ValueError):numbers.require_number(data,saved=None)
        # A legacy draft flag on an issued physical row must not force renumbering.
        self.assertTrue(numbers.require_number(data,saved={'_is_draft':True}))
        with self.assertRaises(ValueError):numbers.require_number({'invnum':'DRAFT-2020131','invoice_number':{'invnum':'DRAFT-2020131','mode':'existing'}},saved={})

    def test_counter_requires_owner_before_reading_body(self):
        api=module('counter');h=FakeHandler('/api/counter',{'action':'reserve','request_id':str(uuid.uuid4())})
        with patch.object(api,'require_owner',return_value=False),patch.object(api,'reserve') as rpc:
            api.handler.do_POST(h);rpc.assert_not_called()
        self.assertEqual(h.rfile.tell(),0)

    def test_legacy_counter_writes_do_not_change_state(self):
        api=module('counter');h=FakeHandler('/api/counter',{'value':1})
        with patch.object(api,'require_owner',return_value=True),patch.object(api,'reserve') as rpc:
            api.handler.do_POST(h);rpc.assert_not_called()
        self.assertEqual(h.code,409)

    def test_number_denial_precedes_pdf_save_and_stripe(self):
        api=module('generate');h=api.handler.__new__(api.handler);raw=json.dumps({'generator_request':True,'invnum':'2020132'}).encode();h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw);h._error=lambda code,data:setattr(h,'result',(code,data))
        with patch.object(api,'stored_invoice',return_value=None),patch.object(api,'require_verified') as tax,patch.object(api,'generate_invoice_pdf') as pdf:
            h.do_POST();tax.assert_not_called();pdf.assert_not_called()
        self.assertEqual(h.result[0],422)
        api=module('stripe');h=api.handler.__new__(api.handler);raw=json.dumps({'source':'invoice_generator','invnum':'2020132'}).encode();h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw);h._respond=lambda code,data:setattr(h,'result',(code,data))
        with patch('urllib.request.urlopen') as call:h.do_POST();call.assert_not_called()
        self.assertEqual(h.result[0],422)

    def test_rpc_uses_fixed_server_endpoint_and_returns_only_proof(self):
        rid=str(uuid.uuid4())
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with patch('urllib.request.urlopen',return_value=Reply(json.dumps({'request_id':rid,'invnum':'2020132'}).encode())) as call:
            result=numbers.reserve(rid)
        self.assertEqual(call.call_args.args[0].full_url,numbers.BASE_URL+'/rest/v1/rpc/reserve_invoice_number')
        self.assertEqual(result['proof'],numbers.signature(rid,'2020132'))
        self.assertNotIn(os.environ['SUPABASE_KEY'],json.dumps(result))

    def test_old_editor_cannot_create_unreserved_new_payment(self):
        api=module('stripe');h=api.handler.__new__(api.handler);raw=json.dumps({'invnum':'2020999','grand':100}).encode();h.headers={'Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw);h._respond=lambda code,data:setattr(h,'result',(code,data))
        with patch.dict(os.environ,{'STRIPE_SECRET_KEY':'fixture-only'}),patch.object(api,'existing_payment_parent',return_value=None),patch('urllib.request.urlopen') as stripe:
            h.do_POST();stripe.assert_not_called()
        self.assertEqual(h.result[0],422)

    def test_existing_extension_resolves_its_original_parent_without_changing_it(self):
        parent={'paid':True,'base_price':2650}
        with patch('lib.invoice_protection.stored_invoice',side_effect=[None,parent]) as lookup:
            self.assertIs(numbers.existing_payment_parent('2020131-EXT1'),parent)
        self.assertEqual([call.args[0] for call in lookup.call_args_list],['2020131-EXT1','2020131'])


if __name__=='__main__':unittest.main()
