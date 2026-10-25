"""Public opt-in comments on disposable databases; no real messages or users."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from campaign_feishu import DeliveryFailure
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
        self.assertEqual(set(item), {'id', 'content', 'nickname', 'city', 'createdAt', 'reply',
                                    'rootId', 'parentId', 'replyToName', 'replyToOfficial',
                                    'replies', 'replyCount', 'repliesNextCursor'})
        self.assertEqual(item['id'], receipt['item']['id'])
        self.assertEqual(self.store.feedback.list()['total'], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT publish_consent FROM campaign_comments').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_feishu_outbox').fetchone()[0], 0)

    def test_idempotency_conflict_and_concurrent_replay(self):
        self.store.notifications.save({'enabled': True, 'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-test-hook-0001'})
        body = data()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.submit(body), range(8)))
        self.assertEqual(len({result['item']['id'] for result in results}), 1)
        self.assertEqual(sum(not result['duplicate'] for result in results), 1)
        for field, value in [('content', '合成不同评论'), ('nickname', '另一个合成昵称'), ('city', None)]:
            self.assert_problem(409, self.submit, body | {field: value})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_rate_events WHERE kind='comments-visitor'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_feishu_outbox WHERE kind='comment'").fetchone()[0], 1)
        other = self.submit(body | {'content': '另一合成访客评论'}, self.store.visitor(None)[0])
        self.assertNotEqual(other['item']['id'], results[0]['item']['id'])

    def test_comment_notification_survives_restart_and_sends_plain_text(self):
        self.store.notifications.save({'enabled': True, 'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-test-hook-0001'})
        body = data(content='<at id=all>合成测试</at> **不执行标记**', nickname='合成<昵称>')
        self.submit(body)
        self.submit(body)
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        captured = []
        self.assertTrue(reopened.notifications.deliver_one(lambda url, payload: captured.append(payload)))
        card = captured[0]['card']
        self.assertEqual(card['header']['subtitle']['content'], '收到一条公开留言')
        elements = card['body']['elements']
        self.assertEqual(elements[0]['text']['content'], body['nickname'] + ' · 深圳')
        self.assertEqual(elements[1]['text'], {'tag': 'plain_text', 'content': body['content'], 'text_size': 'normal'})
        with reopened.connect() as db:
            events = db.execute('SELECT kind,status,attempts,snapshot FROM campaign_feishu_outbox').fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(tuple(events[0])[:3], ('comment', 'sent', 1))
        self.assertEqual(set(json.loads(events[0]['snapshot'])), {'content', 'nickname'})

    def test_notification_retry_preserves_comment_and_pause_does_not_backfill(self):
        settings = {'enabled': False, 'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-test-hook-0001'}
        self.store.notifications.save(settings)
        self.submit()
        self.store.notifications.save(settings | {'enabled': True})
        self.assertIsNone(self.store.notifications.settings()['lastDelivery'])
        self.submit(data(content='合成网络失败测试'))
        def reject(url, payload):
            raise DeliveryFailure('合成暂时失败')
        self.store.notifications.deliver_one(reject)
        self.assertEqual(self.store.comments.public_list()['total'], 2)
        self.assertEqual(self.store.notifications.settings()['lastDelivery']['status'], 'retrying')
        self.now += 6
        self.store.notifications.deliver_one(lambda url, payload: None)
        self.assertEqual(self.store.notifications.settings()['lastDelivery']['status'], 'sent')

    def test_comment_and_notification_are_atomic(self):
        with patch.object(self.store.notifications, 'enqueue', side_effect=RuntimeError('synthetic queue failure')):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertEqual(self.store.comments.public_list()['total'], 0)

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
        self.assertEqual(set(first['items'][0]), {'id', 'content', 'nickname', 'city', 'createdAt', 'status', 'reply', 'replyRevision',
                                                'rootId', 'parentId', 'replyToName', 'replyToOfficial', 'parentContent'})
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

    def test_reply_create_edit_withdraw_persist_and_never_becomes_a_comment(self):
        original = self.submit()['item']
        saved = self.store.comments.set_reply(original['id'], {'content': '周末见！\n<svg onload=alert(1)>', 'revision': 0})['item']
        self.assertEqual(saved['replyRevision'], 1)
        self.assertEqual(saved['reply']['content'], '周末见！\n<svg onload=alert(1)>')
        self.assertEqual(CampaignStore(self.path).comments.public_list()['items'][0]['reply'], saved['reply'])
        self.now += 60
        updated = self.store.comments.set_reply(original['id'], {'content': '时间确定后会在这里回复。', 'revision': 1})['item']
        self.assertEqual(updated['replyRevision'], 2)
        self.assertNotEqual(saved['reply']['updatedAt'], updated['reply']['updatedAt'])
        public = self.store.comments.public_list()
        self.assertEqual(public['total'], 1)
        self.assertEqual(public['items'][0]['createdAt'], original['createdAt'])
        self.assertNotIn('replyRevision', public['items'][0])
        self.assertEqual(self.store.comments.list(reply='unreplied')['total'], 0)
        self.assertEqual(self.store.comments.list(reply='replied')['total'], 1)
        withdrawn = self.store.comments.set_reply(original['id'], {'content': None, 'revision': 2})['item']
        self.assertIsNone(withdrawn['reply'])
        self.assertEqual(withdrawn['replyRevision'], 3)
        self.assertIsNone(CampaignStore(self.path).comments.public_list()['items'][0]['reply'])
        self.assertEqual(self.store.comments.list(reply='unreplied')['total'], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_feishu_outbox').fetchone()[0], 0)

    def test_hidden_comment_reply_is_admin_only_until_restored(self):
        item = self.submit()['item']
        self.store.comments.set_reply(item['id'], {'content': '合成主办方回复', 'revision': 0})
        self.store.comments.set_status(item['id'], 'hidden')
        self.assertEqual(self.store.comments.public_list()['items'], [])
        self.assertIsNotNone(self.store.comments.list('hidden')['items'][0]['reply'])
        self.store.comments.set_status(item['id'], 'visible')
        self.assertEqual(self.store.comments.public_list()['items'][0]['reply']['content'], '合成主办方回复')

    def test_reply_retries_and_concurrent_edit_cannot_overwrite(self):
        item = self.submit()['item']
        body = {'content': '合成回复甲', 'revision': 0}
        saved = self.store.comments.set_reply(item['id'], body)
        self.assertEqual(self.store.comments.set_reply(item['id'], body), saved)
        self.assert_problem(409, self.store.comments.set_reply, item['id'], {'content': '合成旧版本修改', 'revision': 0})
        def edit(content):
            try:
                self.store.comments.set_reply(item['id'], {'content': content, 'revision': 1})
                return 200
            except Problem as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(edit, ['合成回复乙', '合成回复丙'])), [200, 409])
        self.assert_problem(409, self.store.comments.set_reply, item['id'], {'content': None, 'revision': 1})
        self.assertEqual(self.store.comments.list()['items'][0]['replyRevision'], 2)

    def test_reply_validation_and_existing_database_migration(self):
        item = self.submit()['item']
        for body in ({}, {'content': '合成', 'revision': True}, {'content': '合成', 'revision': -1},
                     {'content': '合成', 'revision': 0, 'extra': 1}, {'content': '', 'revision': 0},
                     {'content': ' \n ', 'revision': 0}, {'content': 12, 'revision': 0},
                     {'content': '字' * 501, 'revision': 0}, {'content': 'a\x00b', 'revision': 0}):
            self.assert_problem(400, self.store.comments.set_reply, item['id'], body)
        self.assert_problem(400, self.store.comments.set_reply, 'bad', {'content': '合成', 'revision': 0})
        self.assert_problem(404, self.store.comments.set_reply, '0' * 32, {'content': '合成', 'revision': 0})
        self.assert_problem(400, self.store.comments.list, 'all', 1, 'bad')
        # Recreate the legacy table shape, preserving a pre-existing public comment.
        with self.store.connect() as db:
            for column in ('reply_content', 'reply_updated', 'reply_revision'):
                db.execute('ALTER TABLE campaign_comments DROP COLUMN ' + column)
        reopened = CampaignStore(self.path)
        migrated = reopened.comments.list()['items'][0]
        self.assertEqual(migrated['id'], item['id'])
        self.assertIsNone(migrated['reply'])
        self.assertEqual(migrated['replyRevision'], 0)
        reopened.comments.set_reply(item['id'], {'content': '迁移后回复', 'revision': 0})
        self.assertEqual(reopened.comments.public_list()['total'], 1)

    def test_real_replies_stay_in_thread_and_admin_has_context(self):
        root = self.submit(data(nickname='合成发起人'))['item']
        child = self.submit(data(parentId=root['id'], nickname='合成参与人'))['item']
        grandchild = self.submit(data(parentId=child['id'], content='合成继续讨论'))['item']
        self.assertEqual((root['rootId'], root['parentId'], root['replyToName']), (root['id'], None, None))
        self.assertEqual((child['rootId'], child['parentId'], child['replyToName']), (root['id'], root['id'], '合成发起人'))
        self.assertEqual((grandchild['rootId'], grandchild['parentId'], grandchild['replyToName']), (root['id'], child['id'], '合成参与人'))
        admin = {item['id']: item for item in self.store.comments.list()['items']}
        self.assertEqual(admin[grandchild['id']]['parentContent'], child['content'])
        self.assertNotIn('parentContent', grandchild)
        self.assertNotIn('replyRevision', grandchild)
        public = self.store.comments.public_list()
        self.assertEqual(public['total'], 1)
        self.assertEqual(public['items'][0]['replyCount'], 2)
        self.assertEqual({item['id'] for item in public['items'][0]['replies']}, {child['id'], grandchild['id']})
        self.assertNotIn('parentContent', public['items'][0]['replies'][0])
        self.assertEqual(self.store.comments.list()['total'], 3)
        self.assertEqual(CampaignStore(self.path).comments.public_replies(root['id'])['total'], 2)

    def test_replying_to_official_reply_and_withdrawn_target(self):
        root = self.submit()['item']
        official = {'content': '合成主办方回复', 'revision': 0}
        self.assert_problem(409, self.submit, data(parentId=root['id'], replyToOfficial=True))
        self.store.comments.set_reply(root['id'], official)
        child = self.submit(data(parentId=root['id'], replyToOfficial=True))['item']
        self.assertEqual((child['replyToName'], child['replyToOfficial']), ('薄肌学院', True))
        admin = {item['id']: item for item in self.store.comments.list()['items']}
        self.assertEqual(admin[child['id']]['parentContent'], official['content'])
        self.store.comments.set_reply(child['id'], {'content': '合成楼内主办方回复', 'revision': 0})
        grandchild = self.submit(data(parentId=child['id'], replyToOfficial=True))['item']
        self.assertEqual((grandchild['rootId'], grandchild['replyToName']), (root['id'], '薄肌学院'))
        self.store.comments.set_reply(root['id'], {'content': None, 'revision': 1})
        self.assert_problem(409, self.submit, data(parentId=root['id'], replyToOfficial=True))
        self.assertEqual(self.store.comments.public_replies(root['id'])['total'], 2)

    def test_reply_target_validation_and_no_official_impersonation(self):
        root = self.submit()['item']
        invalid = [{'parentId': ''}, {'parentId': 123}, {'parentId': []}, {'parentId': 'missing'},
                   {'replyToOfficial': True}, {'replyToOfficial': 1}, {'replyToOfficial': None},
                   {'parentId': root['id'], 'rootId': '0' * 32}, {'author': 'official'}, {'official': True}]
        for body in invalid:
            self.assert_problem(400, self.submit, data(**body))
        for nickname in ('薄肌学院', 'LEAN SCHOOL', 'leanSchool', 'ＬＥＡＮ　ＳＣＨＯＯＬ', '薄肌\u200d学院'):
            self.assert_problem(400, self.submit, data(nickname=nickname))
        self.assert_problem(404, self.submit, data(parentId='0' * 32))
        self.assertEqual(self.store.comments.list()['total'], 1)

    def test_hidden_root_blocks_whole_thread_and_hidden_target_is_anonymous(self):
        root = self.submit(data(nickname='合成根昵称'))['item']
        child = self.submit(data(parentId=root['id'], nickname='隐藏后不可泄漏的合成昵称'))['item']
        self.store.comments.set_reply(child['id'], {'content': '合成官方内容', 'revision': 0})
        grandchild_body = data(parentId=child['id'])
        grandchild = self.submit(grandchild_body)['item']
        official_child = self.submit(data(parentId=child['id'], replyToOfficial=True))['item']
        self.store.comments.set_status(child['id'], 'hidden')
        page = self.store.comments.public_replies(root['id'])
        self.assertEqual({item['id'] for item in page['items']}, {grandchild['id'], official_child['id']})
        self.assertTrue(all(item['replyToName'] == '已隐藏的留言' for item in page['items']))
        self.assertNotIn(child['nickname'], json.dumps(page, ensure_ascii=False))
        replay = self.submit(grandchild_body)['item']
        self.assertEqual(replay['replyToName'], '已隐藏的留言')
        self.assertNotIn('parentContent', replay)
        self.assert_problem(404, self.submit, data(parentId=child['id']))
        self.store.comments.set_status(root['id'], 'hidden')
        self.assertEqual(self.store.comments.public_list()['items'], [])
        self.assert_problem(404, self.store.comments.public_replies, root['id'])
        self.assert_problem(404, self.submit, data(parentId=grandchild['id']))
        # Moderation retains every record and can still edit an official response.
        self.assertEqual(self.store.comments.list()['total'], 4)
        self.store.comments.set_reply(grandchild['id'], {'content': '合成后台处理', 'revision': 0})
        self.store.comments.set_status(root['id'], 'visible')
        self.assertEqual(self.store.comments.public_list()['items'][0]['replyCount'], 2)

    def test_thread_cursor_preview_same_second_hidden_boundary_and_new_reply(self):
        root = self.submit()['item']
        created = [self.submit(data(parentId=root['id'], content='合成楼内回复%d' % index))['item'] for index in range(7)]
        first = self.store.comments.public_list()['items'][0]
        self.assertEqual((len(first['replies']), first['replyCount']), (3, 7))
        self.assertIsNotNone(first['repliesNextCursor'])
        ordered = sorted(item['id'] for item in created)
        self.assertEqual([item['id'] for item in first['replies']], ordered[:3])
        self.store.comments.set_status(first['replies'][-1]['id'], 'hidden')
        self.now += 1
        newest = self.submit(data(parentId=root['id']))['item']
        second = self.store.comments.public_replies(root['id'], 2, first['repliesNextCursor'])
        self.assertEqual([item['id'] for item in second['items']], ordered[3:5])
        third = self.store.comments.public_replies(root['id'], 20, second['nextCursor'])
        self.assertEqual([item['id'] for item in third['items']], ordered[5:] + [newest['id']])
        self.assertFalse(third['hasMore'])
        self.assertIsNone(third['nextCursor'])
        self.assertEqual(third['total'], 7)
        self.assert_problem(404, self.store.comments.public_replies, created[0]['id'])
        self.assert_problem(400, self.store.comments.public_replies, 'bad')
        self.assert_problem(400, self.store.comments.public_replies, root['id'], 51)
        self.assert_problem(400, self.store.comments.public_replies, root['id'], 20, 'bad')

    def test_reply_idempotency_target_conflict_atomic_notification_and_quotas(self):
        root = self.submit()['item']
        self.store.notifications.save({'enabled': True, 'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-test-hook-0001'})
        body = data(parentId=root['id'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.submit(body), range(5)))
        self.assertEqual(sum(not result['duplicate'] for result in results), 1)
        self.assertEqual(len({result['item']['id'] for result in results}), 1)
        self.store.comments.set_reply(root['id'], {'content': '合成官方回复', 'revision': 0})
        self.assert_problem(409, self.submit, body | {'parentId': None})
        self.assert_problem(409, self.submit, body | {'replyToOfficial': True})
        with patch.object(self.store.notifications, 'enqueue', side_effect=RuntimeError('synthetic queue failure')):
            with self.assertRaises(RuntimeError):
                self.submit(data(parentId=root['id']))
        self.assertEqual(self.store.comments.public_replies(root['id'])['total'], 1)
        for _ in range(11):
            self.submit(data(parentId=root['id']))
        self.assert_problem(429, self.submit, data(parentId=root['id']))
        self.assertTrue(self.submit(body)['duplicate'])
        # Replies have an independent quota; root comments retain their original limit.
        self.submit()
        self.submit()
        self.assert_problem(429, self.submit)
        for index in range(48):
            self.now += 601
            self.submit(data(parentId=root['id']), ip='192.0.2.2')
        self.now += 601
        self.assert_problem(429, self.submit, data(parentId=root['id']))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_feishu_outbox WHERE kind='comment'").fetchone()[0], 62)

    def test_thread_schema_migrates_legacy_roots_without_losing_official_reply(self):
        root = self.submit()['item']
        self.store.comments.set_reply(root['id'], {'content': '合成已有官方回复', 'revision': 0})
        with self.store.connect() as db:
            db.execute('DROP INDEX campaign_comments_roots')
            db.execute('DROP INDEX campaign_comments_thread')
            for column in ('root_id', 'parent_id', 'reply_to_official'):
                db.execute('ALTER TABLE campaign_comments DROP COLUMN ' + column)
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        migrated = reopened.comments.public_list()['items'][0]
        self.assertEqual((migrated['id'], migrated['rootId'], migrated['parentId']), (root['id'], root['id'], None))
        self.assertEqual(migrated['reply']['content'], '合成已有官方回复')
        self.assertEqual((migrated['replyCount'], migrated['replies']), (0, []))
        reopened.comments.submit(self.visitor, '192.0.2.1', data(parentId=root['id']))
        self.assertEqual(CampaignStore(self.path).comments.public_list()['items'][0]['replyCount'], 1)


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
    setup_admin = http_support.CampaignHTTPTests.setup_admin

    def test_admin_reply_requires_session_origin_csrf_and_syncs_public(self):
        original = self.request('POST', '/api/campaign/comments', data())[1]['item']
        path = '/api/admin/comments/' + original['id'] + '/reply'
        body = {'content': '合成主办方回复', 'revision': 0}
        self.assertEqual(self.request('GET', '/api/admin/comments')[0], 401)
        self.assertEqual(self.request('POST', path, body)[0], 401)
        self.setup_admin()
        self.assertEqual(self.request('POST', path, body)[0], 403)
        headers = {'X-CSRF-Token': self.csrf}
        self.assertEqual(self.request('POST', path, body, headers | {'Origin': 'https://foreign.test'})[0], 403)
        self.assertEqual(self.request('POST', path, body, headers)[0], 200)
        self.assertEqual(self.request('GET', '/api/admin/comments?reply=replied')[1]['total'], 1)
        self.assertEqual(self.request('GET', '/api/admin/comments?reply=unreplied')[1]['total'], 0)
        public = self.request('GET', '/api/campaign/comments')[1]['items'][0]
        self.assertEqual(public['reply']['content'], body['content'])
        self.assertEqual(self.request('POST', path, body | {'content': '冲突内容'}, headers)[0], 409)
        self.assertEqual(self.request('POST', path, {'content': None, 'revision': 1}, headers)[0], 200)
        self.assertIsNone(self.request('GET', '/api/campaign/comments')[1]['items'][0]['reply'])
        status_path = path.removesuffix('reply') + 'status'
        self.assertEqual(self.request('POST', status_path, {'status': 'hidden'})[0], 403)
        self.assertEqual(self.request('POST', status_path, {'status': 'hidden'}, headers)[0], 200)
        self.assertEqual(self.request('GET', '/api/campaign/comments')[1]['total'], 0)
        for query in ('reply=bad', 'reply=all&reply=unreplied', 'page=0', 'page=', 'status=bad', 'extra=1'):
            self.assertEqual(self.request('GET', '/api/admin/comments?' + query)[0], 400, query)

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

    def test_public_thread_http_contract_and_admin_continuation(self):
        root = self.request('POST', '/api/campaign/comments', data())[1]['item']
        body = data(parentId=root['id'], content='<svg onload=alert(1)> 合成回复')
        status, receipt, _ = self.request('POST', '/api/campaign/comments', body)
        self.assertEqual(status, 201)
        child = receipt['item']
        self.assertEqual((child['rootId'], child['parentId']), (root['id'], root['id']))
        self.assertNotIn('parentContent', child)
        self.assertNotIn('replyRevision', child)
        self.assertEqual(self.request('POST', '/api/campaign/comments', body)[0], 200)
        path = '/api/campaign/comments/' + root['id'] + '/replies'
        status, page, headers = self.request('GET', path)
        self.assertEqual((status, page['total'], page['items'][0]['content']), (200, 1, body['content']))
        self.assertEqual(dict(headers)['Cache-Control'], 'no-store')
        self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.setup_admin()
        admin = {item['id']: item for item in self.request('GET', '/api/admin/comments')[1]['items']}
        self.assertEqual(admin[child['id']]['parentContent'], root['content'])
        self.assertEqual(self.request('POST', '/api/admin/comments/' + child['id'] + '/reply',
                                     {'content': '合成官方楼内回复', 'revision': 0}, {'X-CSRF-Token': self.csrf})[0], 200)
        self.assertEqual(self.request('GET', path)[1]['items'][0]['reply']['content'], '合成官方楼内回复')
        self.assertEqual(self.request('GET', '/api/campaign/comments')[1]['total'], 1)
        for query in ('limit=0', 'limit=51', 'limit=', 'cursor=', 'cursor=bad', 'limit=1&limit=2', 'status=hidden', 'extra=1'):
            self.assertEqual(self.request('GET', path + '?' + query)[0], 400, query)
        self.assertEqual(self.request('GET', '/api/campaign/comments/' + child['id'] + '/replies')[0], 404)
        self.store.comments.set_status(root['id'], 'hidden')
        self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('POST', '/api/campaign/comments', data(parentId=child['id']))[0], 404)

    def test_reply_failures_rollback_new_identity_and_require_same_origin(self):
        root = self.request('POST', '/api/campaign/comments', data())[1]['item']
        self.cookies.clear()
        before = self.visitor_count()
        self.assertEqual(self.request('POST', '/api/campaign/comments', data(parentId=root['id']),
                                     {'Origin': 'https://foreign.test'})[0], 403)
        for body, expected in ((data(parentId='0' * 32), 404),
                               (data(parentId=root['id'], replyToOfficial=True), 409),
                               (data(parentId=root['id'], publishConsent=False), 400),
                               (data(parentId=root['id'], rootId=root['id']), 400)):
            status, _, headers = self.request('POST', '/api/campaign/comments', body)
            self.assertEqual(status, expected)
            self.assertFalse(any(key.lower() == 'set-cookie' for key, _ in headers))
        self.assertEqual(self.visitor_count(), before)


if __name__ == '__main__':
    unittest.main()
