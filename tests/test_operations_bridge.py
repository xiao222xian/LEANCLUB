"""Service boundary tests on disposable databases; no network payment or SMS."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from operations_bridge import OperationsBridge, canonical
from store import Problem
from payment_fake import FakePaymentAdapter
from payment_lifecycle import PaymentWorker
from test_campaign_events import event_body, registration_body


class OperationsBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.secret = 'a' * 64
        self.store = CampaignStore(Path(self.tmp.name) / 'bridge.sqlite3', clock=lambda: self.now)
        self.bridge = OperationsBridge(self.store, 'http://127.0.0.1:4180', self.secret, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, action='stats', params=None, **changes):
        raw = json.dumps({'action': action, 'params': params or {}}, ensure_ascii=False).encode()
        timestamp = str(changes.get('timestamp', self.now))
        nonce = changes.get('nonce', uuid.uuid4().hex)
        tenant, actor = changes.get('tenant', '1'), changes.get('actor', '123')
        signature = hmac.new(self.secret.encode(), canonical(raw, timestamp, nonce, tenant, actor), hashlib.sha256).hexdigest()
        headers = Message()
        for name, value in zip(('Timestamp', 'Nonce', 'Tenant', 'Actor', 'Signature'), (timestamp, nonce, tenant, actor, signature)):
            headers['X-LC-' + name] = value
        return headers, raw

    def execute(self, action='stats', params=None, **changes):
        return self.bridge.execute(*self.request(action, params, **changes), '127.0.0.1')

    def assert_problem(self, status, callback, *args):
        with self.assertRaises(Problem) as caught:
            callback(*args)
        self.assertEqual(caught.exception.status, status)

    def test_disabled_and_missing_service_identity_fail_closed(self):
        disabled = OperationsBridge(self.store, 'http://127.0.0.1:4180')
        self.assert_problem(503, disabled.execute, *self.request(), '127.0.0.1')
        self.assert_problem(403, self.bridge.execute, Message(), b'{}', '127.0.0.1')

    def test_signature_binds_body_actor_tenant_and_loopback_peer(self):
        headers, raw = self.request()
        self.assert_problem(403, self.bridge.execute, headers, raw + b' ', '127.0.0.1')
        headers.replace_header('X-LC-Actor', '124')
        self.assert_problem(403, self.bridge.execute, headers, raw, '127.0.0.1')
        self.assert_problem(403, self.bridge.execute, *self.request(tenant='2'), '127.0.0.1')
        self.assert_problem(403, self.bridge.execute, *self.request(), '203.0.113.10')

    def test_duplicate_headers_and_stale_or_future_signatures_are_rejected(self):
        headers, raw = self.request()
        headers['X-LC-Actor'] = '123'
        self.assert_problem(403, self.bridge.execute, headers, raw, '127.0.0.1')
        for delta in (-61, 61):
            self.assert_problem(403, self.bridge.execute, *self.request(timestamp=self.now + delta), '127.0.0.1')

    def test_replay_rejected_after_adapter_restart(self):
        headers, raw = self.request()
        self.bridge.execute(headers, raw, '127.0.0.1')
        restarted = OperationsBridge(self.store, 'http://127.0.0.1:4180', self.secret, clock=lambda: self.now)
        self.assert_problem(409, restarted.execute, headers, raw, '127.0.0.1')

    def test_parallel_replay_has_exactly_one_success(self):
        headers, raw = self.request()
        def run(_):
            try:
                self.bridge.execute(headers, raw, '127.0.0.1')
                return 200
            except Problem as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(4)))
        self.assertEqual(sorted(results), [200, 409, 409, 409])

    def test_arbitrary_actions_and_extra_fields_cannot_reach_internal_services(self):
        self.assert_problem(400, self.execute, '../../admin/feishu/test')
        self.assert_problem(400, self.execute, 'stats', {'url': 'http://example.com'})
        self.assert_problem(400, self.execute, 'feedback-list', {'page': True})
        self.assert_problem(400, self.execute, 'checkin', {'code': 'synthetic', 'requestId': str(uuid.uuid4()), 'paid': True})

    def test_comment_moderation_uses_separate_public_store_and_audits_no_content(self):
        visitor = self.store.visitor(None)[0]
        comment = self.store.comments.submit(visitor, '192.0.2.1', {
            'requestId': str(uuid.uuid4()), 'content': '合成公开评论', 'publishConsent': True})['item']
        self.store.feedback.submit(visitor, '192.0.2.1', {
            'requestId': str(uuid.uuid4()), 'content': '合成私密意见', 'category': 'other', 'city': None})
        listing = self.execute('comments-list', {'status': 'all', 'page': 1})
        self.assertEqual((listing['total'], listing['page'], listing['pageSize'], listing['totalPages']), (1, 1, 20, 1))
        self.assertEqual(listing['items'], [comment | {'replyRevision': 0, 'parentContent': None}])
        self.assertNotIn('unread', listing)
        hidden = self.execute('comments-status', {'id': comment['id'], 'status': 'hidden'})
        self.assertEqual(hidden['item']['status'], 'hidden')
        self.assertEqual(self.store.comments.public_list()['items'], [])
        self.assertEqual(self.execute('feedback-list')['items'][0]['content'], '合成私密意见')
        self.assertEqual(self.execute('comments-list', {'status': 'hidden'})['total'], 1)
        self.assertEqual(self.execute('comments-status', {'id': comment['id'], 'status': 'visible'})['item']['status'], 'visible')
        with self.store.connect() as db:
            audit = dict(db.execute('SELECT * FROM leanclub_operations_audit ORDER BY id DESC LIMIT 1').fetchone())
        self.assertEqual(audit['action'], 'comments-status')
        self.assertEqual(set(audit), {'id', 'created', 'actor', 'tenant', 'action', 'status'})

    def test_comment_actions_reject_extra_fields_and_unsigned_writes(self):
        for params in ({'page': True}, {'status': 'new'}, {'page': 1000001}, {'cursor': 'bad'}):
            self.assert_problem(400, self.execute, 'comments-list', params)
        for params in ({'id': 'bad', 'status': 'hidden'}, {'id': '0' * 32, 'status': 'new'},
                       {'id': '0' * 32, 'status': 'hidden', 'content': 'cannot-edit'}):
            self.assert_problem(400, self.execute, 'comments-status', params)
        self.assert_problem(404, self.execute, 'comments-status', {'id': '0' * 32, 'status': 'hidden'})
        headers, raw = self.request('comments-status', {'id': '0' * 32, 'status': 'hidden'})
        self.assert_problem(403, self.bridge.execute, headers, raw, '203.0.113.1')
        self.assert_problem(403, self.bridge.execute, Message(), raw, '127.0.0.1')

    def test_event_update_is_versioned_with_no_lost_edit_in_same_second(self):
        event = self.execute('event-save', event_body(self.now, status='draft'))['event']
        self.assertEqual(event['version'], 1)
        changed = self.execute('event-save', {'id': event['id'], 'expectedVersion': 1, 'title': '合成编辑一'})['event']
        self.assertEqual(changed['version'], 2)
        self.assert_problem(409, self.execute, 'event-save', {'id': event['id'], 'expectedVersion': 1, 'title': '过期修改'})
        self.assert_problem(400, self.execute, 'event-save', {'id': event['id'], 'title': '缺少版本'})
        self.assertEqual(self.execute('events-list')['events'][0]['title'], '合成编辑一')
        self.assertEqual(self.store.events.list()['events'], [])

    def test_legacy_edits_advance_version_and_invalidate_stale_admin_draft(self):
        event = self.execute('event-save', event_body(self.now, status='draft'))['event']
        self.store.events.save({'title': '旧维护入口修改'}, event['id'])
        self.assert_problem(409, self.execute, 'event-save', {'id': event['id'], 'expectedVersion': 1, 'title': '旧草稿'})

    def test_operations_cannot_confirm_payment_and_keep_audit_metadata_only(self):
        self.assert_problem(400, self.execute, 'mark-paid', {'phone': 'synthetic-private'})
        self.execute('events-list')
        with self.store.connect() as db:
            row = dict(db.execute('SELECT * FROM leanclub_operations_audit ORDER BY id DESC LIMIT 1').fetchone())
        self.assertEqual(row['actor'], '123')
        self.assertEqual(row['action'], 'events-list')
        self.assertEqual(set(row), {'id', 'created', 'actor', 'tenant', 'action', 'status'})

    def test_order_and_checkin_views_do_not_return_raw_phone(self):
        self.store.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        event = self.execute('event-save', event_body(self.now))['event']
        visitor = self.store.visitor(None)[0]
        receipt = self.store.events.register(visitor, event['id'], registration_body(), self.bridge.origin)
        result = self.execute('registrations', {'eventId': event['id']})
        self.assertNotIn('phone', result['items'][0])
        self.assertIn('****', result['items'][0]['phoneMasked'])
        check = self.execute('checkin-preview', {'code': receipt['registration']['ticketNumber']})
        self.assertNotIn('phone', check['registration'])
        self.assertFalse(check['eligible'])


if __name__ == '__main__':
    unittest.main()
