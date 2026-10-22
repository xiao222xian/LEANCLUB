"""Origin validation and real preview-proxy regressions; no database is opened."""
from email.message import Message
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from campaign_http import CampaignAPI
from store import Problem


def request_headers(pairs=()):
    headers = Message()
    for name, value in pairs:
        headers[name] = value
    return SimpleNamespace(headers=headers)


class CampaignOriginTests(unittest.TestCase):
    def setUp(self):
        self.origin = 'http://127.0.0.1:4180'
        # Origin checks do not use the store. No real or temporary DB is needed.
        self.api = CampaignAPI(None, self.origin, allow_local_setup=True)

    def assertAccepted(self, api, pairs):
        self.assertIsNone(api.require_origin(request_headers(pairs)))

    def assertRejected(self, api, pairs):
        with self.assertRaises(Problem) as caught:
            api.require_origin(request_headers(pairs))
        self.assertEqual(caught.exception.status, 403)

    def test_missing_origin_accepts_same_origin_referer_with_or_without_fetch_metadata(self):
        for site in (None, 'same-origin'):
            pairs = [('Referer', self.origin + '/admin?panel=posters')]
            if site is not None:
                pairs.append(('Sec-Fetch-Site', site))
            with self.subTest(site=site):
                self.assertAccepted(self.api, pairs)

    def test_explicit_origin_never_falls_back_to_same_origin_referer(self):
        for origin in ('https://foreign.test', 'null', '', self.origin + '/', self.origin + '/admin'):
            with self.subTest(origin=origin):
                self.assertRejected(self.api, [('Origin', origin), ('Referer', self.origin + '/admin')])

    def test_rejects_cross_port_scheme_spoofed_host_and_userinfo(self):
        origins = (
            'http://127.0.0.1:4181', 'https://127.0.0.1:4180',
            'http://127.0.0.1.foreign.test:4180', 'http://localhost.foreign.test:4180',
            'http://127.0.0.1:4180@foreign.test', 'http://foreign.test@127.0.0.1:4180',
            'http://user:password@127.0.0.1:4180',
        )
        for origin in origins:
            with self.subTest(origin=origin, source='Origin'):
                self.assertRejected(self.api, [('Origin', origin)])
            with self.subTest(origin=origin, source='Referer'):
                self.assertRejected(self.api, [('Referer', origin + '/admin')])

    def test_rejects_invalid_ports_urls_and_control_characters(self):
        values = (
            'http://127.0.0.1:invalid', 'http://127.0.0.1:65536', 'http://127.0.0.1:-1',
            'http://[::1', 'file:///admin', '//127.0.0.1:4180/admin',
            self.origin + '#fragment', self.origin + '\t', '\n' + self.origin,
            self.origin + '\x7f',
        )
        for value in values:
            for header in ('Origin', 'Referer'):
                with self.subTest(value=value, header=header):
                    self.assertRejected(self.api, [(header, value)])

    def test_rejects_missing_both_source_headers_and_duplicates(self):
        for pairs in (
            [], [('Sec-Fetch-Site', 'same-origin')],
            [('Origin', self.origin), ('Origin', self.origin)],
            [('Referer', self.origin + '/admin'), ('Referer', self.origin + '/admin')],
        ):
            with self.subTest(pairs=pairs):
                self.assertRejected(self.api, pairs)

    def test_rejects_fetch_metadata_other_than_same_origin(self):
        for site in ('cross-site', 'same-site', 'none', '', 'same-origin, cross-site'):
            for source in (('Origin', self.origin), ('Referer', self.origin + '/admin')):
                with self.subTest(site=site, source=source[0]):
                    self.assertRejected(self.api, [source, ('Sec-Fetch-Site', site)])

    def test_normalizes_default_ports_and_hostname_case(self):
        for configured, submitted in (
            ('https://lean.example', 'https://LEAN.EXAMPLE:443'),
            ('https://lean.example:443', 'https://lean.example'),
            ('http://lean.example', 'http://LEAN.EXAMPLE:80'),
            ('http://lean.example:80', 'http://lean.example'),
        ):
            api = CampaignAPI(None, configured)
            with self.subTest(configured=configured, submitted=submitted):
                self.assertAccepted(api, [('Origin', submitted)])
                self.assertAccepted(api, [('Referer', submitted + '/admin')])

    def test_explicit_zero_port_is_rejected_instead_of_using_default_port(self):
        for scheme in ('http', 'https'):
            api = CampaignAPI(None, scheme + '://lean.example')
            zero_port = scheme + '://lean.example:0'
            with self.subTest(scheme=scheme):
                self.assertRejected(api, [('Origin', zero_port)])
                self.assertRejected(api, [('Referer', zero_port + '/admin')])
                with self.assertRaises(ValueError):
                    CampaignAPI(None, zero_port)

    def test_loopback_aliases_only_enabled_for_explicit_local_setup(self):
        for host in ('127.0.0.1', 'localhost', '[::1]'):
            origin = 'http://' + host + ':4180'
            with self.subTest(host=host):
                self.assertAccepted(self.api, [('Origin', origin)])
                self.assertAccepted(self.api, [('Referer', origin + '/admin')])
                if host != '127.0.0.1':
                    restricted = CampaignAPI(None, self.origin)
                    self.assertRejected(restricted, [('Origin', origin)])
                    self.assertRejected(restricted, [('Referer', origin + '/admin')])

    def test_production_uses_only_configured_origin_even_if_local_setup_flag_set(self):
        production = CampaignAPI(None, 'https://lean.example', allow_local_setup=True)
        self.assertFalse(production.local_setup)
        self.assertEqual(len(production.allowed_origins), 1)
        self.assertAccepted(production, [('Origin', 'https://lean.example')])
        for origin in ('https://localhost', 'https://127.0.0.1', 'https://[::1]',
                       'https://www.lean.example', 'http://lean.example', 'https://lean.example:444'):
            with self.subTest(origin=origin):
                self.assertRejected(production, [('Origin', origin)])
                self.assertRejected(production, [('Referer', origin + '/admin')])


class PreviewOriginProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('campaign_origin_preview', ROOT / 'scripts' / 'preview.py')
        preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preview)
        cls.received = []

        class StubBackend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                cls.received.append(self.headers)
                try:
                    cls.api.require_origin(self)
                    status, payload = 200, {'ok': True}
                except Problem as error:
                    status, payload = error.status, {'error': error.message}
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        class QuietPreview(preview.Handler):
            def log_message(self, *_):
                pass

        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), StubBackend)
        cls.proxy = ThreadingHTTPServer(('127.0.0.1', 0), QuietPreview)
        cls.proxy.api_port = cls.backend.server_port
        cls.origin = 'http://127.0.0.1:%s' % cls.proxy.server_port
        cls.api = CampaignAPI(None, cls.origin, allow_local_setup=True)
        cls.threads = []
        for server in (cls.backend, cls.proxy):
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.proxy, cls.backend):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=3)

    def post(self, headers):
        connection = HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=3)
        try:
            connection.request('POST', '/api/admin/setup', body=b'{}',
                               headers={'Content-Type': 'application/json', **headers})
            response = connection.getresponse()
            response.read()
            return response.status, self.received[-1]
        finally:
            connection.close()

    def test_forwards_referer_and_fetch_metadata_without_inventing_origin(self):
        referer = self.origin + '/admin?panel=posters'
        status, forwarded = self.post({'Referer': referer, 'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(status, 200)
        self.assertEqual(forwarded.get('Referer'), referer)
        self.assertEqual(forwarded.get('Sec-Fetch-Site'), 'same-origin')
        self.assertIsNone(forwarded.get('Origin'))

    def test_preserves_explicit_foreign_or_null_origin_for_rejection(self):
        for origin in ('https://foreign.test', 'null'):
            with self.subTest(origin=origin):
                status, forwarded = self.post({'Origin': origin, 'Referer': self.origin + '/admin',
                                               'Sec-Fetch-Site': 'same-origin'})
                self.assertEqual(status, 403)
                self.assertEqual(forwarded.get('Origin'), origin)

    def test_preserves_cross_site_metadata_for_rejection(self):
        status, forwarded = self.post({'Referer': self.origin + '/admin', 'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)
        self.assertEqual(forwarded.get('Sec-Fetch-Site'), 'cross-site')


if __name__ == '__main__':
    unittest.main()
