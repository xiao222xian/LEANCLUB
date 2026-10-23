"""Production public facade boundaries with an isolated loopback Java stub."""
import importlib.util
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('production_public_gateway', ROOT / 'deploy/ubuntu/public-gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
from public_app_gateway import MAX_BODY, MAX_RESPONSE

ORIGIN = 'https://www.leanschool.net'
LEGACY = 'https://boji.tensorbot.xyz'


class JavaStub(BaseHTTPRequestHandler):
    def respond(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length)
        record = {'method': self.command, 'path': self.path,
                  'headers': dict(self.headers), 'body': body.decode()}
        self.server.received.append(record)
        mode = self.server.mode
        status = 302 if mode == 'redirect' else 403 if mode == 'forbidden' else 200
        payload = b'x' * (MAX_RESPONSE + 1) if mode == 'oversize' else json.dumps(record).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html' if mode == 'html' else 'application/json')
        self.send_header('Set-Cookie', 'synthetic-java-session=value; HttpOnly')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Location', 'https://foreign.test/synthetic')
        self.send_header('Retry-After', '4')
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = respond
    do_POST = respond
    do_PUT = respond

    def log_message(self, *_):
        pass


class GatewayHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class QuietHandler(gateway.Handler):
            def log_message(self, *_):
                pass
        cls.java = ThreadingHTTPServer(('127.0.0.1', 0), JavaStub)
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        primary, compatible, _, _, tenant = gateway.configuration({
            'CLUB_ORIGIN': ORIGIN, 'CLUB_COMPAT_ORIGINS': LEGACY,
            'LEANCLUB_PUBLIC_TENANT_ID': '7'})
        cls.httpd.primary_api, cls.httpd.compatible_apis = primary, compatible
        cls.httpd.public_tenant_id = tenant
        cls.httpd.yudao_port = cls.java.server_port
        cls.threads = []
        for server in (cls.java, cls.httpd):
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.httpd, cls.java):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=3)

    def setUp(self):
        self.java.received = []
        self.java.mode = 'normal'

    def request(self, method, path, headers=(), body=b''):
        outgoing = [('Host', 'www.leanschool.net')]
        if method in ('POST', 'PUT'):
            outgoing += [('Origin', ORIGIN), ('Content-Type', 'application/json'), ('Content-Length', str(len(body)))]
        for name, value in headers:
            if value is None:
                outgoing = [(key, previous) for key, previous in outgoing if key.lower() != name.lower()]
            else:
                outgoing.append((name, value))
        connection = HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in outgoing:
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw) if raw else None
        finally:
            connection.close()

    def test_fixed_tenant_member_bearer_and_cookie_response_isolation(self):
        status, headers, result = self.request('GET', '/app-api/leanclub/profile/get', [
            ('Authorization', 'Bearer synthetic-member'), ('tenant-id', '999'),
            ('Cookie', 'synthetic-admin=private'), ('X-Real-IP', '192.0.2.1'), ('X-Forwarded-For', '192.0.2.2')])
        self.assertEqual(status, 200)
        received = {key.lower(): value for key, value in result['headers'].items()}
        self.assertEqual(received['tenant-id'], '7')
        self.assertEqual(received['authorization'], 'Bearer synthetic-member')
        for name in ('cookie', 'x-real-ip', 'x-forwarded-for'):
            self.assertNotIn(name, received)
        for name in ('Set-Cookie', 'Access-Control-Allow-Origin', 'Access-Control-Allow-Credentials', 'Location'):
            self.assertNotIn(name, headers)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Retry-After'], '4')

    def test_no_member_auth_no_java_request_and_duplicate_bearer_fails(self):
        for auth in ([], [('Authorization', 'Basic synthetic')],
                     [('Authorization', 'Bearer a'), ('Authorization', 'Bearer b')]):
            self.assertEqual(self.request('GET', '/app-api/leanclub/orders/page', auth)[0], 401)
        self.assertEqual(self.java.received, [])
        self.java.mode = 'forbidden'
        self.assertEqual(self.request('GET', '/app-api/leanclub/profile/get', [('Authorization', 'Bearer synthetic-staff')])[0], 403)

    def test_exact_methods_paths_queries_and_no_static_admin_or_internal_proxy(self):
        for method, path in [('GET', '/'), ('GET', '/admin-api/system/user/page'),
                             ('GET', '/api/campaign'), ('POST', '/internal/leanclub/operations'),
                             ('GET', '/app-api/member/auth/login'), ('GET', '/app-api/leanclub/../system/user'),
                             ('GET', '/app-api/leanclub/%2e%2e/system/user'),
                             ('GET', '/app-api/leanclub%2fschool/list'),
                             ('POST', '/app-api/leanclub/school/list'), ('HEAD', '/app-api/leanclub/school/list'),
                             ('DELETE', '/app-api/leanclub/profile/get'), ('OPTIONS', '/app-api/leanclub/profile/get'),
                             ('GET', '/app-api/leanclub/school/list?token=secret'),
                             ('GET', '/app-api/leanclub/school/list?%74oken=secret'),
                             ('GET', '/app-api/leanclub/school/list?pageNo=1&pageNo=2'),
                             ('GET', '/app-api/leanclub/school/list?keyword=%FF'),
                             ('POST', '/app-api/leanclub/events/' + 'a' * 32 + '/register?paid=true')]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], 404)
        self.assertEqual(self.java.received, [])

    def test_public_queries_and_private_json_forward_unchanged(self):
        path = '/app-api/leanclub/school/list?keyword=%E6%B7%B1%E5%9C%B3&pageNo=2&pageSize=20'
        self.assertEqual(self.request('GET', path)[2]['path'], path)
        body = b'{"schoolId":123}'
        result = self.request('PUT', '/app-api/leanclub/profile/update', [('Authorization', 'Bearer synthetic-member')], body)[2]
        self.assertEqual(result['body'].encode(), body)
        self.assertEqual(result['method'], 'PUT')
        registration = '/app-api/leanclub/events/' + 'a' * 32 + '/register'
        self.assertEqual(self.request('POST', registration, [('Authorization', 'Bearer synthetic-member')], b'{}')[0], 200)

    def test_write_origin_follows_host_without_cross_domain_permission(self):
        endpoint = '/app-api/leanclub/auth/send-sms-code'
        old_host = [('Host', None), ('Host', 'boji.tensorbot.xyz')]
        self.assertEqual(self.request('POST', endpoint, old_host)[0], 403)
        old_source = old_host + [('Origin', None), ('Origin', LEGACY)]
        self.assertEqual(self.request('POST', endpoint, old_source)[0], 200)
        for headers in ([('Origin', None), ('Origin', 'null')], [('Sec-Fetch-Site', 'cross-site')],
                        [('Origin', ORIGIN)], [('Origin', None), ('Referer', LEGACY + '/join')]):
            self.assertEqual(self.request('POST', endpoint, headers)[0], 403)
        self.assertEqual(self.request('POST', endpoint, [('Origin', None), ('Referer', ORIGIN + '/join')])[0], 200)
        self.assertEqual(len(self.java.received), 2)

    def test_unique_host_and_no_forwarded_host_override(self):
        for headers, expected in [([('Host', 'boji.tensorbot.xyz')], 400),
                                  ([('Host', None)], 400),
                                  ([('Host', None), ('Host', 'foreign.test'), ('X-Forwarded-Host', 'www.leanschool.net')], 421)]:
            self.assertEqual(self.request('GET', '/app-api/leanclub/site/published', headers)[0], expected)
        self.assertEqual(self.java.received, [])

    def test_bounded_body_duplicate_security_headers_and_no_get_body(self):
        public = '/app-api/leanclub/auth/send-sms-code'
        self.assertEqual(self.request('POST', public, [('Content-Length', None), ('Content-Length', str(MAX_BODY + 1))])[0], 413)
        self.assertEqual(self.request('POST', public, [('Content-Length', '0')])[0], 400)
        self.assertEqual(self.request('POST', public, [('Content-Type', 'application/json')])[0], 400)
        self.assertEqual(self.request('POST', public, [('Transfer-Encoding', 'chunked')])[0], 400)
        self.assertEqual(self.request('GET', '/app-api/leanclub/school/list', [('Content-Length', '1')], b'x')[0], 400)
        self.assertEqual(self.java.received, [])

    def test_upstream_redirect_html_oversize_and_outage_fail_closed(self):
        for mode in ('redirect', 'html', 'oversize'):
            self.java.mode = mode
            status, headers, _ = self.request('GET', '/app-api/leanclub/site/published')
            self.assertEqual(status, 502)
            self.assertNotIn('Location', headers)
            self.assertNotIn('Set-Cookie', headers)
        original = self.httpd.yudao_port
        try:
            self.httpd.yudao_port = 0
            self.assertEqual(self.request('GET', '/app-api/leanclub/site/published')[0], 502)
        finally:
            self.httpd.yudao_port = original

    def test_health_and_configuration_open_no_database(self):
        with patch('sqlite3.connect', side_effect=AssertionError('Gateway must not open a DB')):
            primary, compatible, port, java, tenant = gateway.configuration({'CLUB_ORIGIN': ORIGIN, 'CLUB_COMPAT_ORIGINS': LEGACY})
            self.assertEqual((port, java, tenant), (5382, 48080, 1))
            self.assertIsNone(primary.store)
            self.assertIsNone(compatible[0].store)
            self.assertEqual(self.request('GET', '/healthz')[0], 200)
        self.assertEqual(self.java.received, [])
        for invalid in ({}, {'CLUB_ORIGIN': 'http://localhost:4180'},
                        {'CLUB_ORIGIN': ORIGIN, 'LEANCLUB_PUBLIC_TENANT_ID': '0'},
                        {'CLUB_ORIGIN': ORIGIN, 'LEANCLUB_PUBLIC_GATEWAY_PORT': '48080'},
                        {'CLUB_ORIGIN': ORIGIN, 'LEANCLUB_YUDAO_PORT': '65536'}):
            with self.assertRaises(ValueError):
                gateway.configuration(invalid)


if __name__ == '__main__':
    unittest.main()
