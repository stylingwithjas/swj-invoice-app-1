import io
import json
import unittest
from unittest.mock import patch
from lib import owner_data_http as api
from tests.test_admin_auth import FakeHandler


class Upstream(io.BytesIO):
    code = 200
    headers = {'Content-Range':'0-0/1'}


class OwnerDataTests(unittest.TestCase):
    def test_unauthorized_request_never_reaches_database(self):
        h = FakeHandler('/api/webhook?data=invoices')
        with patch.object(api, 'require_owner', return_value=False), patch('urllib.request.urlopen') as call:
            api.handle(h, 'GET')
            call.assert_not_called()

    def test_server_only_tables_and_rpc_rejected(self):
        for table in ('plaid_items','admin_revoked_sessions','rpc/arbitrary','../invoices','invoices/../../auth/users'):
            h = FakeHandler('/api/webhook?data='+table)
            with patch.object(api, 'require_owner', return_value=True), patch('urllib.request.urlopen') as call:
                api.handle(h, 'GET')
                call.assert_not_called()
            self.assertEqual(h.code, 404)

    def test_relationship_cannot_bypass_table_allowlist(self):
        h = FakeHandler('/api/webhook?data=invoices&query=select%3D*%2Cplaid_items(*)')
        with patch.object(api, 'require_owner', return_value=True), patch('urllib.request.urlopen') as call:
            api.handle(h, 'GET')
            call.assert_not_called()
        self.assertEqual(h.code, 400)

    def test_service_key_and_fixed_origin_used_only_upstream(self):
        h = FakeHandler('/api/webhook?data=invoices&query=select%3Dinvnum', headers={'Authorization':'Bearer attacker', 'apikey':'attacker'})
        with patch.object(api, 'require_owner', return_value=True), patch.dict('os.environ', {'SUPABASE_KEY':'fixture-service'}), patch('urllib.request.urlopen', return_value=Upstream(b'[{"invnum":"TEST"}]')) as call:
            api.handle(h, 'GET')
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, api.BASE_URL+'/rest/v1/invoices?select=invnum')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-service')
        self.assertEqual(h.data(), [{'invnum':'TEST'}])
        self.assertEqual(h.out_headers['Content-Range'], '0-0/1')
        self.assertNotIn('fixture-service', h.wfile.getvalue().decode())

    def test_body_limit_and_read_only_view(self):
        for method, resource, length, expected in [('POST','invoices',api.MAX_BODY+1,413),('DELETE','plaid_card_summary',0,405)]:
            h = FakeHandler('/api/webhook?data='+resource, headers={'Content-Length':str(length)})
            with patch.object(api, 'require_owner', return_value=True), patch('urllib.request.urlopen') as call:
                api.handle(h, method)
                call.assert_not_called()
            self.assertEqual(h.code, expected)


if __name__ == '__main__': unittest.main()
