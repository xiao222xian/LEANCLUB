"""Feishu regressions with temporary databases and mocked transport only."""
import base64
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
import hmac
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore, IPVoteConflict
from campaign_feishu import DeliveryFailure, build_payload, deliver, validate_webhook
from campaign_http import CampaignAPI
from store import Problem

# Deliberately synthetic, never sent; every transport is a fake.
URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-test-hook-0001'
SECRET = 'synthetic-signing-key-0001'


class FeishuStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000.0
        self.path = Path(self.tmp.name) / 'campaign.sqlite3'
        self.store = CampaignStore(self.path, clock=lambda: self.now)
        self.notifications = self.store.notifications

    def tearDown(self):
        self.tmp.cleanup()

    def enable(self, signing=False):
        return self.notifications.save({'enabled': True, 'webhook': URL, 'signingSecret': SECRET if signing else ''})

    def vote(self, city='shenzhen', ip='192.0.2.1', visitor=None):
        visitor = visitor or self.store.visitor(None)[0]
        self.store.cast_vote(visitor, city, ip)
        return visitor

    def events(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM campaign_feishu_outbox ORDER BY rowid')]

    def test_disabled_default_and_unconfigured_votes_do_not_queue_or_send(self):
        settings = self.notifications.settings()
        self.assertFalse(settings['enabled'])
        self.assertFalse(settings['configured'])
        self.vote()
        self.assertEqual(self.events(), [])
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))
        sender.assert_not_called()
        with self.assertRaises(Problem):
            self.notifications.save({'enabled': True})

    def test_settings_never_return_full_credentials_and_blank_values_preserve(self):
        self.enable(signing=True)
        safe = self.notifications.save({'enabled': True, 'webhook': '', 'signingSecret': ''})
        self.assertTrue(safe['hasSigningSecret'])
        self.assertIn('••••', safe['webhookMasked'])
        self.assertNotIn(URL, json.dumps(safe))
        self.assertNotIn(SECRET, json.dumps(safe))
        self.notifications.save({'enabled': True, 'clearSigningSecret': True})
        self.assertFalse(self.notifications.settings()['hasSigningSecret'])
        self.notifications.save({'enabled': False, 'clearWebhook': True})
        self.assertFalse(self.notifications.settings()['configured'])

    def test_invalid_types_and_conflicting_clear_commands_are_rejected(self):
        for data in ({'enabled': 'yes'}, {'enabled': True, 'unknown': 1}, {'enabled': False, 'webhook': []},
                     {'enabled': False, 'clearWebhook': 'yes'}, {'enabled': True, 'clearWebhook': True},
                     {'enabled': False, 'signingSecret': SECRET, 'clearSigningSecret': True},
                     {'enabled': False, 'signingSecret': 'bad\nsecret'}, {'enabled': False, 'signingSecret': 'x' * 257}):
            with self.subTest(data=data), self.assertRaises(Problem):
                self.notifications.save(data)

    def test_only_new_votes_notify_and_next_new_snapshot_includes_silent_changes(self):
        self.enable()
        visitor = self.vote()
        self.vote(visitor=visitor)
        second = self.store.visitor(None)[0]
        self.vote(visitor=second)  # Same IP and same city is idempotent.
        with self.assertRaises(IPVoteConflict):
            self.vote('guangzhou', visitor=second)
        self.vote('zhuhai', visitor=visitor)
        self.vote('guangzhou', ip='192.0.2.2')
        events = self.events()
        self.assertEqual([event['kind'] for event in events], ['new', 'new'])
        self.assertEqual([json.loads(event['snapshot']) for event in events], [
            {'shenzhen': 1, 'zhuhai': 0, 'guangzhou': 0},
            {'shenzhen': 0, 'zhuhai': 1, 'guangzhou': 1}])
        self.assertEqual(self.store.stats()['totalVotes'], 2)
        self.assertNotIn(visitor, json.dumps(events))
        self.assertNotIn('192.0.2.', json.dumps(events))

    def test_rollback_never_commits_a_vote_without_its_enabled_outbox_event(self):
        self.enable()
        enqueue = self.notifications.enqueue
        def fail_after_enqueue(*args, **kwargs):
            enqueue(*args, **kwargs)
            raise RuntimeError('synthetic transaction failure')
        with patch.object(self.notifications, 'enqueue', side_effect=fail_after_enqueue):
            with self.assertRaises(RuntimeError):
                self.vote()
        self.assertEqual(self.store.stats()['totalVotes'], 0)
        self.assertEqual(self.events(), [])

    def test_parallel_duplicate_posts_commit_one_vote_and_one_event(self):
        self.enable()
        visitor = self.store.visitor(None)[0]
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: self.vote(visitor=visitor), range(10)))
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        self.assertEqual(len(self.events()), 1)

    def test_pending_events_and_credentials_survive_database_reopen(self):
        self.enable(signing=True)
        self.vote()
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        self.assertEqual(reopened.notifications.settings()['pendingCount'], 1)
        sender = Mock()
        reopened.notifications.deliver_one(sender)
        sender.assert_called_once()
        self.assertEqual(sender.call_args.args[0], URL)
        self.assertIn('sign', sender.call_args.args[1])
        self.assertEqual(self.notifications.settings()['lastDelivery']['status'], 'sent')

    def test_interactive_card_includes_new_city_total_three_columns_and_valid_signature(self):
        self.enable(signing=True)
        visitor = self.vote()
        self.vote('zhuhai', visitor=visitor)
        self.vote('guangzhou', ip='192.0.2.2')
        event = self.events()[-1]
        payload = build_payload(event, SECRET, self.now)
        self.assertEqual(payload['msg_type'], 'interactive')
        self.assertEqual(payload['card']['schema'], '2.0')
        body = payload['card']['body']['elements']
        total = next(element for element in body if element.get('text_size') == 'heading-0')
        self.assertEqual(total['content'], '**2**')
        columns = next(element for element in body if element['tag'] == 'column_set')['columns']
        self.assertEqual(len(columns), 3)
        for column, name, count in zip(columns, ('深圳', '珠海', '广州'), (0, 1, 1)):
            self.assertEqual(column['weight'], 1)
            self.assertEqual([item['content'] for item in column['elements']], [name, '**%s**' % count, '票'])
        serialized = json.dumps(payload, ensure_ascii=False)
        for value in ('LEAN CLUB', '薄肌俱乐部', '广州 +1', '北京时间', '累计有效票'):
            self.assertIn(value, serialized)
        self.assertLess(len(serialized.encode('utf-8')), 20000)
        expected = base64.b64encode(hmac.new((str(int(self.now)) + '\n' + SECRET).encode(), b'', hashlib.sha256).digest()).decode()
        self.assertEqual(payload['timestamp'], str(int(self.now)))
        self.assertEqual(payload['sign'], expected)
        self.assertNotIn(visitor, serialized)
        self.assertNotIn(event['id'], serialized)
        self.assertNotIn(SECRET, serialized)

    def test_failed_network_preserves_votes_and_retries_with_same_event(self):
        self.enable()
        self.vote()
        sender = Mock(side_effect=[TimeoutError(URL + SECRET), None])
        self.notifications.deliver_one(sender)
        settings = self.notifications.settings()
        self.assertEqual(settings['lastDelivery']['status'], 'retrying')
        self.assertNotIn(URL, json.dumps(settings))
        self.assertNotIn(SECRET, json.dumps(settings))
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        self.assertFalse(self.notifications.deliver_one(sender))
        self.now += 5
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(sender.call_args_list[0].args[1], sender.call_args_list[1].args[1])
        self.assertEqual(self.notifications.settings()['lastDelivery']['status'], 'sent')

    def test_retry_is_bounded_then_admin_can_retry_failed_event(self):
        self.enable()
        self.vote()
        sender = Mock(side_effect=DeliveryFailure('模拟网络故障。'))
        for step in (0, 5, 30, 120, 600):
            self.now += step
            self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(sender.call_count, 5)
        self.assertEqual(self.notifications.settings()['failedCount'], 1)
        self.assertEqual(self.notifications.settings()['pendingCount'], 0)
        self.assertFalse(self.notifications.deliver_one(sender))
        result = self.notifications.retry_failed()
        self.assertEqual(result['queuedCount'], 1)
        self.now += 1
        self.notifications.deliver_one(Mock())
        self.assertEqual(self.notifications.settings()['failedCount'], 0)

    def test_permanent_error_fails_immediately_without_background_loop(self):
        self.enable()
        self.vote()
        sender = Mock(side_effect=DeliveryFailure('签名校验失败。', retryable=False))
        self.notifications.deliver_one(sender)
        self.now += 1000
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.notifications.settings()['lastDelivery']['attempts'], 1)
        self.assertEqual(self.notifications.settings()['failedCount'], 1)

    def test_worker_pacing_and_expired_lease_recovery(self):
        self.enable()
        self.vote()
        self.vote('zhuhai', ip='192.0.2.2')
        first, _ = self.notifications.claim()
        self.assertIsNone(self.notifications.claim())
        self.now += .75
        second, _ = self.notifications.claim()
        self.assertNotEqual(first['id'], second['id'])
        self.now += 31
        recovered, _ = self.notifications.claim()
        self.assertEqual(recovered['id'], first['id'])
        self.assertNotEqual(recovered['lease_token'], first['lease_token'])
        self.assertEqual(recovered['attempts'], 2)

    def test_network_delivery_does_not_hold_database_or_block_voting(self):
        self.enable()
        visitor = self.vote()
        sending, release = threading.Event(), threading.Event()
        def slow_send(*_):
            sending.set()
            if not release.wait(timeout=5):
                raise TimeoutError()
        worker = threading.Thread(target=self.notifications.deliver_one, args=(slow_send,))
        worker.start()
        try:
            self.assertTrue(sending.wait(timeout=2))
            self.vote('guangzhou', visitor=visitor)
            self.assertEqual(self.store.vote(visitor), {'city': 'guangzhou'})
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertEqual(len(self.events()), 1)

    def test_disabling_and_changing_robot_cancel_unsent_events(self):
        self.enable(signing=True)
        self.vote()
        self.notifications.save({'enabled': False})
        self.assertEqual(self.notifications.settings()['pendingCount'], 0)
        self.assertEqual(self.events()[0]['status'], 'cancelled')
        self.vote('zhuhai', ip='192.0.2.2')
        self.assertEqual(len(self.events()), 1)
        result = self.notifications.save({'enabled': True, 'webhook': URL + '2'})
        self.assertFalse(result['hasSigningSecret'])
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))

    def test_test_notification_works_when_disabled_and_never_adds_a_vote(self):
        self.notifications.save({'enabled': False, 'webhook': URL})
        self.vote('zhuhai')
        result = self.notifications.queue_test()
        self.assertTrue(result['queued'])
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        sender = Mock()
        self.notifications.deliver_one(sender)
        card = sender.call_args.args[1]['card']
        self.assertEqual(card['header']['subtitle']['content'], '连接测试')
        self.assertEqual(json.loads(self.events()[-1]['snapshot']), {'shenzhen': 0, 'zhuhai': 1, 'guangzhou': 0})

    def test_manual_summary_uses_current_counts_when_automatic_notifications_are_off(self):
        self.notifications.save({'enabled': False, 'webhook': URL})
        visitor = self.vote()
        self.vote('zhuhai', visitor=visitor)
        self.vote('guangzhou', ip='192.0.2.2')
        self.assertEqual(self.events(), [])
        result = self.notifications.queue_summary()
        self.assertTrue(result['queued'])
        self.assertEqual(result['settings']['lastDelivery']['kind'], 'summary')
        event = self.events()[-1]
        self.assertEqual(json.loads(event['snapshot']), {'shenzhen': 0, 'zhuhai': 1, 'guangzhou': 1})
        sender = Mock()
        self.notifications.deliver_one(sender)
        card = sender.call_args.args[1]['card']
        self.assertEqual(card['header']['subtitle']['content'], '当前票数汇总')
        self.assertNotIn('+1', json.dumps(card, ensure_ascii=False))
        self.assertEqual(self.store.stats()['totalVotes'], 2)
        self.assertFalse(self.notifications.settings()['enabled'])

    def test_manual_summary_with_no_votes_is_zero_not_a_fabricated_event(self):
        with self.assertRaises(Problem):
            self.notifications.queue_summary()
        self.notifications.save({'enabled': False, 'webhook': URL})
        self.notifications.queue_summary()
        self.assertEqual(json.loads(self.events()[0]['snapshot']), {'shenzhen': 0, 'zhuhai': 0, 'guangzhou': 0})
        self.assertEqual(self.store.stats()['totalVotes'], 0)

    def test_upgrade_cancels_legacy_change_jobs_in_all_unsent_states(self):
        self.enable()
        with self.store.connect() as db:
            for index, status in enumerate(('pending', 'retrying', 'failed', 'sending')):
                db.execute('''INSERT INTO campaign_feishu_outbox
                    (id,kind,city,previous_city,snapshot,created,revision,status,available,lease_until,lease_token)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                           ('legacy-change-%s' % index, 'change', 'zhuhai', 'shenzhen', '{}', int(self.now), 1,
                            status, self.now, self.now + 30, 'old-lease'))
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        self.assertTrue(all(event['status'] == 'cancelled' for event in self.events()))
        sender = Mock()
        self.assertFalse(reopened.notifications.deliver_one(sender))
        self.assertEqual(reopened.notifications.retry_failed()['queuedCount'], 0)
        sender.assert_not_called()

    def test_worker_and_retry_guard_against_newly_injected_legacy_change_jobs(self):
        self.enable()
        self.vote()
        with self.store.connect() as db:
            db.execute("UPDATE campaign_feishu_outbox SET kind='change',status='failed'")
        self.assertEqual(self.notifications.retry_failed()['queuedCount'], 0)
        with self.store.connect() as db:
            db.execute("UPDATE campaign_feishu_outbox SET status='pending'")
            self.assertIsNone(self.notifications.enqueue(db, 'change', 'zhuhai', 'shenzhen', int(self.now)))
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.events()[0]['status'], 'cancelled')
        sender.assert_not_called()
        with self.assertRaises(ValueError):
            self.notifications.queue_manual('change')
        with self.assertRaises(ValueError):
            build_payload(self.events()[0])

    def test_failed_summary_can_be_retried_with_automatic_notifications_off(self):
        self.notifications.save({'enabled': False, 'webhook': URL})
        self.notifications.queue_summary()
        self.notifications.deliver_one(Mock(side_effect=DeliveryFailure('模拟配置故障。', retryable=False)))
        self.assertEqual(self.notifications.retry_failed()['queuedCount'], 1)
        self.now += 1
        self.notifications.deliver_one(Mock())
        self.assertEqual(self.notifications.settings()['lastDelivery']['status'], 'sent')

    def request(self, method, path, data=None, token=None, csrf=None, origin='http://127.0.0.1:4180'):
        headers = Message()
        headers['Origin'] = origin
        headers['Content-Type'] = 'application/json'
        raw = json.dumps(data or {}).encode()
        headers['Content-Length'] = str(len(raw))
        if token:
            headers['Cookie'] = 'lean_admin=' + token
        if csrf:
            headers['X-CSRF-Token'] = csrf
        handler = SimpleNamespace(headers=headers, rfile=io.BytesIO(raw), response_cookies=[],
                                  connection=SimpleNamespace(settimeout=lambda _: None), ip=lambda: '127.0.0.1',
                                  send_json=lambda status, payload: (status, payload))
        api = CampaignAPI(self.store, 'http://127.0.0.1:4180')
        return (api.get if method == 'GET' else api.post)(handler, path)

    def assert_problem(self, status, callback):
        with self.assertRaises(Problem) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def test_admin_config_read_write_test_and_retry_are_protected(self):
        for method, path in [('GET', '/api/admin/feishu'), ('POST', '/api/admin/feishu'),
                             ('POST', '/api/admin/feishu/test'), ('POST', '/api/admin/feishu/summary'), ('POST', '/api/admin/feishu/retry')]:
            self.assert_problem(401, lambda: self.request(method, path))
        self.store.create_admin('tester', 'Synthetic-only-password-991')
        token, session = self.store.new_session('tester')
        csrf = session['csrfToken']
        self.assert_problem(403, lambda: self.request('POST', '/api/admin/feishu', {'enabled': True, 'webhook': URL}, token))
        self.assert_problem(403, lambda: self.request('POST', '/api/admin/feishu', {'enabled': True, 'webhook': URL}, token, csrf, 'https://foreign.test'))
        status, safe = self.request('POST', '/api/admin/feishu', {'enabled': True, 'webhook': URL, 'signingSecret': SECRET}, token, csrf)
        self.assertEqual(status, 200)
        self.assertNotIn(SECRET, json.dumps(safe))
        self.assertNotIn(URL, json.dumps(self.request('GET', '/api/admin/feishu', token=token)))
        self.assertEqual(self.request('POST', '/api/admin/feishu/test', token=token, csrf=csrf)[0], 202)
        self.assertEqual(self.request('POST', '/api/admin/feishu/retry', token=token, csrf=csrf)[0], 202)
        self.assert_problem(403, lambda: self.request('POST', '/api/admin/feishu/summary', token=token))
        self.notifications.save({'enabled': False})
        status, result = self.request('POST', '/api/admin/feishu/summary', token=token, csrf=csrf)
        self.assertEqual(status, 202)
        self.assertEqual(result['settings']['lastDelivery']['kind'], 'summary')
        self.assert_problem(400, lambda: self.request('POST', '/api/admin/feishu/summary', {'enabled': True}, token, csrf))
        self.store.logout(token)
        self.assert_problem(401, lambda: self.request('GET', '/api/admin/feishu', token=token))


class FeishuTransportTests(unittest.TestCase):
    def test_webhook_allowlist_rejects_redirect_targets_userinfo_ports_queries_and_controls(self):
        invalid = ('http://open.feishu.cn/open-apis/bot/v2/hook/synthetic-token',
                   URL.replace('open.feishu.cn', 'open.feishu.cn.evil.test'),
                   URL.replace('open.feishu.cn', '127.0.0.1'), URL.replace('open.feishu.cn', 'open.feishu.cn:444'),
                   URL.replace('open.feishu.cn', 'user:pass@open.feishu.cn'), URL + '?secret=x', URL + '#x',
                   URL + '/extra', URL.replace('synthetic', 'syn%74hetic'), URL.replace('hook/', 'hook/\n'), None)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(Problem):
                validate_webhook(value)
        self.assertEqual(validate_webhook(URL.replace('open.feishu.cn', 'open.feishu.cn:443')), URL)

    def response(self, status=200, body=None):
        response = Mock(status=status)
        response.read.return_value = json.dumps({'code': 0} if body is None else body).encode()
        connection = Mock()
        connection.getresponse.return_value = response
        return connection

    def test_transport_sends_only_to_fixed_host_and_accepts_both_success_formats(self):
        for body in ({'code': 0}, {'StatusCode': 0}):
            connection = self.response(body=body)
            with patch('campaign_feishu.HTTPSConnection', return_value=connection) as constructor:
                deliver(URL, {'msg_type': 'text', 'content': {'text': 'synthetic'}})
            constructor.assert_called_once_with('open.feishu.cn', timeout=5)
            self.assertEqual(connection.request.call_args.args[:2], ('POST', '/open-apis/bot/v2/hook/synthetic-test-hook-0001'))
            connection.close.assert_called_once()

    def test_redirect_is_not_followed_and_upstream_body_is_not_exposed(self):
        connection = self.response(302, {'msg': URL + SECRET, 'Location': 'http://127.0.0.1'})
        with patch('campaign_feishu.HTTPSConnection', return_value=connection), self.assertRaises(DeliveryFailure) as caught:
            deliver(URL, {})
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(URL, str(caught.exception))
        self.assertEqual(connection.request.call_count, 1)

    def test_http200_feishu_errors_and_malformed_success_are_not_counted_as_sent(self):
        for body in ({'code': 19021, 'msg': SECRET}, {'code': 19022}, {'code': 19024},
                     {'code': 11232}, {'code': True}, {'unexpected': 'shape'}, []):
            connection = self.response(body=body)
            with self.subTest(body=body), patch('campaign_feishu.HTTPSConnection', return_value=connection), self.assertRaises(DeliveryFailure) as caught:
                deliver(URL, {})
            self.assertNotIn(SECRET, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
