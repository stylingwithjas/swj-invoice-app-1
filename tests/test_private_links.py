import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from lib import private_links as links, public_invoice_http as api
from tests.test_admin_auth import FakeHandler

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://preview.example.test'
ENV = {'SUPABASE_KEY':'synthetic-service-key-only-32-bytes','VERCEL_ENV':'preview'}
ROW = {'invnum':'2020132','client':'Fixture Client','address':'Synthetic property','data':{
    'base_price':2650,'taxrate':10.55,'paid':False,'profit':900,'notes':'Private operations',
    'mover_name':'Fixture Crew','startdate':'2026-11-10','truck_pickup_time':'09:00',
    'cosigner':{'name':'Fixture Co','email':'fixture@example.test','role':'Agent'},
    'signatures':[{'role':'client','signed_by':'Fixture Client','signed_date':'2026-11-05','ip_address':'private','sig_image_b64':'secret'}],
    'addons':[{'desc':'Office','price':100,'internal_cost':10}],
    'confirmations':{},'client_email':'private@example.test'}}


def request(action, payload, headers=None):
    return FakeHandler('/api/webhook?public='+action, payload, {'Origin':ORIGIN,**(headers or {})})


class PrivateLinkTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV)
        env.start();self.addCleanup(env.stop)

    def test_token_binds_invoice_role_kind_origin_and_expiry(self):
        token = links.issue('2020132','client','proposal',ORIGIN,now=1000)
        self.assertTrue(links.verify(token,'2020132','client','proposal',ORIGIN,now=1001))
        for invoice,role,kind,origin in [('2020133','client','proposal',ORIGIN),('2020132','cosigner','proposal',ORIGIN),('2020132','mover1','confirm',ORIGIN),('2020132','client','proposal','https://other.test')]:
            self.assertFalse(links.verify(token,invoice,role,kind,origin,now=1001))
        self.assertFalse(links.verify(token,'2020132','client','proposal',ORIGIN,now=1000+links.LIFETIME))
        self.assertFalse(links.verify(token+'x','2020132','client','proposal',ORIGIN,now=1001))
        with patch.dict(os.environ,{'VERCEL_ENV':'production'}):
            self.assertFalse(links.verify(token,'2020132','client','proposal',ORIGIN,now=1001))

    def test_jasmine_password_rotation_does_not_break_client_links(self):
        token = links.issue('2020132','client','proposal',ORIGIN)
        with patch.dict(os.environ,{'SWJ_ADMIN_PASSWORD':'a-different-owner-password'}):
            self.assertTrue(links.verify(token,'2020132','client','proposal',ORIGIN))

    def test_email_stamps_only_first_party_links_and_preserves_stripe(self):
        text = ORIGIN+'/proposal.html?inv=2020132&role=cosigner\nhttps://buy.stripe.com/fixture\nhttps://other.test/proposal.html?inv=1'
        result = links.secure_email_links(text,ORIGIN)
        from urllib.parse import parse_qs,urlsplit
        query = parse_qs(urlsplit(result.splitlines()[0]).query)
        self.assertTrue(links.verify(query['token'][0],'2020132','cosigner','proposal',ORIGIN))
        self.assertEqual(result.splitlines()[1:],text.splitlines()[1:])

    def test_no_token_never_reads_database(self):
        for action,role in [('invoice','client'),('confirm','mover1'),('respond','mover1')]:
            h = request(action,{'invnum':'2020132','role':role})
            with patch.object(api.admin_auth,'authorized',return_value=False),patch.object(api,'db') as db:
                api.handle(h,'POST');db.assert_not_called()
            self.assertEqual(h.code,403)

    def test_valid_link_returns_only_client_fields(self):
        token = links.issue('2020132','client','proposal',ORIGIN)
        h = request('invoice',{'invnum':'2020132','role':'client','token':token})
        with patch.object(api,'db',return_value=[ROW]): api.handle(h,'POST')
        self.assertEqual(h.code,200)
        data = h.data()['invoice']['data']
        self.assertEqual(data['base_price'],2650)
        for field in ('profit','notes','mover_name','client_email'): self.assertNotIn(field,data)
        self.assertNotIn('ip_address',data['signatures'][0])
        self.assertNotIn('sig_image_b64',data['signatures'][0])
        self.assertNotIn('internal_cost',data['addons'][0])
        self.assertEqual(h.out_headers['Referrer-Policy'],'no-referrer')

    def test_crew_token_cannot_read_client_proposal(self):
        token = links.issue('2020132','mover1','confirm',ORIGIN)
        h = request('invoice',{'invnum':'2020132','role':'mover1','kind':'confirm','token':token})
        with patch.object(api,'db') as db:
            api.handle(h,'POST');db.assert_not_called()
        self.assertEqual(h.code,403)

    def test_crew_write_is_server_scoped_and_keeps_financials(self):
        token = links.issue('2020132','mover1','confirm',ORIGIN)
        h = request('respond',{'invnum':'2020132','role':'mover1','status':'confirmed','token':token,
                             'data':{'paid':True,'base_price':1},'to':'attacker@example.test'})
        writes=[]
        def db(method,query,body=None):
            if method=='GET':return [ROW]
            writes.append((query,body))
            return [{**ROW,'data':body['data']}]
        with patch.object(api,'db',side_effect=db),patch.object(api,'notify_crew') as notify:
            api.handle(h,'POST')
        self.assertEqual(h.code,200)
        self.assertEqual(writes[0][1]['data']['base_price'],2650)
        self.assertFalse(writes[0][1]['data']['paid'])
        self.assertEqual(writes[0][1]['data']['notes'],'Private operations')
        self.assertEqual(set(writes[0][1]['data']['confirmations']),{'mover1'})
        notify.assert_called_once()
        self.assertNotIn('base_price',h.data()['invoice']['data'])

    def test_crew_concurrent_change_and_invalid_status_do_not_notify(self):
        token = links.issue('2020132','mover1','confirm',ORIGIN)
        for status,expected in [('confirmed',409),('paid',400)]:
            h=request('respond',{'invnum':'2020132','role':'mover1','status':status,'token':token})
            with patch.object(api,'db',side_effect=[[ROW],[]]),patch.object(api,'notify_crew') as notify:
                api.handle(h,'POST');notify.assert_not_called()
            self.assertEqual(h.code,expected)

    def test_mint_requires_owner_and_never_reads_public_data(self):
        h=request('mint',{'invnum':'2020132','role':'client'})
        with patch.object(api,'require_owner',return_value=False),patch.object(api,'db') as db:
            api.handle(h,'POST');db.assert_not_called()
        h=request('mint',{'invnum':'2020132','role':'cosigner'})
        with patch.object(api,'require_owner',return_value=True),patch.object(api,'db') as db:
            api.handle(h,'POST');db.assert_not_called()
        self.assertEqual(h.code,200)
        self.assertIn('token=',h.data()['url'])

    def test_signature_rejects_missing_wrong_invoice_and_wrong_role_before_lookup(self):
        spec=importlib.util.spec_from_file_location('scoped_sign',ROOT/'api/sign.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for token,invnum,role in [(None,'2020132','client'),(links.issue('2020132','client','proposal',ORIGIN),'2020133','client'),(links.issue('2020132','client','proposal',ORIGIN),'2020132','cosigner')]:
            h=request('unused',{'invnum':invnum,'role':role,'token':token,'signed_by':'Fixture'})
            h._respond=lambda code,data:api.response(h,code,data)
            with patch.object(module,'supabase_get_invoice') as db:
                module.handler.do_POST(h);db.assert_not_called()
            self.assertEqual(h.code,403)

    def test_refresh_contract_link_requires_owner(self):
        spec=importlib.util.spec_from_file_location('refresh_sign',ROOT/'api/sign.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        h=request('unused',{'action':'refresh_link','invnum':'2020132'})
        with patch.object(module,'require_owner',return_value=False),patch.object(module,'_sign_contract_url') as sign:
            module.handler.do_POST(h);sign.assert_not_called()

    def test_valid_signature_token_reaches_existing_idempotent_flow(self):
        spec=importlib.util.spec_from_file_location('valid_sign',ROOT/'api/sign.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        token=links.issue('2020132','client','proposal',ORIGIN)
        h=request('unused',{'invnum':'2020132','role':'client','token':token,'signed_by':'Fixture Client','signed_date':'2026-11-05','agreed':True})
        h.client_address=('127.0.0.1',10000)
        h._respond=lambda code,data:api.response(h,code,data)
        with patch.object(module,'supabase_get_invoice',return_value=ROW),patch.object(module,'supabase_update_invoice') as write,patch.object(module,'build_full_signed_contract') as pdf:
            module.handler.do_POST(h)
            write.assert_not_called();pdf.assert_not_called()
        self.assertEqual(h.code,200)
        self.assertTrue(h.data()['signed'])
        self.assertFalse(h.data()['fully_signed'])

    def test_both_private_signers_complete_existing_workflow_without_financial_changes(self):
        import copy
        from urllib.parse import parse_qs,urlsplit
        spec=importlib.util.spec_from_file_location('both_signers',ROOT/'api/sign.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        row=copy.deepcopy(ROW);row['data']['signatures']=[]
        initial=copy.deepcopy(row['data'])
        def update(invnum,patch_data):
            self.assertEqual(invnum,'2020132');row.update(patch_data)
        with patch.object(module,'supabase_get_invoice',side_effect=lambda inv:copy.deepcopy(row)),patch.object(module,'supabase_update_invoice',side_effect=update),patch.object(module,'send_pending_signature_email') as pending,patch.object(module,'build_full_signed_contract',return_value=b'%PDF-fixture') as pdf,patch.object(module,'supabase_upload_pdf',return_value='https://fixture.test/signed.pdf'),patch.object(module,'send_signed_email') as email:
            token=links.issue('2020132','client','proposal',ORIGIN)
            h=request('unused',{'invnum':'2020132','role':'client','token':token,'signed_by':'Fixture Client','signed_date':'2026-11-05','agreed':True,'sig_image':'fixture-client-image'})
            h.client_address=('127.0.0.1',10000);h._respond=lambda code,data:api.response(h,code,data)
            module.handler.do_POST(h)
            self.assertEqual(h.code,200);self.assertFalse(h.data()['fully_signed']);pdf.assert_not_called()
            pending.assert_called_once()
            query=parse_qs(urlsplit(pending.call_args.args[-1]).query)
            co_token=query['token'][0]
            self.assertTrue(links.verify(co_token,'2020132','cosigner','proposal',ORIGIN))
            h=request('unused',{'invnum':'2020132','role':'cosigner','token':co_token,'signed_by':'Fixture Co','signed_date':'2026-11-05','agreed':True,'sig_image':'fixture-co-image'})
            h.client_address=('127.0.0.1',10000);h._respond=lambda code,data:api.response(h,code,data)
            module.handler.do_POST(h)
            self.assertEqual(h.code,200);self.assertTrue(h.data()['fully_signed'])
            pdf.assert_called_once();self.assertEqual(email.call_count,3)
        for key in ('base_price','taxrate','paid','profit','notes','addons'):
            self.assertEqual(row['data'][key],initial[key])
        self.assertEqual(row['data']['stage'],'confirmed')
        self.assertEqual({s['role'] for s in row['data']['signatures']},{'client','cosigner'})
        self.assertTrue(all('sig_image_b64' not in s for s in row['data']['signatures']))

    def test_owner_email_stamps_private_link_without_changing_stripe_link(self):
        spec=importlib.util.spec_from_file_location('private_email',ROOT/'api/email.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        h=request('unused',{'to':'fixture@example.test','message':ORIGIN+'/proposal.html?inv=2020132\nhttps://buy.stripe.com/fixture'})
        h._respond=lambda code,data:api.response(h,code,data)
        with patch.object(module,'require_owner',return_value=True),patch.dict(os.environ,{'EMAIL_PASSWORD':'fixture-only'}),patch.object(module.smtplib,'SMTP_SSL') as smtp:
            module.handler.do_POST(h)
        self.assertEqual(h.code,200)
        from email import message_from_string
        from urllib.parse import parse_qs,urlsplit
        message=message_from_string(smtp.return_value.__enter__.return_value.sendmail.call_args.args[2])
        plain=next(part for part in message.walk() if part.get_content_type()=='text/plain').get_payload(decode=True).decode()
        query=parse_qs(urlsplit(plain.splitlines()[0]).query)
        self.assertTrue(links.verify(query['token'][0],'2020132','client','proposal',ORIGIN))
        self.assertEqual(plain.splitlines()[1],'https://buy.stripe.com/fixture')

    def test_manual_report_send_requires_owner_and_stays_out_of_webhooks(self):
        spec=importlib.util.spec_from_file_location('manual_job',ROOT/'api/webhook.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for allowed in (False,True):
            h=FakeHandler('/api/webhook?job=monthly_tax_report',{}, {'Origin':ORIGIN})
            h._run_scheduled_job=lambda:setattr(h,'ran_job',True)
            with patch('lib.owner_guard.require_owner',return_value=allowed),patch.object(module,'verify_signature') as verify:
                module.handler.do_POST(h);verify.assert_not_called()
            self.assertEqual(getattr(h,'ran_job',False),allowed)


if __name__=='__main__':unittest.main()
