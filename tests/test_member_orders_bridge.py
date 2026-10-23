"""Purpose-bound member bridge tests; temporary DBs and loopback HTTP only."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from member_orders_bridge import MemberOrdersBridge, canonical, PATH, MAX_BODY
from operations_bridge import canonical as employee_canonical
from store import Problem
from payment_fake import FakePaymentAdapter
from payment_lifecycle import PaymentWorker
from test_campaign_events import event_body, registration_body
import test_campaign as http_support


class MemberBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.secret = 'a' * 64
        self.store = CampaignStore(Path(self.tmp.name) / 'member-bridge.sqlite3', clock=lambda: self.now)
        self.bridge = MemberOrdersBridge(self.store, 'http://127.0.0.1:4180', self.secret, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, action='member-orders', params=None, **changes):
        raw = changes.get('raw', json.dumps({'action': action, 'params': params or {}}, ensure_ascii=False, separators=(',', ':')).encode())
        timestamp = str(changes.get('timestamp', self.now))
        nonce = changes.get('nonce', uuid.uuid4().hex)
        tenant, actor = changes.get('tenant', '1'), changes.get('actor', '101')
        signature = hmac.new(changes.get('secret', self.secret).encode(), canonical(raw, timestamp, nonce, tenant, actor), hashlib.sha256).hexdigest()
        headers = Message()
        for name, value in zip(('Timestamp', 'Nonce', 'Tenant', 'Actor-Type', 'Actor', 'Signature'),
                               (timestamp, nonce, tenant, changes.get('actor_type', 'MEMBER'), actor, signature)):
            headers['X-LC-' + name] = value
        return headers, raw

    def execute(self, action='member-orders', params=None, **changes):
        return self.bridge.execute(*self.request(action, params, **changes), '127.0.0.1')

    def assert_problem(self, status, callback, *args):
        with self.assertRaises(Problem) as caught:
            callback(*args)
        self.assertEqual(caught.exception.status, status)

    def test_fixed_cross_language_ascii_and_unicode_signatures(self):
        key = ('0123456789abcdef' * 4).encode()
        values = ('1792728000', '0123456789abcdef0123456789abcdef', '1', '123')
        for raw, expected in (
            (b'{"action":"member-order","params":{"id":"abcdef0123456789abcdef0123456789"}}',
             '5a9bc4c129f162b5b29eaae772aca809548fc592660780a6acc338dfdc7ff305'),
            ('{"action":"member-register","params":{"name":"测试会员"}}'.encode('utf-8'),
             '468ff72131a5f9a3284eef138df0fe736e8541523296fdeb2b35e7bea9fe88a6')):
            signed = canonical(raw, *values)
            self.assertFalse(signed.endswith(b'\n'))
            self.assertIn(b'\nMEMBER\n123\n', signed)
            self.assertEqual(hmac.new(key, signed, hashlib.sha256).hexdigest(), expected)

    def test_disabled_missing_identity_and_non_loopback_fail_closed(self):
        disabled = MemberOrdersBridge(self.store, self.bridge.origin)
        self.assert_problem(503, disabled.execute, *self.request(), '127.0.0.1')
        self.assert_problem(403, self.bridge.execute, Message(), b'{}', '127.0.0.1')
        self.assert_problem(403, self.bridge.execute, *self.request(), '203.0.113.2')

    def test_actor_type_purpose_key_actor_tenant_and_body_are_bound(self):
        for changes in ({'actor_type': 'ADMIN'}, {'actor_type': 'member'}, {'secret': 'b' * 64},
                        {'tenant': '2'}, {'actor': '0'}, {'actor': str(2**63)}):
            self.assert_problem(403, self.bridge.execute, *self.request(**changes), '127.0.0.1')
        headers, raw = self.request()
        self.assert_problem(403, self.bridge.execute, headers, raw + b' ', '127.0.0.1')
        headers.replace_header('X-LC-Actor', '102')
        self.assert_problem(403, self.bridge.execute, headers, raw, '127.0.0.1')
        headers, raw = self.request()
        signature = hmac.new(self.secret.encode(), employee_canonical(raw, headers['X-LC-Timestamp'],
            headers['X-LC-Nonce'], '1', '101'), hashlib.sha256).hexdigest()
        headers.replace_header('X-LC-Signature', signature)
        self.assert_problem(403, self.bridge.execute, headers, raw, '127.0.0.1')

    def test_duplicate_headers_and_time_window_are_rejected(self):
        headers, raw = self.request()
        headers['X-LC-Actor-Type'] = 'MEMBER'
        self.assert_problem(403, self.bridge.execute, headers, raw, '127.0.0.1')
        for delta in (-61, 61):
            self.assert_problem(403, self.bridge.execute, *self.request(timestamp=self.now + delta), '127.0.0.1')

    def test_nonce_is_durable_and_parallel_replay_has_one_success(self):
        headers, raw = self.request()
        def call(_):
            try:
                self.bridge.execute(headers, raw, '127.0.0.1')
                return 200
            except Problem as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sorted(pool.map(call, range(4))), [200, 409, 409, 409])
        restarted = MemberOrdersBridge(self.store, self.bridge.origin, self.secret, clock=lambda: self.now)
        self.assert_problem(409, restarted.execute, headers, raw, '127.0.0.1')

    def test_fixed_actions_extra_identity_fields_and_non_integer_pages_are_rejected(self):
        self.assert_problem(400, self.execute, 'mark-paid', {'id': 'a' * 32})
        for params in ({'memberId': 101}, {'tenantId': 1}, {'visitor': 'chosen'}, {'url': 'http://example.test'},
                       {'pageNo': True}, {'pageSize': False}, {'pageSize': 21}, {'status': False}):
            self.assert_problem(400, self.execute, 'member-orders', params)
        self.assert_problem(400, self.execute, 'member-cancel', {'id': 'a' * 32, 'requestId': str(uuid.uuid4()), 'paid': True})

    def test_duplicate_json_keys_trailing_values_non_objects_and_nonfinite_are_rejected(self):
        for raw in (b'{"action":"member-orders","action":"member-orders","params":{}}',
                    b'{"action":"member-orders","params":{"pageNo":1,"pageNo":2}}',
                    b'{"action":"member-orders","params":{"pageNo":NaN}}',
                    b'{"action":"member-orders","params":{}} {}', b'[]'):
            self.assert_problem(400, self.bridge.execute, *self.request(raw=raw), '127.0.0.1')

    def test_request_and_response_caps_are_enforced(self):
        self.assert_problem(413, self.bridge.execute, *self.request(raw=b'x' * (MAX_BODY + 1)), '127.0.0.1')
        with patch('member_orders_bridge.MAX_RESPONSE', 5):
            self.assert_problem(502, self.execute)

    def test_payment_off_still_refuses_signed_registration_and_does_not_log_body(self):
        event = self.store.events.save(event_body(self.now))['event']
        self.assert_problem(409, self.execute, 'member-register', {'eventId': event['id'], **registration_body(name='合成隐私标记')})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_registrations').fetchone()[0], 0)
            row = dict(db.execute('SELECT * FROM leanclub_member_operations_audit').fetchone())
        self.assertEqual(set(row), {'id', 'created', 'member_id', 'tenant', 'action', 'status'})
        self.assertNotIn('合成隐私标记', json.dumps(row))
        self.assertNotIn('13900000001', json.dumps(row))

    def test_write_rate_limit_is_per_authenticated_member_and_forged_calls_do_not_count(self):
        data = {'id': 'a' * 32, 'requestId': str(uuid.uuid4())}
        for _ in range(5):
            self.assert_problem(403, self.bridge.execute, *self.request('member-cancel', data, secret='b' * 64), '127.0.0.1')
        for _ in range(30):
            self.assert_problem(404, self.execute, 'member-cancel', data)
        self.assert_problem(429, self.execute, 'member-cancel', data)
        self.assert_problem(404, self.bridge.execute, *self.request('member-cancel', data, actor='102'), '127.0.0.1')
        self.now += 61
        self.assert_problem(404, self.execute, 'member-cancel', data)

    def test_uncertain_response_retries_with_new_nonce_same_business_key_recover_one_order(self):
        self.store.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        event = self.store.events.save(event_body(self.now))['event']
        params = {'eventId': event['id'], **registration_body()}
        with patch('member_orders_bridge.MAX_RESPONSE', 5):
            self.assert_problem(502, self.execute, 'member-register', params)
        receipt = self.execute('member-register', params)
        self.assertTrue(receipt['duplicate'])
        self.assertEqual(self.execute()['total'], 1)
        self.assert_problem(404, self.bridge.execute, *self.request('member-order', {'id': receipt['order']['id']}, actor='102'), '127.0.0.1')


class MemberBridgeHTTPTests(unittest.TestCase):
    setUpClass = classmethod(http_support.CampaignHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(http_support.CampaignHTTPTests.tearDownClass.__func__)
    request = http_support.CampaignHTTPTests.request

    def setUp(self):
        http_support.CampaignHTTPTests.setUp(self)
        self.bridge = MemberOrdersBridge(self.store, self.origin, 'c' * 64)
        self.member_patch = patch.object(self.server_module, 'MEMBER_ORDERS', self.bridge)
        self.member_patch.start()

    def tearDown(self):
        self.member_patch.stop()
        http_support.CampaignHTTPTests.tearDown(self)

    def signed(self, raw):
        timestamp = str(int(self.store.clock()))
        nonce = uuid.uuid4().hex
        signature = hmac.new(('c' * 64).encode(), canonical(raw, timestamp, nonce, '1', '101'), hashlib.sha256).hexdigest()
        return {'X-LC-Timestamp': timestamp, 'X-LC-Nonce': nonce, 'X-LC-Tenant': '1',
                'X-LC-Actor-Type': 'MEMBER', 'X-LC-Actor': '101', 'X-LC-Signature': signature}

    def test_real_private_http_route_requires_signature_and_never_uses_cookies(self):
        self.assertEqual(self.request('POST', PATH, {})[0], 403)
        raw = b'{"action":"member-orders","params":{}}'
        status, result, headers = self.request('POST', PATH, raw=raw, headers=self.signed(raw))
        self.assertEqual(status, 200)
        self.assertEqual(result['list'], [])
        self.assertTrue(any(key.lower() == 'cache-control' and value == 'no-store' for key, value in headers))
        self.assertEqual(self.request('GET', PATH)[0], 404)
        self.assertEqual(self.request('POST', PATH + '?unexpected=1', raw=raw, headers=self.signed(raw))[0], 404)

    def test_http_body_limit_and_content_type(self):
        self.assertEqual(self.request('POST', PATH, raw=b'x' * (MAX_BODY + 1))[0], 413)
        self.assertEqual(self.request('POST', PATH, raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 415)


if __name__ == '__main__':
    unittest.main()
