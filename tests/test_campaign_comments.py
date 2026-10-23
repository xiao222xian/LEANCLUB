"""Public opt-in comments on disposable databases; no real messages or users."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from store import Problem
import test_campaign as http_support


def data(**changes):
    return {'requestId': str(uuid.uuid4()), 'content': '合成测试：想参加周末公园训练。',
            'nickname': '合成同学', 'city': 'shenzhen', 'publishConsent': True} | changes


class CommentsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.path = Path(self.tmp.name) / 'comments.sqlite3'
        self.store = CampaignStore(self.path, clock=lambda: self.now)
        self.visitor = self.store.visitor(None)[0]

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, body=None, visitor=None, ip='192.0.2.1'):
        return self.store.comments.submit(visitor or self.visitor, ip, data() if body is None else body)

    def assert_problem(self, status, callback, *args):
        with self.assertRaises(Problem) as error:
            callback(*args)
        self.assertEqual(error.exception.status, status)

    def seed(self, count):
        for index in range(count):
            self.submit(data(content='合成分页评论%d' % index), self.store.visitor(None)[0], '192.0.2.%d' % (index + 1))

    def test_public_projection_and_separation_from_private_feedback(self):
        self.store.feedback.submit(self.visitor, '192.0.2.1', {
            'requestId': str(uuid.uuid4()), 'content': '合成私密意见绝不可公开', 'category': 'other', 'city': None})
        self.assertEqual(self.store.comments.public_list()['items'], [])
        receipt = self.submit()
        item = self.store.comments.public_list()['items'][0]
        self.assertEqual(set(item), {'id', 'content', 'nickname', 'city', 'createdAt'})
        self.assertEqual(item['id'], receipt['item']['id'])
        self.assertEqual(self.store.feedback.list()['total'], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT publish_consent FROM campaign_comments').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_feishu_outbox').fetchone()[0], 0)

    def test_idempotency_conflict_and_concurrent_replay(self):
        body = data()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.submit(body), range(8)))
        self.assertEqual(len({result['item']['id'] for result in results}), 1)
        self.assertEqual(sum(not result['duplicate'] for result in results), 1)
        for field, value in [('content', '合成不同评论'), ('nickname', '另一个合成昵称'), ('city', None)]:
            self.assert_problem(409, self.submit, body | {field: value})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_rate_events WHERE kind='comments-visitor'").fetchone()[0], 1)
        other = self.submit(body | {'content': '另一合成访客评论'}, self.store.visitor(None)[0])
        self.assertNotEqual(other['item']['id'], results[0]['item']['id'])

    def test_opt_in_and_text_validation(self):
        invalid = [{'publishConsent': False}, {'publishConsent': 1}, {'publishConsent': None},
                   {'content': ''}, {'content': '一'}, {'content': ' ' * 4}, {'content': '好' * 241},
                   {'content': None}, {'content': []}, {'content': '内容\x00'}, {'content': '内容\u202e'},
                   {'content': '内容\ud800'}, {'content': '\u200d' * 10}, {'nickname': '合成\n昵称'},
                   {'nickname': '好' * 21}, {'nickname': None}, {'nickname': '\u200d'},
                   {'city': 'beijing'}, {'city': True}, {'requestId': 'bad'}, {'visitor': 'forged'},
                   {'status': 'visible'}, {'contact': 'private'}, {'category': 'other'}]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_problem(400, self.submit, data(**changes))
        missing_consent = data()
        del missing_consent['publishConsent']
        self.assert_problem(400, self.submit, missing_consent)
        self.assertEqual(self.store.comments.list()['total'], 0)

    def test_unicode_normalization_defaults_and_plain_text_roundtrip(self):
        body = data(content=' 合成训练🏋️‍♀️\r\n一起出发。 ', nickname='  ', city=None)
        item = self.submit(body)['item']
        self.assertEqual(item['content'], '合成训练🏋️‍♀️\n一起出发。')
        self.assertEqual(item['nickname'], '薄肌同好')
        self.assertIsNone(item['city'])
        self.assertEqual(self.submit(data(content='字' * 240, nickname='字' * 20))['item']['content'], '字' * 240)
        text = '<script>alert("synthetic")</script>'
        self.assertEqual(self.submit(data(content=text))['item']['content'], text)

    def test_burst_and_day_limits_and_replay_after_quota(self):
        first = data()
        self.submit(first)
        for _ in range(2):
            self.submit()
        self.assert_problem(429, self.submit)
        self.assertTrue(self.submit(first)['duplicate'])
        for _ in range(7):
            self.now += 601
            self.submit()
        self.now += 601
        self.assert_problem(429, self.submit)
        self.now += 86401
        self.assertFalse(self.submit()['duplicate'])

    def test_ip_quota_survives_cookie_rotation_and_concurrent_quota(self):
        for _ in range(10):
            self.submit(visitor=self.store.visitor(None)[0])
        self.assert_problem(429, self.submit, data(), self.store.visitor(None)[0])
        self.now += 601
        def attempt(_):
            try:
                self.submit(ip='192.0.2.2')
                return 201
            except Problem as error:
                return error.status
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(attempt, range(8)))
        self.assertEqual(statuses.count(201), 3)
        self.assertEqual(statuses.count(429), 5)

    def test_cursor_handles_same_second_hidden_cursor_and_new_insert(self):
        self.seed(26)
        first = self.store.comments.public_list(limit=20)
        self.assertEqual((len(first['items']), first['total'], first['hasMore']), (20, 26, True))
        self.store.comments.set_status(first['items'][-1]['id'], 'hidden')
        self.now += 1
        newest = self.submit(ip='192.0.2.100')['item']
        second = self.store.comments.public_list(limit=20, cursor=first['nextCursor'])
        self.assertEqual(len(second['items']), 6)
        self.assertFalse(second['hasMore'])
        self.assertIsNone(second['nextCursor'])
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
        self.assertNotIn(newest['id'], {row['id'] for row in second['items']})
        self.assertEqual(self.store.comments.public_list()['items'][0]['id'], newest['id'])

    def test_moderation_hides_replay_never_republishes_and_reopen_persists(self):
        body = data()
        item = self.submit(body)['item']
        self.assertEqual(self.store.comments.set_status(item['id'], 'hidden')['item']['status'], 'hidden')
        self.assertEqual(self.store.comments.public_list()['items'], [])
        self.assertEqual(self.submit(body)['item']['status'], 'hidden')
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        self.assertEqual(reopened.comments.public_list()['total'], 0)
        self.assertEqual(reopened.comments.list('hidden')['total'], 1)
        self.store.comments.set_status(item['id'], 'visible')
        self.assertEqual(self.store.comments.public_list()['total'], 1)

    def test_admin_paging_filters_and_parameter_validation(self):
        self.seed(25)
        first, second = self.store.comments.list(), self.store.comments.list(page=2)
        self.assertEqual((first['total'], first['pageSize'], first['totalPages']), (25, 20, 2))
        self.assertEqual(len(second['items']), 5)
        self.assertEqual(set(first['items'][0]), {'id', 'content', 'nickname', 'city', 'createdAt', 'status'})
        selected = first['items'][0]['id']
        self.store.comments.set_status(selected, 'hidden')
        self.assertEqual(self.store.comments.list('hidden')['total'], 1)
        self.assertEqual(self.store.comments.list('visible')['total'], 24)
        for status, page in [('bad', 1), ([], 1), ('all', 0), ('all', True), ('all', 1000001)]:
            self.assert_problem(400, self.store.comments.list, status, page)
        for limit, cursor in [(0, None), (51, None), (True, None), (20, ''), (20, 'bad'), (20, []), (20, '9' * 20 + '.' + 'a' * 32)]:
            self.assert_problem(400, self.store.comments.public_list, limit, cursor)
        for item_id, status in [('bad', 'visible'), (selected, 'all'), (selected, None)]:
            self.assert_problem(400, self.store.comments.set_status, item_id, status)
        self.assert_problem(404, self.store.comments.set_status, '0' * 32, 'hidden')


class CommentsHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        http_support.CampaignHTTPTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        http_support.CampaignHTTPTests.tearDownClass.__func__(cls)

    setUp = http_support.CampaignHTTPTests.setUp
    tearDown = http_support.CampaignHTTPTests.tearDown
    request = http_support.CampaignHTTPTests.request

    def visitor_count(self):
        with self.store.connect() as db:
            return db.execute('SELECT COUNT(*) FROM campaign_visitors').fetchone()[0]

    def test_get_never_issues_identity_and_post_reuses_vote_identity(self):
        status, listing, headers = self.request('GET', '/api/campaign/comments')
        self.assertEqual(status, 200)
        self.assertEqual(listing, {'items': [], 'total': 0, 'hasMore': False, 'nextCursor': None})
        self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.assertEqual(self.visitor_count(), 0)
        self.request('GET', '/api/campaign')
        token = self.cookies['lean_visitor']
        body = data()
        status, first, headers = self.request('POST', '/api/campaign/comments', body)
        self.assertEqual(status, 201)
        self.assertEqual(self.cookies['lean_visitor'], token)
        self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.assertEqual(self.visitor_count(), 1)
        status, repeat, _ = self.request('POST', '/api/campaign/comments', body)
        self.assertEqual((status, repeat), (200, first | {'duplicate': True}))
        self.assertEqual(self.request('POST', '/api/campaign/comments', body | {'content': '不同合成内容'})[0], 409)
        self.store.comments.set_status(first['item']['id'], 'hidden')
        self.assertEqual(self.request('GET', '/api/campaign/comments')[1]['items'], [])
        self.assertEqual(self.request('POST', '/api/campaign/comments', body)[1]['item']['status'], 'hidden')

    def test_origin_payload_queries_and_no_public_moderation(self):
        for headers in ({'Origin': 'https://foreign.test'}, {'Origin': 'null'}, {'Origin': ''},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('POST', '/api/campaign/comments', data(), headers)[0], 403)
        self.assertEqual(self.request('POST', '/api/campaign/comments', data(), {'Content-Type': 'text/plain'})[0], 415)
        # Declare an oversized body without filling the socket after the server
        # has already rejected its length (Windows can reset that connection).
        self.assertEqual(self.request('POST', '/api/campaign/comments', raw=b'{}', headers={'Content-Length': '33000'})[0], 413)
        self.assertEqual(self.request('POST', '/api/campaign/comments', raw=b'{"publishConsent":true,"publishConsent":false}')[0], 400)
        for query in ('limit=0', 'limit=51', 'limit=true', 'limit=', 'limit=1&limit=2', 'cursor=', 'cursor=bad',
                      'status=hidden', 'page=1', 'limit', 'limit=20&cursor=x&extra=y'):
            self.assertEqual(self.request('GET', '/api/campaign/comments?' + query)[0], 400, query)
        self.assertEqual(self.request('POST', '/api/campaign/comments/' + '0' * 32 + '/status', {'status': 'visible'})[0], 404)
        self.assertEqual(self.store.comments.list()['total'], 0)
        self.assertEqual(self.visitor_count(), 0)

    def test_failed_and_limited_writes_do_not_issue_or_persist_new_identity(self):
        for body in (data(publishConsent=False), data(content='一'), data(status='visible')):
            status, _, headers = self.request('POST', '/api/campaign/comments', body)
            self.assertEqual(status, 400)
            self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.assertEqual(self.visitor_count(), 0)
        for _ in range(10):
            self.cookies.clear()
            self.assertEqual(self.request('POST', '/api/campaign/comments', data())[0], 201)
        self.cookies.clear()
        status, _, headers = self.request('POST', '/api/campaign/comments', data())
        self.assertEqual(status, 429)
        self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.assertEqual(self.visitor_count(), 10)

    def test_cursor_and_plain_text_content_served_as_nosniff_json(self):
        for index in range(3):
            self.request('POST', '/api/campaign/comments', data(content='<svg onload=alert(%d)>' % index))
        status, first, headers = self.request('GET', '/api/campaign/comments?limit=2')
        self.assertEqual(status, 200)
        self.assertEqual(dict(headers)['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(dict(headers)['Cache-Control'], 'no-store')
        self.assertEqual(len(first['items']), 2)
        second = self.request('GET', '/api/campaign/comments?limit=2&cursor=' + first['nextCursor'])[1]
        self.assertEqual(len(second['items']), 1)
        self.assertFalse(first['items'][0]['id'] == second['items'][0]['id'])


if __name__ == '__main__':
    unittest.main()
