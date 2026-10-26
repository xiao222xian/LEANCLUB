"""Beijing-time summaries and immediate messages on synthetic, local data only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from campaign_feishu import DeliveryFailure, latest_summary_slot


# Deliberately synthetic; deliver_one always receives a fake transport.
URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-test-hook-0001'


def utc(value):
    """Test inputs include an explicit UTC offset, independent of host timezone."""
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class FeishuScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 2026-10-26 07:59:59 in Beijing.
        self.now = utc('2026-10-25T23:59:59Z')
        self.path = Path(self.tmp.name) / 'schedule.sqlite3'
        self.store = CampaignStore(self.path, clock=lambda: self.now)
        self.notifications = self.store.notifications

    def tearDown(self):
        self.tmp.cleanup()

    def enable(self):
        self.notifications.save({'enabled': True, 'webhook': URL})

    def at(self, moment):
        self.now = utc(moment)

    def events(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM campaign_feishu_outbox ORDER BY rowid')]

    def cursor(self):
        with self.store.connect() as db:
            return db.execute('SELECT cursor FROM campaign_feishu_schedule WHERE id=1').fetchone()[0]

    def test_latest_slot_uses_beijing_time_at_all_four_boundaries(self):
        cases = (
            ('2026-10-25T23:59:59.999Z', '2026-10-25T14:00:00Z'),
            ('2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z'),
            ('2026-10-26T04:59:59.999Z', '2026-10-26T00:00:00Z'),
            ('2026-10-26T05:00:00Z', '2026-10-26T05:00:00Z'),
            ('2026-10-26T09:59:59.999Z', '2026-10-26T05:00:00Z'),
            ('2026-10-26T10:00:00Z', '2026-10-26T10:00:00Z'),
            ('2026-10-26T13:59:59.999Z', '2026-10-26T10:00:00Z'),
            ('2026-10-26T14:00:00Z', '2026-10-26T14:00:00Z'),
            ('2026-10-26T16:00:00Z', '2026-10-26T14:00:00Z'),
            ('2026-10-26T23:59:59.999Z', '2026-10-26T14:00:00Z'),
        )
        for moment, expected in cases:
            with self.subTest(moment=moment):
                self.assertEqual(latest_summary_slot(utc(moment)), utc(expected))

    def test_each_daily_slot_sends_even_when_counts_stay_zero(self):
        self.enable()
        sender = Mock()
        for hour in ('00', '05', '10', '14'):
            self.at('2026-10-26T%s:00:00Z' % hour)
            self.now -= .001
            self.assertFalse(self.notifications.deliver_one(sender))
            self.now += .001
            self.assertTrue(self.notifications.deliver_one(sender))
            self.assertFalse(self.notifications.deliver_one(sender))
            self.now += 1
            self.assertFalse(self.notifications.deliver_one(sender))
        events = self.events()
        self.assertEqual(sender.call_count, 4)
        self.assertEqual([event['kind'] for event in events], ['scheduled_summary'] * 4)
        self.assertTrue(all(event['status'] == 'sent' for event in events))
        self.assertTrue(all(json.loads(event['snapshot']) == {'shenzhen': 0, 'zhuhai': 0, 'guangzhou': 0}
                            for event in events))
        for call in sender.call_args_list:
            card = call.args[1]['card']
            self.assertEqual(card['header']['subtitle']['content'], '定时票数汇总')
            total = next(item for item in card['body']['elements'] if item.get('text_size') == 'heading-0')
            self.assertEqual(total['content'], '**0**')
            columns = next(item for item in card['body']['elements'] if item['tag'] == 'column_set')['columns']
            self.assertEqual([column['elements'][0]['content'] for column in columns], ['深圳', '珠海', '广州'])
            self.assertEqual([column['elements'][1]['content'] for column in columns], ['**0**'] * 3)

    def test_summary_uses_current_counts_after_revotes_without_realtime_vote_events(self):
        self.enable()
        first, second = (self.store.visitor(None)[0] for _ in range(2))
        self.store.cast_vote(first, 'shenzhen', '192.0.2.1')
        self.store.cast_vote(first, 'shenzhen', '192.0.2.1')
        self.store.cast_vote(first, 'zhuhai', '192.0.2.1')
        self.store.cast_vote(second, 'guangzhou', '192.0.2.2')
        self.assertEqual(self.events(), [])
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))
        sender.assert_not_called()
        self.at('2026-10-26T00:00:00Z')
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(json.loads(self.events()[0]['snapshot']), {'shenzhen': 0, 'zhuhai': 1, 'guangzhou': 1})
        self.assertEqual(self.store.stats()['totalVotes'], 2)
        total = next(item for item in sender.call_args.args[1]['card']['body']['elements']
                     if item.get('text_size') == 'heading-0')
        self.assertEqual(total['content'], '**2**')
        serialized = json.dumps(sender.call_args.args[1], ensure_ascii=False)
        for private in (first, second, '192.0.2.1', '192.0.2.2'):
            self.assertNotIn(private, serialized)
        self.assertNotIn('+1', serialized)

    def test_beijing_midnight_does_not_repeat_the_previous_evening_slot(self):
        self.enable()
        sender = Mock()
        self.at('2026-10-26T14:00:00Z')  # Beijing 22:00.
        self.assertTrue(self.notifications.deliver_one(sender))
        for moment in ('2026-10-26T16:00:00Z', '2026-10-26T23:59:59Z'):
            self.at(moment)
            self.assertFalse(self.notifications.deliver_one(sender))
        self.at('2026-10-27T00:00:00Z')
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(self.cursor(), self.now)

    def test_reopen_before_due_poll_sends_once_and_reopen_afterward_does_not_repeat(self):
        self.enable()
        self.at('2026-10-26T00:00:02Z')
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        sender = Mock()
        self.assertTrue(reopened.notifications.deliver_one(sender))
        self.now += 2
        reopened_again = CampaignStore(self.path, clock=lambda: self.now)
        self.assertFalse(reopened_again.notifications.deliver_one(sender))
        sender.assert_called_once()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.cursor(), utc('2026-10-26T00:00:00Z'))

    def test_concurrent_workers_share_one_durable_scheduled_event(self):
        self.enable()
        workers = [CampaignStore(self.path, clock=lambda: self.now).notifications for _ in range(4)]
        self.at('2026-10-26T00:00:00Z')
        sender = Mock()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda worker: worker.deliver_one(sender), workers))
        self.assertEqual(results.count(True), 1)
        sender.assert_called_once()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]['status'], 'sent')

    def test_long_outage_only_sends_latest_missed_slot_with_current_counts(self):
        self.enable()
        self.at('2026-10-29T15:03:00Z')  # Beijing 23:03, several missed days.
        visitor = self.store.visitor(None)[0]
        self.store.cast_vote(visitor, 'guangzhou', '192.0.2.1')
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        sender = Mock()
        self.assertTrue(reopened.notifications.deliver_one(sender))
        self.now += 1
        self.assertFalse(reopened.notifications.deliver_one(sender))
        sender.assert_called_once()
        event = self.events()[0]
        self.assertEqual(event['created'], int(utc('2026-10-29T15:03:00Z')))
        self.assertEqual(json.loads(event['snapshot']), {'shenzhen': 0, 'zhuhai': 0, 'guangzhou': 1})
        self.assertEqual(self.cursor(), utc('2026-10-29T14:00:00Z'))
        self.at('2026-10-30T00:00:00Z')
        self.assertTrue(reopened.notifications.deliver_one(sender))
        self.assertEqual(len(self.events()), 2)

    def test_disabled_slots_advance_cursor_and_resume_only_at_a_future_slot(self):
        self.enable()
        self.notifications.save({'enabled': False})
        self.at('2026-10-26T05:00:00Z')
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.cursor(), self.now)
        self.at('2026-10-26T10:00:00Z')
        self.notifications.save({'enabled': True})
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.events(), [])
        sender.assert_not_called()
        self.at('2026-10-26T14:00:00Z')
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(len(self.events()), 1)

    def test_unconfigured_slots_are_skipped_and_configuring_does_not_backfill(self):
        self.at('2026-10-26T05:00:00Z')
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.cursor(), self.now)
        self.at('2026-10-26T10:00:00Z')
        self.enable()
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.events(), [])
        sender.assert_not_called()
        self.at('2026-10-26T14:00:00Z')
        self.assertTrue(self.notifications.deliver_one(sender))

    def test_changing_robot_skips_elapsed_slots_and_cancels_old_group_report(self):
        self.enable()
        self.at('2026-10-26T00:00:00Z')
        old_event, _ = self.notifications.claim()
        self.at('2026-10-26T00:00:31Z')
        self.notifications.claim()  # Recover the interrupted automatic report.
        self.at('2026-10-26T05:00:10Z')
        self.notifications.save({'enabled': True, 'webhook': URL + '-new'})
        sender = Mock()
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual(self.events()[0]['id'], old_event['id'])
        self.assertEqual(self.events()[0]['status'], 'cancelled')
        sender.assert_not_called()
        self.at('2026-10-26T10:00:00Z')
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(sender.call_args.args[0], URL + '-new')
        self.assertEqual([event['kind'] for event in self.events()], ['scheduled_summary', 'scheduled_summary'])

    def test_saving_unchanged_settings_does_not_skip_a_due_slot(self):
        self.enable()
        before = self.cursor()
        self.at('2026-10-26T00:00:02Z')
        self.notifications.save({'enabled': True, 'webhook': '', 'signingSecret': ''})
        self.assertEqual(self.cursor(), before)
        self.assertTrue(self.notifications.deliver_one(Mock()))
        self.assertEqual(len(self.events()), 1)

    def test_adding_schedule_to_configured_database_does_not_backfill_old_slots(self):
        self.enable()
        self.at('2026-10-26T00:10:00Z')
        with self.store.connect() as db:
            db.execute('DROP TABLE campaign_feishu_schedule')
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        sender = Mock()
        self.assertFalse(reopened.notifications.deliver_one(sender))
        sender.assert_not_called()
        self.assertEqual(self.events(), [])
        self.at('2026-10-26T05:00:00Z')
        self.assertTrue(reopened.notifications.deliver_one(sender))

    def test_manual_summary_does_not_consume_an_automatic_slot(self):
        self.enable()
        before = self.cursor()
        self.at('2026-10-26T00:00:00Z')
        self.notifications.queue_summary()
        self.assertEqual(self.cursor(), before)
        sender = Mock()
        self.assertTrue(self.notifications.deliver_one(sender))
        self.now += 1
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual([event['kind'] for event in self.events()], ['summary', 'scheduled_summary'])
        self.assertEqual([call.args[1]['card']['header']['subtitle']['content'] for call in sender.call_args_list],
                         ['当前票数汇总', '定时票数汇总'])

    def test_disabled_automatic_schedule_preserves_manual_summary_and_test_sending(self):
        self.notifications.save({'enabled': False, 'webhook': URL})
        self.at('2026-10-26T00:00:00Z')
        self.notifications.queue_summary()
        self.notifications.queue_test()
        sender = Mock()
        self.assertTrue(self.notifications.deliver_one(sender))
        self.now += 1
        self.assertTrue(self.notifications.deliver_one(sender))
        self.now += 1
        self.assertFalse(self.notifications.deliver_one(sender))
        self.assertEqual([event['kind'] for event in self.events()], ['summary', 'test'])
        self.assertFalse(self.notifications.settings()['enabled'])

    def test_scheduled_queue_and_cursor_roll_back_together(self):
        self.enable()
        before = self.cursor()
        self.at('2026-10-26T00:00:00Z')
        enqueue = self.notifications.enqueue

        def fail_after_insert(*args, **kwargs):
            enqueue(*args, **kwargs)
            raise RuntimeError('synthetic failure after summary insertion')

        with patch.object(self.notifications, 'enqueue', side_effect=fail_after_insert), self.assertRaises(RuntimeError):
            self.notifications.claim()
        self.assertEqual(self.events(), [])
        self.assertEqual(self.cursor(), before)
        self.assertTrue(self.notifications.deliver_one(Mock()))
        self.assertEqual(len(self.events()), 1)

    def test_scheduled_failure_retries_the_same_event_after_restart(self):
        self.enable()
        self.at('2026-10-26T00:00:00Z')
        sender = Mock(side_effect=[DeliveryFailure('合成汇总发送暂时失败'), None])
        self.assertTrue(self.notifications.deliver_one(sender))
        event_id = self.events()[0]['id']
        self.assertEqual(self.events()[0]['status'], 'retrying')
        self.now += 5
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        self.assertTrue(reopened.notifications.deliver_one(sender))
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]['id'], event_id)
        self.assertEqual(self.events()[0]['status'], 'sent')
        self.assertEqual(self.events()[0]['attempts'], 2)
        self.assertEqual(sender.call_args_list[0].args[1], sender.call_args_list[1].args[1])

    def test_comments_and_private_feedback_still_send_immediately_with_idempotent_retry(self):
        self.at('2026-10-26T00:10:00Z')  # Well before the next fixed summary.
        self.enable()
        visitor = self.store.visitor(None)[0]
        comment = {'requestId': str(uuid.uuid4()), 'content': '合成公开留言：希望参加训练。',
                   'nickname': '合成同学', 'city': 'shenzhen', 'publishConsent': True}
        feedback = {'requestId': str(uuid.uuid4()), 'content': '合成私密意见：希望增加活动。',
                    'category': 'activity', 'city': 'zhuhai'}
        first = self.store.comments.submit(visitor, '192.0.2.1', comment)
        repeated = self.store.comments.submit(visitor, '192.0.2.1', comment)
        self.assertFalse(first['duplicate'])
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(first['item']['id'], repeated['item']['id'])
        private = self.store.feedback.submit(visitor, '192.0.2.1', feedback)
        self.assertTrue(self.store.feedback.submit(visitor, '192.0.2.1', feedback)['duplicate'])
        self.assertEqual([event['kind'] for event in self.events()], ['comment', 'feedback'])
        sender = Mock(side_effect=[DeliveryFailure('合成网络暂时失败'), None, None])
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(self.events()[0]['status'], 'retrying')
        self.now += 1
        self.assertTrue(self.notifications.deliver_one(sender))
        self.assertEqual(self.events()[1]['status'], 'sent')
        self.assertEqual(self.store.comments.public_list()['total'], 1)
        self.assertEqual(self.store.feedback.list()['items'][0]['id'], private['id'])
        self.now += 4
        reopened = CampaignStore(self.path, clock=lambda: self.now)
        self.assertTrue(reopened.notifications.deliver_one(sender))
        self.assertEqual([event['status'] for event in self.events()], ['sent', 'sent'])
        self.assertEqual(sender.call_args_list[0].args[1], sender.call_args_list[2].args[1])
        self.assertEqual([call.args[1]['card']['header']['subtitle']['content'] for call in sender.call_args_list],
                         ['收到一条公开留言', '收到一条新想法', '收到一条公开留言'])


if __name__ == '__main__':
    unittest.main()
