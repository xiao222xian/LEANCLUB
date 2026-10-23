"""Dual-origin HTTP isolation using only loopback servers and temporary databases."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
import hmac
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign_http import CampaignAPI
from campaign_hosts import authority, compatibility_apis, select_api
from operations_bridge import OperationsBridge, canonical, PATH as OPERATIONS_PATH
from member_orders_bridge import MemberOrdersBridge, canonical as member_canonical, PATH as MEMBER_PATH
from payment_fake import FakePaymentAdapter
from store import Problem
from test_campaign_events import event_body, registration_body, synthetic_paid
import test_campaign as http_support

PRIMARY = 'https://www.leanschool.net'
LEGACY = 'https://boji.tensorbot.xyz'


class HostConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.api = CampaignAPI(None, PRIMARY)

    def test_config_requires_distinct_exact_https_dns_origins(self):
        self.assertEqual(compatibility_apis(self.api, ''), ())
        apis = compatibility_apis(self.api, LEGACY)
        self.assertEqual(len(apis), 1)
        self.assertEqual(apis[0].origin, LEGACY)
        self.assertEqual(apis[0].allowed_origins, {('https', 'boji.tensorbot.xyz', 443)})
        self.assertIs(apis[0].events.api, apis[0])
        for value in ('http://boji.tensorbot.xyz', LEGACY + '/', LEGACY + '?a=1', LEGACY + '#x',
                      LEGACY + ':444', PRIMARY, PRIMARY + ':443', LEGACY + ',' + LEGACY,
                      'https://*.tensorbot.xyz', 'https://127.0.0.1', 'https://localhost',
                      'https://user@boji.tensorbot.xyz', 'https://boji.tensorbot.xyz.',
                      'https://boji_tensorbot.xyz', 'https://薄肌.example', LEGACY + ',', ','):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compatibility_apis(self.api, value)
        with self.assertRaises(ValueError):
            compatibility_apis(CampaignAPI(None, 'http://localhost:4180'), LEGACY)

    def test_host_authority_rejects_url_lists_credentials_controls_and_invalid_ports(self):
        for value in ('', 'www.leanschool.net:0', 'www.leanschool.net:65536', 'www.leanschool.net:',
                      'https://www.leanschool.net', 'www.leanschool.net,boji.tensorbot.xyz',
                      'user@www.leanschool.net', 'www.leanschool.net/', 'www.leanschool.net?x=y',
                      'www.leanschool.net#x', 'www.leanschool.net\t', 'www.leanschool.net.',
                      'www.leanschool.net\\evil', 'www.leanschool.net%00', '薄肌.example',
                      '[::1', '::1', 'www.leanschool.net:abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                authority(value, 443)
        self.assertEqual(authority('WWW.LEANSCHOOL.NET:443', 443), ('www.leanschool.net', 443))
        self.assertEqual(authority('[::1]:5381', 80), ('::1', 5381))

    def test_local_preview_keeps_loopback_host_ports_without_production_aliases(self):
        local = CampaignAPI(None, 'http://127.0.0.1:4180', allow_local_setup=True)
        for host in ('127.0.0.1:5381', 'localhost:4180', '[::1]:5381'):
            headers = Message()
            headers['Host'] = host
            self.assertIs(select_api(local, (), headers, '127.0.0.1'), local)
            with self.assertRaises(Problem):
                select_api(self.api, compatibility_apis(self.api, LEGACY), headers, '127.0.0.1')
        headers = Message()
        headers['Host'] = '127.0.0.1:5381'
        with self.assertRaises(Problem):
            select_api(local, (), headers, '192.0.2.1')


class CampaignHostHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        http_support.CampaignHTTPTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        http_support.CampaignHTTPTests.tearDownClass.__func__(cls)

    def setUp(self):
        http_support.CampaignHTTPTests.setUp(self)
        self.patch.stop()
        self.api = CampaignAPI(self.store, PRIMARY)
        self.patch = patch.object(self.server_module, 'CAMPAIGN', self.api)
        self.patch.start()
        self.compatible = compatibility_apis(self.api, LEGACY)
        self.compat_patch = patch.object(self.server_module, 'COMPAT_CAMPAIGNS', self.compatible)
        self.compat_patch.start()
        self.proxy_patch = patch.object(self.server_module, 'TRUSTED_PROXIES', {'127.0.0.1'})
        self.proxy_patch.start()

    def tearDown(self):
        self.proxy_patch.stop()
        self.compat_patch.stop()
        http_support.CampaignHTTPTests.tearDown(self)

    def request(self, method, path, origin=PRIMARY, data=None, cookies=None, headers=(), host=None, raw=None):
        outgoing = [('Host', urlsplit(origin).netloc if host is None else host)]
        if cookies:
            outgoing.append(('Cookie', '; '.join('%s=%s' % item for item in cookies.items())))
        body = b'' if raw is None else raw
        if method == 'POST':
            outgoing.extend([('Origin', origin), ('Content-Type', 'application/json')])
            body = json.dumps(data if data is not None else {}).encode() if raw is None else raw
            outgoing.append(('Content-Length', str(len(body))))
        # A header value of None removes a default; duplicates remain duplicates.
        for key, value in headers:
            if value is None:
                outgoing = [(name, prior) for name, prior in outgoing if name.lower() != key.lower()]
            else:
                outgoing.append((key, value))
        connection = HTTPConnection('127.0.0.1', self.port, timeout=8)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in outgoing:
                connection.putheader(key, value)
            connection.endheaders(body)
            response = connection.getresponse()
            payload = json.loads(response.read())
            returned = response.getheaders()
            if cookies is not None:
                for key, value in returned:
                    if key.lower() == 'set-cookie':
                        parsed = SimpleCookie(value)
                        cookies.update((name, item.value) for name, item in parsed.items())
            return response.status, payload, returned
        finally:
            connection.close()

    def test_host_and_origin_matrix_including_missing_fetch_metadata(self):
        for host_origin in (PRIMARY, LEGACY):
            for source in (PRIMARY, LEGACY, 'https://foreign.test', 'null', 'http://' + urlsplit(host_origin).netloc):
                with self.subTest(host=host_origin, source=source):
                    status, _, _ = self.request('POST', '/api/campaign/unknown', origin=source,
                                                host=urlsplit(host_origin).netloc)
                    self.assertEqual(status, 404 if source == host_origin else 403)
            self.assertEqual(self.request('POST', '/api/campaign/unknown', origin=host_origin,
                headers=[('Sec-Fetch-Site', 'cross-site')])[0], 403)
            for referer in (host_origin + '/city-vote', (LEGACY if host_origin == PRIMARY else PRIMARY) + '/city-vote'):
                self.assertEqual(self.request('POST', '/api/campaign/unknown', origin=host_origin,
                    headers=[('Origin', None), ('Referer', referer)])[0], 404 if referer.startswith(host_origin + '/') else 403)
            self.assertEqual(self.request('POST', '/api/campaign/unknown', origin=host_origin,
                headers=[('Origin', None)])[0], 403)
            self.assertEqual(self.request('POST', '/api/campaign/unknown', origin=host_origin,
                headers=[('Origin', host_origin)])[0], 403)

    def test_unknown_duplicate_missing_and_forwarded_hosts_do_not_issue_cookies(self):
        for host in ('foreign.test', 'www.leanschool.net:444', '127.0.0.1:5381'):
            status, _, headers = self.request('GET', '/api/campaign', host=host,
                headers=[('X-Forwarded-Host', 'www.leanschool.net'), ('X-Forwarded-Proto', 'https')])
            self.assertEqual(status, 421)
            self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        for changes in ([('Host', 'www.leanschool.net')], [('Host', None)]):
            self.assertEqual(self.request('GET', '/api/campaign', headers=changes)[0], 400)
        self.assertEqual(self.request('GET', '/api/campaign', host='www.leanschool.net,boji.tensorbot.xyz')[0], 400)
        self.assertEqual(self.request('GET', 'https://www.leanschool.net/api/campaign')[0], 400)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_visitors').fetchone()[0], 0)

    def test_legacy_cookie_keeps_change_right_new_cookie_same_ip_cannot_claim_it(self):
        old, new = {}, {}
        status, _, headers = self.request('POST', '/api/campaign/vote', origin=LEGACY,
                                          data={'city': 'shenzhen'}, cookies=old)
        self.assertEqual(status, 200)
        cookie = next(value for key, value in headers if key.lower() == 'set-cookie')
        self.assertIn('Secure', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertNotIn('Domain=', cookie)
        status, state, _ = self.request('GET', '/api/campaign', cookies=new)
        self.assertEqual(status, 200)
        self.assertEqual(state['vote'], {'city': 'shenzhen'})
        self.assertFalse(state['canChange'])
        self.assertNotEqual(old['lean_visitor'], new['lean_visitor'])
        self.assertEqual(self.request('POST', '/api/campaign/vote', data={'city': 'zhuhai'}, cookies=new)[0], 409)
        self.assertEqual(self.request('POST', '/api/campaign/vote', origin=LEGACY, data={'city': 'zhuhai'}, cookies=old)[0], 200)
        self.assertTrue(self.request('GET', '/api/campaign', origin=LEGACY, cookies=old)[1]['canChange'])
        self.assertEqual(self.request('GET', '/api/campaign', cookies=new)[1]['vote'], {'city': 'zhuhai'})
        self.assertEqual(self.store.stats()['totalVotes'], 1)

    def test_parallel_domains_cannot_mutate_global_origin_or_duplicate_same_ip_vote(self):
        def attempt(index):
            origin = PRIMARY if index % 2 else LEGACY
            return self.request('POST', '/api/campaign/vote', origin=origin, data={'city': 'shenzhen'}, cookies={})[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(list(pool.map(attempt, range(12))), [200] * 12)
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        self.assertEqual(self.api.origin, PRIMARY)
        self.assertEqual(self.compatible[0].origin, LEGACY)
        self.assertIs(self.api.store, self.compatible[0].store)

    def test_existing_order_urls_follow_selected_api_and_full_qr_stays_origin_bound(self):
        self.store.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        for origin in (PRIMARY, LEGACY):
            cookies = {}
            self.request('GET', '/api/campaign', origin=origin, cookies=cookies)
            visitor = self.store.visitor(cookies['lean_visitor'])[0]
            event = self.store.events.save(event_body(self.store.clock()))['event']
            order = self.store.events.register(visitor, event['id'], registration_body(), origin)
            synthetic_paid(self.store, order['order']['id'])
            status, result, _ = self.request('POST', '/api/campaign/events/ticket', origin=origin,
                data={'orderToken': order['orderToken']}, cookies=cookies)
            self.assertEqual(status, 200)
            self.assertTrue(result['ticketUrl'].startswith(origin + '/ticket#'))
            self.assertTrue(result['checkInUrl'].startswith(origin + '/check-in#'))
            mine = self.request('GET', '/api/campaign/events/mine', origin=origin, cookies=cookies)[1]
            self.assertTrue(mine['tickets'][0]['checkInUrl'].startswith(origin + '/check-in#'))
            other = LEGACY if origin == PRIMARY else PRIMARY
            self.assertEqual(self.request('POST', '/api/campaign/events/ticket', origin=other,
                data={'ticketToken': result['checkInUrl']}, cookies=cookies)[0], 404)

    def test_admin_still_requires_login_csrf_and_its_own_host_origin(self):
        self.store.create_admin('synthetic-admin', 'Synthetic-only-password-611')
        for origin in (PRIMARY, LEGACY):
            cookies = {}
            self.assertEqual(self.request('GET', '/api/admin/stats', origin=origin)[0], 401)
            status, login, _ = self.request('POST', '/api/admin/login', origin=origin, cookies=cookies,
                data={'username': 'synthetic-admin', 'password': 'Synthetic-only-password-611'})
            self.assertEqual(status, 200)
            endpoint = '/api/admin/feedback/' + '0' * 32 + '/status'
            self.assertEqual(self.request('POST', endpoint, origin=origin, cookies=cookies, data={'status': 'read'})[0], 403)
            csrf = [('X-CSRF-Token', login['csrfToken'])]
            self.assertEqual(self.request('POST', endpoint, origin=origin, cookies=cookies, data={'status': 'read'}, headers=csrf)[0], 404)
            other = LEGACY if origin == PRIMARY else PRIMARY
            self.assertEqual(self.request('POST', endpoint, origin=other, host=urlsplit(origin).netloc,
                cookies=cookies, data={'status': 'read'}, headers=csrf)[0], 403)

    def test_internal_routes_keep_loopback_signed_boundary_without_public_host(self):
        secret = 'a' * 64
        operations = OperationsBridge(self.store, PRIMARY, secret)
        member = MemberOrdersBridge(self.store, PRIMARY, secret)
        for endpoint, action, signer, bridge, name in (
            (OPERATIONS_PATH, 'comments-list', canonical, operations, 'OPERATIONS'),
            (MEMBER_PATH, 'member-orders', member_canonical, member, 'MEMBER_ORDERS')):
            raw = json.dumps({'action': action, 'params': {}}).encode()
            stamp, nonce = str(int(self.store.clock())), uuid.uuid4().hex
            signature = hmac.new(secret.encode(), signer(raw, stamp, nonce, '1', '101'), hashlib.sha256).hexdigest()
            headers = [('X-LC-Timestamp', stamp), ('X-LC-Nonce', nonce), ('X-LC-Tenant', '1'),
                       ('X-LC-Actor', '101'), ('X-LC-Signature', signature)]
            if endpoint == MEMBER_PATH:
                headers.append(('X-LC-Actor-Type', 'MEMBER'))
            with patch.object(self.server_module, name, bridge):
                self.assertEqual(self.request('POST', endpoint, host='127.0.0.1:5381', raw=raw)[0], 403)
                self.assertEqual(self.request('POST', endpoint, host='127.0.0.1:5381', raw=raw, headers=headers)[0], 200)
                self.assertEqual(self.request('POST', endpoint, host='127.0.0.1:5381', raw=raw, headers=headers)[0], 409)


if __name__ == '__main__':
    unittest.main()
