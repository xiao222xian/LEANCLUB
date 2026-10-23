"""Member ownership and migration invariants in disposable databases only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
from campaign import CampaignStore
from campaign_events import MemberPrincipal
from member_orders_schema import LEGACY_COLUMNS, member_schema_ready
from migrate_member_orders import migrate
from store import Problem
from payment_fake import FakePaymentAdapter
from payment_lifecycle import PaymentWorker
from test_campaign_events import ORIGIN, event_body, registration_body, synthetic_paid


@contextmanager
def connection(path):
    with closing(sqlite3.connect(path)) as db:
        with db:
            yield db


class MemberOrdersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.path = Path(self.tmp.name) / 'member-orders.sqlite3'
        self.store = CampaignStore(self.path, clock=lambda: self.now)
        self.events = self.store.events
        self.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        self.event = self.events.save(event_body(self.now))['event']
        self.member = MemberPrincipal(1, 101)
        self.other = MemberPrincipal(1, 102)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_problem(self, status, method, *args):
        with self.assertRaises(Problem) as failure:
            method(*args)
        self.assertEqual(failure.exception.status, status)

    def register(self, data=None, member=None, event=None):
        return self.events.register_member(member or self.member, event or self.event['id'], data or registration_body(), ORIGIN)

    def test_new_member_order_has_explicit_owner_and_no_fake_visitor_or_bearer_read_link(self):
        result = self.register()
        self.assertEqual(result['accessMode'], 'member')
        self.assertIn('/ticket?order=', result['orderUrl'])
        self.assertIsNone(result['qrDataUrl'])
        self.assertIsNone(result['checkInUrl'])
        self.assertTrue(result['canCancel'])
        for field in ('orderToken', 'ticketToken', 'memberId', 'visitor'):
            self.assertNotIn(field, result)
        self.assertNotIn('13900000001', json.dumps(result))
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM campaign_registrations').fetchone()
            self.assertEqual((row['member_tenant_id'], row['member_user_id']), (1, 101))
            self.assertIsNone(row['visitor'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_visitors').fetchone()[0], 0)

    def test_payment_is_still_required_on_server_without_any_order_or_command_written(self):
        self.events.payment_adapter = None
        self.assert_problem(409, self.register)
        self.assertFalse(self.events.get(self.event['id'])['event']['paymentReady'])
        with self.store.connect() as db:
            for table in ('campaign_registrations', 'campaign_member_requests'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_cross_device_uses_signed_owner_not_contact_cookie_or_numeric_id_alone(self):
        result = self.register()
        identifier = result['order']['id']
        # A second service request has no visitor or browser state.
        again = self.events.member_order(MemberPrincipal(1, 101), identifier, ORIGIN)
        self.assertEqual(again['order'], result['order'])
        for principal in (self.other, MemberPrincipal(2, 101)):
            self.assert_problem(404, self.events.member_order, principal, identifier, ORIGIN)
            self.assert_problem(404, self.events.cancel_member, principal, identifier, str(uuid.uuid4()), ORIGIN)
            self.assertEqual(self.events.member_orders(principal, ORIGIN)['total'], 0)
        self.assert_problem(409, self.register, registration_body(), self.other)

    def test_no_untyped_principal_and_strict_registration_fields(self):
        self.assert_problem(403, self.events.member_order, {'tenant_id': 1, 'member_id': 101}, 'a' * 32, ORIGIN)
        for values in ((True, 1), (1, False), (0, 1), (1, 2**63)):
            self.assert_problem(403, MemberPrincipal, *values)
        for patch_values in ({'memberId': 101}, {'visitor': 'a' * 64}, {'paymentStatus': 'paid'},
                             {'expectedPriceFen': True}, {'expectedPriceFen': 9900.0}, {'consent': 1}):
            self.assert_problem(400, self.register, registration_body(**patch_values))

    def test_same_request_normalization_and_recovery_precede_event_changes(self):
        data = registration_body(name='  合成会员  ')
        first = self.register(data)
        self.events.save({'priceFen': 19900, 'status': 'closed'}, self.event['id'])
        replay = self.register(data | {'name': '合成会员'})
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['order'], first['order'])
        for change in ({'name': '另一个人'}, {'phone': '13900000002'}, {'expectedPriceFen': 19900}):
            self.assert_problem(409, self.register, data | change)
        second_event = self.events.save(event_body(self.now))['event']
        self.assert_problem(409, self.register, data, None, second_event['id'])

    def test_parallel_same_request_reserves_once_and_different_requests_do_not_duplicate_member(self):
        data = registration_body()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.register(data), range(8)))
        self.assertEqual(len({item['order']['id'] for item in results}), 1)
        self.assertEqual(sum(not item['duplicate'] for item in results), 1)
        self.assert_problem(409, self.register, registration_body(phone='13900000002'))
        self.assertEqual(self.events.member_orders(self.member, ORIGIN)['total'], 1)

    def test_parallel_last_seat_shared_by_anonymous_and_member_orders(self):
        self.events.save({'capacity': 1}, self.event['id'])
        visitor = self.store.visitor(None)[0]
        def run(index):
            try:
                body = registration_body(phone='139000000%02d' % (index + 1))
                if index == 0:
                    return self.events.register(visitor, self.event['id'], body, ORIGIN)
                return self.register(body, MemberPrincipal(1, 100 + index))
            except Problem as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(run, range(6)))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(results.count(409), 5)
        self.assertEqual(self.events.get(self.event['id'])['event']['remaining'], 0)

    def test_legacy_secret_cookie_and_short_ticket_cannot_control_member_order(self):
        receipt = self.register()
        token = self.events.token(receipt['order']['id'])
        visitor = self.store.visitor(None)[0]
        self.assert_problem(404, self.events.ticket, token, ORIGIN, visitor)
        self.assert_problem(404, self.events.cancel, token, ORIGIN, visitor)
        self.assertEqual(self.events.mine(visitor, ORIGIN)['tickets'], [])
        self.assertEqual(self.events.mine(None, ORIGIN)['tickets'], [])
        self.assert_problem(404, self.events.ticket, receipt['registration']['ticketNumber'], ORIGIN)
        self.assertFalse(self.events.preview(token, ORIGIN)['eligible'])

    def test_legacy_member_provenance_never_reinstates_visitor_control(self):
        visitor = self.store.visitor(None)[0]
        receipt = self.events.register(visitor, self.event['id'], registration_body(), ORIGIN)
        # Boundary fixture for a later reviewed claim flow, not an exposed claim API.
        with self.store.connect() as db:
            db.execute('UPDATE campaign_registrations SET member_tenant_id=1,member_user_id=101,owner_bound_at=? WHERE id=?',
                       (self.now, receipt['order']['id']))
        self.assertEqual(self.events.mine(visitor, ORIGIN)['tickets'], [])
        self.assert_problem(404, self.events.ticket, receipt['orderToken'], ORIGIN, visitor)
        self.assert_problem(404, self.events.cancel, receipt['orderToken'], ORIGIN, visitor)
        self.assert_problem(409, self.events.register, visitor, self.event['id'], registration_body(), ORIGIN)

    def test_legacy_order_keeps_original_secret_read_and_cookie_control(self):
        visitor = self.store.visitor(None)[0]
        receipt = self.events.register(visitor, self.event['id'], registration_body(), ORIGIN)
        self.assertFalse(self.events.ticket(receipt['orderToken'], ORIGIN)['canCancel'])
        self.assertEqual(self.events.mine(visitor, ORIGIN)['tickets'][0]['orderToken'], receipt['orderToken'])
        self.assert_problem(404, self.events.member_order, self.member, receipt['order']['id'], ORIGIN)
        self.assert_problem(409, self.register)
        self.assertEqual(self.events.cancel(receipt['orderToken'], ORIGIN, visitor)['order']['status'], 'closing')

    def test_member_cancel_is_idempotent_and_same_key_cannot_cancel_another_order(self):
        first = self.register()
        rid = str(uuid.uuid4())
        result = self.events.cancel_member(self.member, first['order']['id'], rid, ORIGIN)
        self.assertEqual(result['order']['status'], 'closing')
        self.assertFalse(result['duplicate'])
        self.assertTrue(self.events.cancel_member(self.member, first['order']['id'], rid, ORIGIN)['duplicate'])
        PaymentWorker(self.events.payments, self.events.payment_adapter).run_once()
        second = self.register()
        self.assert_problem(409, self.events.cancel_member, self.member, second['order']['id'], rid, ORIGIN)
        self.assertEqual(self.events.member_order(self.member, second['order']['id'], ORIGIN)['order']['status'], 'pending_payment')

    def test_paid_detail_uses_original_staff_checkin_and_refund_request_invalidates_qr(self):
        first = self.register()
        synthetic_paid(self.store, first['order']['id'])
        detail = self.events.member_order(self.member, first['order']['id'], ORIGIN)
        self.assertTrue(detail['qrDataUrl'].startswith('data:image/png;base64,'))
        self.assertIn('/check-in#ticket=', detail['checkInUrl'])
        self.assertNotIn('orderToken', detail)
        self.assertTrue(self.events.preview(detail['checkInUrl'], ORIGIN)['eligible'])
        cancelled = self.events.cancel_member(self.member, first['order']['id'], str(uuid.uuid4()), ORIGIN)
        self.assertEqual(cancelled['order']['status'], 'refund_pending')
        self.assertIsNone(cancelled['qrDataUrl'])
        self.assertIsNone(cancelled['checkInUrl'])
        self.assertEqual(cancelled['event']['refundPendingCount'], 1)
        self.assertFalse(self.events.preview(detail['checkInUrl'], ORIGIN)['eligible'])

    def test_checked_in_member_cannot_cancel_or_generate_a_fresh_qr(self):
        first = self.register()
        synthetic_paid(self.store, first['order']['id'])
        self.events.check_in(first['registration']['ticketNumber'], str(uuid.uuid4()), ORIGIN, 'isolated-test')
        detail = self.events.member_order(self.member, first['order']['id'], ORIGIN)
        self.assertFalse(detail['canCancel'])
        self.assertIsNone(detail['qrDataUrl'])
        self.assert_problem(409, self.events.cancel_member, self.member, first['order']['id'], str(uuid.uuid4()), ORIGIN)

    def test_member_list_is_paged_scoped_and_never_generates_qr(self):
        for index in range(4):
            event = self.events.save(event_body(self.now, title='隔离活动%d' % index))['event']
            self.register(event=event['id'])
        self.register(registration_body(phone='13900000002'), self.other)
        with patch('campaign_events.qrcode.make', side_effect=AssertionError('List must not generate QR')):
            first = self.events.member_orders(self.member, ORIGIN, 1, 2)
            second = self.events.member_orders(self.member, ORIGIN, 2, 2)
        self.assertEqual(first['total'], 4)
        self.assertEqual(len(first['list']), 2)
        self.assertTrue({x['order']['id'] for x in first['list']}.isdisjoint({x['order']['id'] for x in second['list']}))
        for item in first['list']:
            for field in ('qrDataUrl', 'checkInUrl', 'orderToken', 'ticketToken', 'ticketUrl'):
                self.assertNotIn(field, item)
        for page, size, status in ((True, 20, 'all'), (1, True, 'all'), (1, 21, 'all'), (0, 20, 'all'), (1, 20, True)):
            self.assert_problem(400, self.events.member_orders, self.member, ORIGIN, page, size, status)

    def test_request_scope_is_per_member_not_globally_client_chosen_uuid(self):
        data = registration_body()
        first = self.register(data)
        second = self.register(data | {'phone': '13900000002'}, self.other)
        self.assertNotEqual(first['order']['id'], second['order']['id'])


def legacy_fixture(store):
    """Recreate the previous table in an isolated fixture, preserving its rows."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        from payment_schema import TABLES
        for table in TABLES:
            db.execute('DROP TABLE ' + table)
        db.execute('DROP TABLE campaign_member_requests')
        db.execute('DROP TABLE leanclub_schema_migrations')
        indexes = [row[0] for row in db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='campaign_registrations' AND sql IS NOT NULL AND name NOT LIKE 'campaign_registration_member%'")]
        db.execute('''CREATE TABLE campaign_registrations_legacy (
            id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES campaign_events(id),
            visitor TEXT NOT NULL REFERENCES campaign_visitors(token_hash),
            request_id TEXT NOT NULL, ticket_number TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, phone TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('registered','checked_in','cancelled')),
            created INTEGER NOT NULL, checked_in INTEGER, cancelled INTEGER,
            checked_by TEXT, check_request_id TEXT, payment_status TEXT NOT NULL DEFAULT 'pending_payment',
            amount_fen INTEGER NOT NULL DEFAULT 0, payment_expires INTEGER NOT NULL DEFAULT 0,
            paid_at INTEGER,payment_reference TEXT,paid_by TEXT,refund_at INTEGER,refund_reference TEXT,
            UNIQUE(visitor,request_id))''')
        columns = ','.join(LEGACY_COLUMNS)
        db.execute('INSERT INTO campaign_registrations_legacy(' + columns + ') SELECT ' + columns + ' FROM campaign_registrations')
        db.execute('DROP TABLE campaign_registrations')
        db.execute('ALTER TABLE campaign_registrations_legacy RENAME TO campaign_registrations')
        for sql in indexes:
            db.execute(sql)


class MemberMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'legacy.sqlite3'
        self.backups = Path(self.tmp.name) / 'private-backups'
        self.now = 1792674000
        self.store = CampaignStore(self.path, clock=lambda: self.now)
        self.store.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        self.event = self.store.events.save(event_body(self.now))['event']
        self.visitor = self.store.visitor(None)[0]
        self.receipt = self.store.events.register(self.visitor, self.event['id'], registration_body(), ORIGIN)
        synthetic_paid(self.store, self.receipt['order']['id'])
        legacy_fixture(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def original_fields(self):
        with connection(self.path) as db:
            return list(db.execute('SELECT ' + ','.join(LEGACY_COLUMNS) + ' FROM campaign_registrations ORDER BY id'))

    def test_old_database_startup_does_not_rebuild_or_enable_member_orders(self):
        before = self.original_fields()
        restarted = CampaignStore(self.path, clock=lambda: self.now)
        self.assertFalse(restarted.events.member_orders_ready)
        with restarted.connect() as db:
            self.assertNotIn('member_user_id', {row[1] for row in db.execute('PRAGMA table_info(campaign_registrations)')})
        self.assertEqual(before, self.original_fields())
        self.assertEqual(restarted.events.ticket(self.receipt['orderToken'], ORIGIN)['order']['status'], 'paid')
        with self.assertRaises(Problem) as error:
            restarted.events.member_orders(MemberPrincipal(1, 101), ORIGIN)
        self.assertEqual(error.exception.status, 503)

    def test_dry_run_and_missing_stop_confirmation_do_not_change_database(self):
        before = self.original_fields()
        self.assertEqual(migrate(self.path, self.backups)['state'], 'migration-required')
        with self.assertRaises(ValueError):
            migrate(self.path, self.backups, apply=True)
        self.assertFalse(self.backups.exists())
        self.assertEqual(before, self.original_fields())

    def test_backup_migration_preserves_every_legacy_field_and_secret_then_is_noop(self):
        before = self.original_fields()
        token = self.receipt['orderToken']
        result = migrate(self.path, self.backups, apply=True, confirmed_stopped=True)
        self.assertTrue(result['changed'])
        self.assertEqual(before, self.original_fields())
        with connection(result['backup']) as backup:
            self.assertNotIn('member_user_id', {row[1] for row in backup.execute('PRAGMA table_info(campaign_registrations)')})
            self.assertEqual(list(backup.execute('SELECT ' + ','.join(LEGACY_COLUMNS) + ' FROM campaign_registrations ORDER BY id')), before)
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        restarted = CampaignStore(self.path, clock=lambda: self.now)
        self.assertTrue(restarted.events.member_orders_ready)
        self.assertEqual(restarted.events.token(self.receipt['order']['id']), token)
        self.assertEqual(restarted.events.ticket(token, ORIGIN, self.visitor)['order']['status'], 'paid')
        repeated = migrate(self.path, self.backups, apply=True, confirmed_stopped=True)
        self.assertFalse(repeated['changed'])
        self.assertEqual(len(list(self.backups.glob('*.sqlite3'))), 1)
        with restarted.connect() as db:
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])
        from migrate_payments import migrate as migrate_payment_schema
        migrate_payment_schema(self.path, self.backups, apply=True, confirmed_stopped=True)
        restarted = CampaignStore(self.path, clock=lambda: self.now)
        restarted.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        member = restarted.events.register_member(MemberPrincipal(1, 101), self.event['id'], registration_body(phone='13900000002'), ORIGIN)
        self.assertEqual(member['accessMode'], 'member')

    def test_failure_after_table_swap_rolls_back_and_retains_private_backup(self):
        before = self.original_fields()
        with patch('migrate_member_orders.create_member_metadata', side_effect=RuntimeError('isolated failure')):
            with self.assertRaises(RuntimeError):
                migrate(self.path, self.backups, apply=True, confirmed_stopped=True)
        self.assertEqual(before, self.original_fields())
        with connection(self.path) as db:
            self.assertFalse(member_schema_ready(db))
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_registrations_member_v1'").fetchone())
        self.assertEqual(len(list(self.backups.glob('*.sqlite3'))), 1)

    def test_unknown_schema_or_dependent_foreign_key_requires_explicit_review(self):
        with connection(self.path) as db:
            db.execute('CREATE TABLE future_detail(id TEXT REFERENCES campaign_registrations(id))')
        with self.assertRaises(ValueError):
            migrate(self.path, self.backups, apply=True, confirmed_stopped=True)
        self.assertFalse(self.backups.exists())
        with connection(self.path) as db:
            db.execute('DROP TABLE future_detail')
            db.execute('ALTER TABLE campaign_registrations ADD COLUMN future_field TEXT')
        with self.assertRaises(ValueError):
            migrate(self.path, self.backups, apply=True, confirmed_stopped=True)


if __name__ == '__main__':
    unittest.main()
