"""Feedback persistence, privacy, quotas and mocked notification acceptance."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from campaign_feishu import DeliveryFailure, build_payload
from store import Problem, Store
import test_campaign as http_support

# Synthetic credentials are confined to temporary databases; transport is mocked.
URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-feedback-test-0001'
SECRET = 'synthetic-feedback-signing-key'


def data(**changes):
    return {'requestId': str(uuid.uuid4()), 'content': '希望组织新手友好的线下力量训练。',
            'category': 'activity', 'city': 'zhuhai'} | changes


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.path = Path(self.tmp.name) / 'feedback.sqlite3'
        self.store = CampaignStore(self.path, clock=lambda: self.now)
        self.visitor = self.store.visitor(None)[0]

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, body=None, visitor=None, ip='192.0.2.1'):
        return self.store.feedback.submit(visitor or self.visitor, ip, data() if body is None else body)

    def events(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM campaign_feishu_outbox ORDER BY rowid')]

    def enable(self):
        self.store.notifications.save({'enabled': True, 'webhook': URL, 'signingSecret': SECRET})

    def test_same_request_is_idempotent_and_changed_content_conflicts(self):
        self.enable()
        body = data()
        first, second = self.submit(body), self.submit(body)
        self.assertFalse(first['duplicate'])
        self.assertEqual(second, first | {'duplicate': True})
        for field, value in [('content', '希望有更多初学者入门教学。'), ('category', 'training'), ('city', None)]:
            with self.subTest(field=field), self.assertRaises(Problem) as error:
                self.submit(body | {field: value})
            self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.store.feedback.list()['total'], 1)
        self.assertEqual(len(self.events()), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_rate_events WHERE kind='feedback-visitor'").fetchone()[0], 1)

    def test_cross_visitor_reused_uuid_does_not_read_other_id_or_content(self):
        body = data()
        first = self.submit(body)
        other = self.store.visitor(None)[0]
        second = self.submit(body | {'content': '我想了解活动有哪些不同类型。'}, visitor=other)
        self.assertFalse(second['duplicate'])
        self.assertNotEqual(second['id'], first['id'])
        self.assertNotIn('content', second)
        self.assertEqual(self.submit(body | {'content': '我想了解活动有哪些不同类型。'}, visitor=other)['id'], second['id'])

    def test_parallel_retries_insert_one_feedback_one_outbox_and_one_quota(self):
        self.enable()
        body = data()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.submit(body), range(10)))
        self.assertEqual(len({result['id'] for result in results}), 1)
        self.assertEqual(sum(not result['duplicate'] for result in results), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.store.feedback.list()['total'], 1)

    def test_content_type_length_control_category_city_and_extra_fields_validation(self):
        invalid = [{'content': ''}, {'content': '     \n  '}, {'content': '四个汉字'}, {'content': '好    '},
                   {'content': '好' * 1001}, {'content': None}, {'content': ['内容内容内容']},
                   {'content': '合法文字\x00'}, {'content': '\t合法文字内容'}, {'content': '合法文字\x7f'},
                   {'content': '合法文字\u202e'}, {'content': '合法文字\u200b'}, {'content': '合法文字\ud800'},
                   {'content': '\u200d' * 10}, {'category': 'invalid'}, {'category': []},
                   {'city': 'beijing'}, {'city': True}, {'requestId': 'bad'}, {'requestId': None},
                   {'email': 'synthetic@example.test'}, {'phone': 'synthetic'}, {'visitor': 'forged'}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(Problem):
                self.submit(data(**changes))
        self.assertEqual(self.store.feedback.list()['total'], 0)
        self.assertEqual(self.events(), [])

    def test_normal_newlines_emoji_and_max_length_are_preserved(self):
        text = ' 希望举办亲子活动 👨‍👩‍👧‍👦\r\n也想学习力量训练 🏋️‍♀️。 '
        receipt = self.submit(data(content=text, city=None, category='training'))
        item = self.store.feedback.list()['items'][0]
        self.assertEqual(item['content'], text.strip().replace('\r\n', '\n'))
        self.assertEqual(item['id'], receipt['id'])
        self.assertIsNone(item['city'])
        self.submit(data(content='好' * 1000))

    def test_visitor_burst_limit_duplicate_retry_and_day_limit(self):
        first = data()
        self.submit(first)
        for _ in range(2):
            self.submit()
        with self.assertRaises(Problem) as error:
            self.submit()
        self.assertEqual(error.exception.status, 429)
        self.assertTrue(self.submit(first)['duplicate'])
        for _ in range(7):
            self.now += 601
            self.submit()
        self.now += 601
        with self.assertRaises(Problem) as error:
            self.submit()
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(self.store.feedback.list()['total'], 10)
        self.now += 86401
        self.assertFalse(self.submit()['duplicate'])

    def test_ip_burst_and_day_limits_survive_cookie_rotation(self):
        for _ in range(10):
            self.submit(visitor=self.store.visitor(None)[0])
        with self.assertRaises(Problem) as error:
            self.submit(visitor=self.store.visitor(None)[0])
        self.assertEqual(error.exception.status, 429)
        for _ in range(2):
            self.now += 601
            for _ in range(10):
                self.submit(visitor=self.store.visitor(None)[0])
        self.now += 601
        with self.assertRaises(Problem):
            self.submit(visitor=self.store.visitor(None)[0])
        self.assertEqual(self.store.feedback.list()['total'], 30)
        self.assertFalse(self.submit(visitor=self.store.visitor(None)[0], ip='192.0.2.2')['duplicate'])

    def test_concurrent_unique_submissions_cannot_exceed_quota(self):
        def attempt(_):
            try:
                self.submit()
                return 201
            except Problem as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(10)))
        self.assertEqual(results.count(201), 3)
        self.assertEqual(results.count(429), 7)
        self.assertEqual(self.store.feedback.list()['total'], 3)

    def test_pagination_filters_unread_idempotent_marking_and_private_fields(self):
        for index in range(25):
            self.now += 1
            self.submit(data(content='合成活动意见编号%d' % index), self.store.visitor(None)[0], '192.0.2.%s' % (index + 1))
        first, second = self.store.feedback.list(), self.store.feedback.list(page=2)
        self.assertEqual((first['total'], first['unread'], first['pageSize'], first['totalPages']), (25, 25, 20, 2))
        self.assertEqual(len(first['items']), 20)
        self.assertEqual(len(second['items']), 5)
        self.assertEqual(first['items'][0]['content'], '合成活动意见编号24')
        ids1, ids2 = ({item['id'] for item in page['items']} for page in (first, second))
        self.assertFalse(ids1 & ids2)
        self.assertEqual(set(first['items'][0]), {'id', 'content', 'category', 'city', 'status', 'createdAt'})
        selected = first['items'][0]['id']
        for _ in range(2):
            self.assertEqual(self.store.feedback.set_status(selected, 'read')['unread'], 24)
        self.assertEqual(self.store.feedback.list('read')['total'], 1)
        self.assertEqual(self.store.feedback.list('new')['total'], 24)
        self.assertEqual(self.store.feedback.set_status(selected, 'new')['unread'], 25)
        self.assertEqual(self.store.feedback.list(page=99)['items'], [])
        for status, page in [('bad', 1), ([], 1), ('all', 0), ('all', True), ('all', 1.5), ('all', 1000001)]:
            with self.assertRaises(Problem):
                self.store.feedback.list(status, page)
        for item_id, status in [('bad-id', 'new'), (selected, 'all'), (selected, None), ('0' * 32, 'read')]:
            with self.assertRaises(Problem):
                self.store.feedback.set_status(item_id, status)

    def test_disabled_notifications_keep_feedback_and_never_retroactively_queue(self):
        receipt = self.submit()
        self.assertEqual(self.events(), [])
        self.enable()
        sender = Mock()
        self.assertFalse(self.store.notifications.deliver_one(sender))
        sender.assert_not_called()
        self.assertEqual(self.store.feedback.list()['items'][0]['id'], receipt['id'])

    def test_feedback_and_outbox_rollback_together(self):
        self.enable()
        enqueue = self.store.notifications.enqueue
        def fail(*args, **kwargs):
            enqueue(*args, **kwargs)
            raise RuntimeError('synthetic failure after queue insertion')
        with patch.object(self.store.notifications, 'enqueue', side_effect=fail), self.assertRaises(RuntimeError):
            self.submit()
        self.assertEqual(self.store.feedback.list()['total'], 0)
        self.assertEqual(self.events(), [])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_rate_events WHERE kind LIKE 'feedback-%'").fetchone()[0], 0)

    def test_notification_plain_text_no_markup_or_identity_and_signing(self):
        self.enable()
        body = data(content='建议 @all <at id=all></at> **粗体** [链接](https://example.test) <script>alert(1)</script>')
        receipt = self.submit(body)
        event = self.events()[0]
        self.assertEqual(event['kind'], 'feedback')
        self.assertEqual(json.loads(event['snapshot']), {'content': body['content'], 'category': 'activity'})
        payload = build_payload(event, SECRET, self.now)
        text_nodes = []
        def visit(node):
            if isinstance(node, dict):
                self.assertNotIn(node.get('tag'), ('markdown', 'lark_md', 'at'))
                if 'content' in node:
                    self.assertEqual(node['tag'], 'plain_text')
                    text_nodes.append(node['content'])
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(payload)
        self.assertIn(body['content'], text_nodes)
        self.assertIn('活动玩法 · 珠海', text_nodes)
        self.assertIn('sign', payload)
        serialized = json.dumps(payload, ensure_ascii=False)
        for private in (self.visitor, '192.0.2.1', body['requestId'], receipt['id'], event['id'], URL, SECRET):
            self.assertNotIn(private, serialized)

    def test_feedback_survives_delivery_failure_reopen_retry_and_mark_read(self):
        self.enable()
        receipt = self.submit()
        sender = Mock(side_effect=DeliveryFailure('合成网络失败'))
        self.assertTrue(self.store.notifications.deliver_one(sender))
        self.assertEqual(self.events()[0]['status'], 'retrying')
        self.assertEqual(self.store.feedback.list()['total'], 1)
        self.store.feedback.set_status(receipt['id'], 'read')
        self.now += 6
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        good_sender = Mock()
        self.assertTrue(reopened.notifications.deliver_one(good_sender))
        good_sender.assert_called_once()
        self.assertEqual(self.events()[0]['status'], 'sent')
        self.assertEqual(reopened.feedback.list('read')['total'], 1)

    def test_failed_feedback_manual_retry_and_disable_cancel_keep_submission(self):
        self.enable()
        self.submit()
        self.store.notifications.deliver_one(Mock(side_effect=DeliveryFailure('合成不可重试错误', retryable=False)))
        self.assertEqual(self.events()[0]['status'], 'failed')
        self.assertEqual(self.store.notifications.retry_failed()['queuedCount'], 1)
        self.now += 1  # Respect the shared worker's 0.75-second sending interval.
        self.store.notifications.deliver_one(Mock())
        self.assertEqual(self.events()[0]['status'], 'sent')
        self.now += 1
        self.submit()
        self.store.notifications.save({'enabled': False})
        self.assertEqual(self.events()[-1]['status'], 'cancelled')
        self.assertEqual(self.store.feedback.list()['total'], 2)

    def test_upgrade_preserves_accounts_votes_analytics_posters_settings_and_outbox(self):
        self.store.create_admin('tester', 'Synthetic-password-only-481')
        token, _ = self.store.new_session('tester')
        self.enable()
        self.store.cast_vote(self.visitor, 'shenzhen', '192.0.2.1')
        self.store.notifications.queue_summary()
        self.store.track(self.visitor, 'home', str(uuid.uuid4()))
        schools = Store(str(self.path), [])
        schools.save({'school': '合成测试学校', 'contact': '合成负责人', 'count': 12, 'qr': '', 'consent': True}, 'synthetic-ip')
        picture = io.BytesIO()
        Image.new('RGB', (100, 160), 'green').save(picture, format='PNG')
        self.store.replace_poster('guangzhou', picture.getvalue(), 'image/png')
        self.store.position('guangzhou', 61, 42)
        with self.store.connect() as db:
            # Simulate the deployed pre-feedback schema with populated legacy data.
            db.execute('DROP TABLE campaign_feedback')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables}
        files = {path.name: path.read_bytes() for path in self.store.media_dir.iterdir()}
        upgraded = CampaignStore(self.path, clock=lambda: self.now)
        with upgraded.connect() as db:
            after = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables}
        self.assertEqual(before, after)
        self.assertEqual(files, {path.name: path.read_bytes() for path in upgraded.media_dir.iterdir()})
        self.assertTrue(upgraded.session(token)['authenticated'])
        self.assertEqual(upgraded.stats()['totalVotes'], 1)
        self.assertEqual(upgraded.feedback.list()['total'], 0)
        self.assertTrue(upgraded.notifications.settings()['enabled'])
        self.assertEqual(upgraded.notifications.settings()['pendingCount'], 1)


class FeedbackHTTPTests(unittest.TestCase):
    # Reuse only the isolated server harness, not its unrelated test methods.
    @classmethod
    def setUpClass(cls):
        http_support.CampaignHTTPTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        http_support.CampaignHTTPTests.tearDownClass.__func__(cls)

    setUp = http_support.CampaignHTTPTests.setUp
    tearDown = http_support.CampaignHTTPTests.tearDown
    request = http_support.CampaignHTTPTests.request
    setup_admin = http_support.CampaignHTTPTests.setup_admin

    def test_public_submit_receipt_repeat_conflict_and_no_public_listing(self):
        body = data()
        status, first, _ = self.request('POST', '/api/campaign/feedback', body)
        self.assertEqual(status, 201)
        self.assertEqual(set(first), {'ok', 'id', 'createdAt', 'duplicate'})
        status, repeated, _ = self.request('POST', '/api/campaign/feedback', body)
        self.assertEqual(status, 200)
        self.assertEqual(repeated, first | {'duplicate': True})
        self.assertEqual(self.request('POST', '/api/campaign/feedback', body | {'content': '另一条新内容用于验证冲突。'})[0], 409)
        self.assertEqual(self.request('GET', '/api/campaign/feedback')[0], 404)
        self.assertEqual(self.request('GET', '/api/admin/feedback')[0], 401)
        self.cookies.clear()
        status, other, _ = self.request('POST', '/api/campaign/feedback', body)
        self.assertEqual(status, 201)
        self.assertNotEqual(other['id'], first['id'])

    def test_origin_control_payload_limits_and_failed_writes_do_not_persist(self):
        for headers in ({'Origin': 'https://foreign.test'}, {'Origin': 'null'}, {'Origin': ''},
                        {'Origin': self.origin, 'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('POST', '/api/campaign/feedback', data(), headers)[0], 403)
        self.assertEqual(self.request('POST', '/api/campaign/feedback', data(), {'Content-Type': 'text/plain'})[0], 415)
        for body in (data(content='太短'), data(city='beijing'), data(category='bad'), data(contact='synthetic')):
            self.assertEqual(self.request('POST', '/api/campaign/feedback', body)[0], 400)
        self.assertEqual(self.request('POST', '/api/campaign/feedback', raw=b' ' * 33000)[0], 413)
        self.assertEqual(self.store.feedback.list()['total'], 0)

    def test_admin_list_state_requires_login_csrf_origin_and_valid_parameters(self):
        receipt = self.request('POST', '/api/campaign/feedback', data())[1]
        path = '/api/admin/feedback/' + receipt['id'] + '/status'
        self.assertEqual(self.request('POST', path, {'status': 'read'})[0], 401)
        self.setup_admin()
        status, listing, _ = self.request('GET', '/api/admin/feedback?status=new&page=1')
        self.assertEqual(status, 200)
        self.assertEqual((listing['unread'], listing['total'], listing['pageSize']), (1, 1, 20))
        self.assertEqual(self.request('POST', path, {'status': 'read'})[0], 403)
        self.assertEqual(self.request('POST', path, {'status': 'read'}, {'X-CSRF-Token': self.csrf, 'Origin': 'https://foreign.test'})[0], 403)
        headers = {'X-CSRF-Token': self.csrf}
        status, result, _ = self.request('POST', path, {'status': 'read'}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(result['unread'], 0)
        self.assertEqual(result['item']['status'], 'read')
        self.assertEqual(self.request('GET', '/api/admin/feedback?status=new')[1]['items'], [])
        for body in ({'status': 'all'}, {'status': True}, {'status': 'read', 'content': 'tamper'}, {}):
            self.assertEqual(self.request('POST', path, body, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/admin/feedback/bad/status', {'status': 'read'}, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/admin/feedback/' + '0' * 32 + '/status', {'status': 'read'}, headers)[0], 404)
        for query in ('page=0', 'page=-1', 'page=1.5', 'page=true', 'page=1000001', 'page=',
                      'status=unknown', 'status=', 'page=1&page=2', 'status=new&status=read',
                      'pageSize=100', 'page', 'page=1&bad=x'):
            self.assertEqual(self.request('GET', '/api/admin/feedback?' + query)[0], 400, query)
        self.assertEqual(self.request('GET', '/api/admin/feedback?status=all&page=2')[1]['items'], [])

    def test_http_quota_returns_429_but_same_receipt_still_retries(self):
        body = data()
        for item in (body, data(), data()):
            self.assertEqual(self.request('POST', '/api/campaign/feedback', item)[0], 201)
        self.assertEqual(self.request('POST', '/api/campaign/feedback', data())[0], 429)
        self.assertEqual(self.request('POST', '/api/campaign/feedback', body)[0], 200)

    def test_invalid_and_rate_limited_requests_do_not_create_visitors(self):
        def visitor_count():
            with self.store.connect() as db:
                return db.execute('SELECT COUNT(*) FROM campaign_visitors').fetchone()[0]
        for _ in range(3):
            self.cookies.clear()
            status, _, headers = self.request('POST', '/api/campaign/feedback', data(content='太短'))
            self.assertEqual(status, 400)
            self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.assertEqual(visitor_count(), 0)
        for _ in range(10):
            self.cookies.clear()
            self.assertEqual(self.request('POST', '/api/campaign/feedback', data())[0], 201)
        self.assertEqual(visitor_count(), 10)
        self.cookies.clear()
        status, _, headers = self.request('POST', '/api/campaign/feedback', data())
        self.assertEqual(status, 429)  # Successful-submission IP quota.
        self.assertEqual(visitor_count(), 10)
        self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        for _ in range(46):  # Fourteen real requests above plus these = 60/minute.
            self.store.limit('127.0.0.1', 'feedback-request', 60, 60)
        status, _, headers = self.request('POST', '/api/campaign/feedback', data())
        self.assertEqual(status, 429)  # Wider request-level guard, before identity creation.
        self.assertEqual(visitor_count(), 10)
        self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))


if __name__ == '__main__':
    unittest.main()
