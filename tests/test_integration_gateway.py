"""Real HTTP boundary checks for the local website-to-Yudao gateway."""
import importlib.util
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location('club_preview', Path(__file__).resolve().parents[1] / 'scripts' / 'preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'path': self.path, 'headers': dict(self.headers), 'method': self.command}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Set-Cookie', 'synthetic-upstream-cookie=value; HttpOnly')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET
    do_PUT = do_GET

    def log_message(self, *args):
        pass


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        cls.gateway = ThreadingHTTPServer(('127.0.0.1', 0), preview.Handler)
        cls.gateway.yudao_port = cls.gateway.api_port = cls.upstream.server_port
        cls.gateway.public_tenant_id = 1
        cls.gateway.admin_url = 'http://127.0.0.1:5173/login?redirect=/leanclub/content'
        cls.threads = []
        for server in (cls.upstream, cls.gateway):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gateway, cls.upstream):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)

    def request(self, method, path, headers=None):
        connection = HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_public_member_boundary_overrides_tenant_and_drops_cookies(self):
        status, headers, raw = self.request('GET', '/app-api/leanclub/profile/get', {
            'Authorization': 'Bearer synthetic-member-token',
            'Cookie': 'old-admin=synthetic', 'tenant-id': '999', 'X-Forwarded-For': 'fake-ip'
        })
        self.assertEqual(status, 200)
        received = {k.lower(): v for k, v in json.loads(raw)['headers'].items()}
        self.assertEqual(received['tenant-id'], '1')
        self.assertEqual(received['authorization'], 'Bearer synthetic-member-token')
        self.assertNotIn('cookie', received)
        self.assertNotIn('x-forwarded-for', received)
        self.assertNotIn('Set-Cookie', headers)

    def test_existing_campaign_cookie_stays_on_its_own_service(self):
        status, headers, raw = self.request('GET', '/api/campaign/session', {'Cookie': 'synthetic=old'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['headers']['Cookie'], 'synthetic=old')
        self.assertIn('Set-Cookie', headers)

    def test_member_auth_must_use_capability_checked_facade(self):
        status, _, _ = self.request('POST', '/app-api/member/auth/send-sms-code')
        self.assertEqual(status, 404)
        status, _, raw = self.request('POST', '/app-api/leanclub/auth/send-sms-code')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['method'], 'POST')

    def test_no_general_admin_proxy_or_encoded_path_escape(self):
        for path in ('/admin-api/system/user/page', '/app-api/system/user/page',
                     '/app-api/leanclub/../system/user', '/app-api/leanclub/%2e%2e/system/user',
                     '/app-api/leanclub%2fprofile/get'):
            with self.subTest(path=path):
                status, _, _ = self.request('GET', path)
                self.assertIn(status, (400, 404))

    def test_profile_put_and_school_query_forward_without_losing_params(self):
        for method, path in [('PUT', '/app-api/leanclub/profile/update'),
                             ('GET', '/app-api/leanclub/school/list?keyword=test&pageNo=2')]:
            status, _, raw = self.request(method, path, {'Authorization': 'Bearer synthetic-member-token'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['path'], path)
            self.assertEqual(json.loads(raw)['method'], method)

    def test_exact_routes_reject_unknown_methods_query_tokens_and_ambiguous_parameters(self):
        for method, path in [
            ('POST', '/app-api/leanclub/profile/get'),
            ('GET', '/app-api/leanclub/unknown'),
            ('GET', '/app-api/leanclub/orders/page?token=synthetic'),
            ('GET', '/app-api/leanclub/profile/get?%74oken=synthetic'),
            ('GET', '/app-api/leanclub/orders/page?pageNo=1&pageNo=2'),
            ('GET', '/app-api/leanclub/orders/page?memberId=1'),
            ('POST', '/app-api/leanclub/events/' + 'a' * 32 + '/register?paid=true'),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], 404)

    def test_new_member_routes_require_bearer_and_preserve_only_allowed_queries(self):
        paths = [('GET', '/app-api/leanclub/orders/page?pageNo=1&pageSize=20&status=all'),
                 ('GET', '/app-api/leanclub/orders/get?id=' + 'a' * 32),
                 ('POST', '/app-api/leanclub/orders/cancel'),
                 ('POST', '/app-api/leanclub/events/' + 'a' * 32 + '/register')]
        for method, path in paths:
            self.assertEqual(self.request(method, path)[0], 401)
            self.assertEqual(self.request(method, path, {'Authorization': 'synthetic-unprefixed'})[0], 401)
            status, _, raw = self.request(method, path, {'Authorization': 'Bearer synthetic-member-token'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['path'], path)

    def test_admin_entry_goes_to_existing_yudao_login(self):
        for path in ('/admin', '/admin/'):
            status, headers, raw = self.request('GET', path)
            self.assertEqual(status, 302)
            self.assertEqual(headers['Location'], self.gateway.admin_url)
            self.assertEqual(raw, b'')

    def test_staff_checkin_moves_to_original_admin_without_copying_query_secrets(self):
        status, headers, raw = self.request('GET', '/check-in?ticket=synthetic-do-not-forward')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], 'http://127.0.0.1:5173/leanclub/registrations')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(raw, b'')

    def test_staff_checkin_keeps_console_mount_for_login_or_base_url(self):
        original = self.gateway.admin_url
        try:
            for admin_url in ('https://www.leanschool.net/console/login?redirect=/leanclub/content',
                              'https://www.leanschool.net/console/', 'https://www.leanschool.net/console'):
                self.gateway.admin_url = admin_url
                for method in ('GET', 'HEAD'):
                    status, headers, raw = self.request(method, '/check-in?ticket=synthetic-no-copy')
                    self.assertEqual(status, 302)
                    self.assertEqual(headers['Location'], 'https://www.leanschool.net/console/leanclub/registrations')
                    self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
                    self.assertEqual(raw, b'')
        finally:
            self.gateway.admin_url = original


if __name__ == '__main__':
    unittest.main()
