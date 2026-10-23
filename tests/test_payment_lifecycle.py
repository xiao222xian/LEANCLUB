"""Provider-independent lifecycle tests. All funds and databases are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
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
from payment_lifecycle import (PaymentBinding, PaymentLifecycle, PaymentObservation, PaymentScope,
                               PaymentWorker, RefundBinding, RefundObservation, binding_from)
from payment_schema import TABLES, VERSION, payment_schema_ready
from payment_fake import FakePaymentAdapter
from migrate_payments import migrate
from store import Problem
from test_campaign_events import ORIGIN, event_body, registration_body


class PaymentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.store = CampaignStore(Path(self.tmp.name) / 'payments.sqlite3', clock=lambda: self.now)
        self.events = self.store.events
        self.adapter = FakePaymentAdapter(lambda: self.now)
        self.events.payment_adapter = self.adapter
        self.domain = self.events.payments
        self.worker = PaymentWorker(self.domain, self.adapter, 20)
        self.member = MemberPrincipal(1, 101)
        self.event = self.events.save(event_body(self.now, capacity=1))['event']

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, member=None, phone='13900000001', event=None, data=None):
        return self.events.register_member(member or self.member, event or self.event['id'],
            data or registration_body(phone=phone), ORIGIN)

    def binding(self, order):
        with self.store.connect() as db:
            return binding_from(db.execute('SELECT * FROM campaign_payment_bindings WHERE order_id=?', (order,)).fetchone())

    def row(self, order):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM campaign_registrations WHERE id=?', (order,)).fetchone())

    def details(self, order):
        return self.events.member_order(self.member, order, ORIGIN)

    def scalar(self, query, args=()):
        with self.store.connect() as db:
            return db.execute(query, args).fetchone()[0]

    def cancel(self, order):
        return self.events.cancel_member(self.member, order, str(uuid.uuid4()), ORIGIN)

    def create_at_provider(self):
        order = self.register()['order']['id']
        self.worker.run_once()
        return order, self.binding(order)

    def pay(self):
        order, binding = self.create_at_provider()
        self.domain.accept(self.adapter.paid(binding))
        return order, binding

    def advance(self, seconds=61):
        self.now += seconds

    def assert_problem(self, code, fn, *args, **kwargs):
        with self.assertRaises(Problem) as error:
            fn(*args, **kwargs)
        self.assertEqual(error.exception.status, code)

    def test_atomic_binding_and_outbox_exactly_once_for_registration_replay(self):
        data = registration_body()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.register(data=data), range(6)))
        self.assertEqual(len({r['order']['id'] for r in results}), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_bindings'), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_outbox'), 1)
        binding = self.binding(results[0]['order']['id'])
        self.assertEqual(binding.merchant_order_id, binding.order_id)
        self.assertEqual(binding.attempt_id, binding.out_trade_no)
        self.assertEqual(binding.amount_fen, 9900)

    def test_adapter_ready_boolean_alone_cannot_enable_payments(self):
        from types import SimpleNamespace
        self.events.payment_adapter = SimpleNamespace(ready=True)
        self.assertFalse(self.events.get(self.event['id'])['event']['paymentReady'])
        self.assert_problem(409, self.register)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_registrations'), 0)

    def test_expired_read_does_not_release_final_seat_and_timeout_closes_in_worker(self):
        order, binding = self.create_at_provider()
        self.advance(1800)
        detail = self.details(order)
        self.assertEqual(detail['order']['status'], 'pending_payment')
        self.assertEqual(detail['event']['remaining'], 0)
        self.assert_problem(409, self.register, MemberPrincipal(1, 102), '13900000002')
        self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'expired')
        self.assertEqual(self.details(order)['event']['remaining'], 1)
        self.assertEqual([call[0] for call in self.adapter.calls], ['create', 'query', 'close'])

    def test_delayed_paid_before_timeout_is_fulfilled_without_overselling(self):
        order, binding = self.create_at_provider()
        self.adapter.paid(binding)
        self.advance(1800)
        self.assert_problem(409, self.register, MemberPrincipal(1, 102), '13900000002')
        self.worker.run_once()
        detail = self.details(order)
        self.assertEqual(detail['order']['status'], 'paid')
        self.assertTrue(detail['qrDataUrl'])
        self.assertEqual(detail['event']['remaining'], 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_registrations'), 1)

    def test_paid_observed_after_deadline_with_reserved_seat_still_fulfilled(self):
        order, binding = self.create_at_provider()
        self.advance(1801)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.events.expire(db)
        self.assertEqual(self.row(order)['payment_status'], 'closing')
        self.domain.accept(self.adapter.paid(binding))
        self.assertEqual(self.row(order)['payment_status'], 'paid')

    def test_cancel_intent_wins_over_delayed_paid_and_keeps_seat_for_refund(self):
        order, binding = self.create_at_provider()
        self.cancel(order)
        self.domain.accept(self.adapter.paid(binding))
        detail = self.details(order)
        self.assertEqual(detail['order']['status'], 'refund_pending')
        self.assertEqual(detail['event']['remaining'], 0)
        self.assertFalse(detail['canCancel'])
        self.assertIsNone(detail['qrDataUrl'])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_refunds'), 1)

    def test_cancel_and_paid_concurrent_always_end_refund_pending(self):
        order, binding = self.create_at_provider()
        observation = self.adapter.paid(binding)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.cancel, order), pool.submit(self.domain.accept, observation)]
            for future in futures:
                future.result()
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.assertFalse(self.events.preview(self.events.token(order), ORIGIN)['eligible'])

    def test_event_cancellation_wins_over_existing_timeout_and_late_paid(self):
        order, binding = self.create_at_provider()
        self.advance(1800)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.events.expire(db)
        self.events.save({'status': 'cancelled'}, self.event['id'])
        self.domain.accept(self.adapter.paid(binding))
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.assertEqual(self.scalar('SELECT close_reason FROM campaign_payment_bindings'), 'event_cancel')

    def test_unknown_or_provider_not_found_does_not_release_or_create_a_new_attempt(self):
        order, binding = self.create_at_provider()
        self.adapter.payments.clear()  # provider query cannot locate it: unknown, not closed
        self.cancel(order)
        for _ in range(3):
            self.advance()
            self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'closing')
        self.assertEqual(self.details(order)['event']['remaining'], 0)
        self.assertEqual(sum(c[0] == 'create' for c in self.adapter.calls), 1)
        self.assertEqual(self.binding(order), binding)

    def test_closure_requires_terminal_fenced_and_all_attempts_evidence(self):
        order, binding = self.create_at_provider()
        self.cancel(order)
        for changes in ({}, {'existing_terminal': True}, {'existing_terminal': True, 'creation_fenced': True},
                        {'existing_terminal': True, 'creation_fenced': True, 'all_attempts_closed': 1}):
            with self.assertRaises(ValueError):
                self.domain.accept(self.adapter.observe(binding, 'confirmed_closed', **changes))
        self.assertEqual(self.row(order)['payment_status'], 'closing')
        self.assertEqual(self.details(order)['event']['remaining'], 0)

    def test_only_atomic_never_started_local_fence_can_release_without_provider(self):
        order = self.register()['order']['id']
        self.cancel(order)
        self.assertEqual(self.details(order)['event']['closingCount'], 1)
        self.assertFalse(self.details(order)['canCancel'])
        self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'cancelled')
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.scalar('SELECT create_state FROM campaign_payment_bindings'), 'fenced')
        self.assertFalse(self.worker.run_once())

    def test_legacy_unbound_pending_stays_closing_without_inventing_never_sent_proof(self):
        order = self.register()['order']['id']
        with self.store.connect() as db:
            db.execute('DELETE FROM campaign_payment_outbox')
            db.execute('DELETE FROM campaign_payment_bindings')
        self.cancel(order)
        self.assertEqual(self.row(order)['payment_status'], 'closing')
        self.assertEqual(self.scalar('SELECT code FROM campaign_payment_anomalies'), 'legacy_unbound_closing')
        self.assertFalse(self.worker.run_once())

    def test_scope_amount_currency_and_attempt_mismatch_rejected_before_accounting(self):
        order, binding = self.create_at_provider()
        for changes in ({'amount_fen': 1}, {'amount_fen': True}, {'currency': 'USD'}, {'app_id': 'other'},
                        {'merchant_id': 'other'}, {'provider': 'other'}, {'out_trade_no': 'other'},
                        {'attempt_id': 'other'}, {'merchant_order_id': 'other'}):
            with self.assertRaises(ValueError):
                self.domain.accept(self.adapter.paid(replace(binding, **changes)))
        self.assertEqual(self.row(order)['payment_status'], 'pending_payment')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_transactions'), 0)

    def test_missing_verified_source_or_paid_transaction_evidence_rejected(self):
        order, binding = self.create_at_provider()
        valid = self.adapter.paid(binding)
        for changes in ({'source': 'local_pay_order'}, {'paid_at': True}, {'transaction_id': ''}, {'observed_at': True},
                        {'observed_at': self.now + 61}, {'event_key': ''}):
            with self.assertRaises(ValueError):
                self.domain.accept(replace(valid, **changes))
        self.assertEqual(self.row(order)['payment_status'], 'pending_payment')

    def test_payment_transaction_cannot_belong_to_two_orders(self):
        self.events.save({'capacity': 2}, self.event['id'])
        first, binding = self.create_at_provider()
        second = self.register(MemberPrincipal(1, 102), '13900000002')['order']['id']
        self.domain.accept(self.adapter.paid(binding, 'same-provider-transaction'))
        self.domain.accept(self.adapter.paid(self.binding(second), 'same-provider-transaction'))
        self.assertEqual(self.row(first)['payment_status'], 'paid')
        self.assertEqual(self.row(second)['payment_status'], 'pending_payment')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_transactions'), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM campaign_payment_anomalies WHERE code='transaction_ownership_conflict'"), 1)

    def test_duplicate_or_reordered_paid_never_changes_paid_time_or_cancel_intent(self):
        order, binding = self.create_at_provider()
        paid = self.adapter.paid(binding)
        self.assertTrue(self.domain.accept(paid))
        self.assertFalse(self.domain.accept(paid))
        self.advance(5)
        self.domain.accept(replace(paid, event_key=uuid.uuid4().hex, paid_at=self.now, observed_at=self.now))
        self.assertEqual(self.row(order)['paid_at'], paid.paid_at)
        self.cancel(order)
        self.domain.accept(replace(paid, event_key=uuid.uuid4().hex))
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_refunds'), 1)

    def test_same_event_identity_cannot_change_payload(self):
        order, binding = self.create_at_provider()
        paid = self.adapter.paid(binding)
        self.domain.accept(paid)
        with self.assertRaises(ValueError):
            self.domain.accept(replace(paid, paid_at=paid.paid_at - 1))
        self.assertEqual(self.row(order)['paid_at'], paid.paid_at)

    def test_closed_observation_after_paid_does_not_release_paid_ticket(self):
        order, binding = self.pay()
        self.domain.accept(self.adapter.observe(binding, 'confirmed_closed', existing_terminal=True,
                                               all_attempts_closed=True, creation_fenced=True))
        self.assertEqual(self.row(order)['payment_status'], 'paid')
        self.assertEqual(self.details(order)['event']['remaining'], 0)

    def test_paid_after_confirmed_closed_never_reactivates_cancelled_ticket(self):
        order, binding = self.create_at_provider()
        self.cancel(order)
        self.advance()
        self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'cancelled')
        replacement = self.register(MemberPrincipal(1, 102), '13900000002')['order']['id']
        self.domain.accept(self.adapter.paid(binding))
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.assertEqual(self.row(order)['status'], 'cancelled')
        self.assertEqual(self.row(replacement)['payment_status'], 'pending_payment')
        self.assertIsNone(self.details(order)['qrDataUrl'])
        self.assertEqual(self.details(order)['event']['refundPendingCount'], 1)
        self.assertTrue(self.details(order)['event']['capacityConflict'])
        self.assertEqual(self.details(order)['event']['overbookedCount'], 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM campaign_payment_anomalies WHERE code='capacity_conflict_requires_reconciliation'"), 2)

    def test_payment_after_event_end_requires_refund_not_ticket(self):
        order, binding = self.create_at_provider()
        self.advance(7200)
        self.domain.accept(self.adapter.paid(binding))
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.assertIsNone(self.details(order)['qrDataUrl'])

    def test_capacity_cannot_shrink_below_closing_and_refund_pending(self):
        self.events.save({'capacity': 3}, self.event['id'])
        first, binding = self.create_at_provider()
        second = self.register(MemberPrincipal(1, 102), '13900000002')['order']['id']
        self.domain.accept(self.adapter.paid(binding))
        self.cancel(first)
        self.events.cancel_member(MemberPrincipal(1, 102), second, str(uuid.uuid4()), ORIGIN)
        self.assert_problem(409, self.events.save, {'capacity': 1}, self.event['id'])
        event = self.events.get(self.event['id'])['event']
        self.assertEqual((event['closingCount'], event['refundPendingCount'], event['remaining']), (1, 1, 1))

    def test_member_admin_filters_and_checkin_agree_on_closing(self):
        order, _ = self.create_at_provider()
        self.cancel(order)
        self.assertEqual(self.events.member_orders(self.member, ORIGIN, status='closing')['total'], 1)
        self.assertEqual(self.events.member_orders(self.member, ORIGIN, status='expired')['total'], 0)
        self.assertEqual(self.events.registrations(self.event['id'], 'closing')['total'], 1)
        self.assert_problem(409, self.events.check_in, self.events.token(order), str(uuid.uuid4()), ORIGIN, 'test-staff')

    def test_refund_success_only_releases_and_duplicate_does_not_change_time(self):
        order, binding = self.pay()
        self.cancel(order)
        # Payment job may be acknowledged as obsolete before refund task.
        for _ in range(3):
            self.worker.run_once()
        refund = next(call[1] for call in self.adapter.calls if call[0] == 'refund-create')
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        succeeded = self.adapter.refunded(refund)
        self.domain.accept(succeeded)
        self.advance()
        self.domain.accept(replace(succeeded, event_key=uuid.uuid4().hex, observed_at=self.now))
        self.assertEqual(self.row(order)['payment_status'], 'refunded')
        self.assertEqual(self.row(order)['status'], 'cancelled')
        self.assertEqual(self.row(order)['refund_at'], succeeded.observed_at)
        self.assertEqual(self.details(order)['event']['remaining'], 1)
        self.assertIsNone(self.details(order)['qrDataUrl'])

    def test_failed_unknown_refund_keeps_seat_stable_refund_id_and_no_second_create(self):
        order, _ = self.pay()
        self.cancel(order)
        for _ in range(3):
            self.worker.run_once()
        refund = next(call[1] for call in self.adapter.calls if call[0] == 'refund-create')
        for state in ('failed', 'unknown', 'pending'):
            self.adapter.refunds[refund.refund_id] = RefundObservation(refund, uuid.uuid4().hex, state, self.now, 'provider_query')
            self.advance()
            self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.assertEqual(self.details(order)['event']['remaining'], 0)
        self.assertEqual(sum(call[0] == 'refund-create' for call in self.adapter.calls), 1)
        self.assertEqual(self.scalar('SELECT refund_id FROM campaign_payment_refunds'), refund.refund_id)

    def test_refund_wrong_binding_amount_or_transaction_is_rejected(self):
        order, _ = self.pay()
        self.cancel(order)
        for _ in range(3):
            self.worker.run_once()
        refund = next(call[1] for call in self.adapter.calls if call[0] == 'refund-create')
        for wrong in (replace(refund, refund_id='other'), replace(refund, transaction_id='other'),
                      replace(refund, payment=replace(refund.payment, amount_fen=1))):
            with self.assertRaises(ValueError):
                self.domain.accept(self.adapter.refunded(wrong))
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')

    def test_create_success_before_crash_recovers_by_query_same_attempt(self):
        order = self.register()['order']['id']
        work = self.worker.claim()
        self.worker.execute(work)  # provider succeeds; no local result commit
        self.advance(21)
        restarted = PaymentWorker(PaymentLifecycle(self.store), self.adapter)
        restarted.run_once()
        self.assertEqual([c[0] for c in self.adapter.calls], ['create', 'query'])
        self.assertEqual(self.binding(order), work['binding'])

    def test_crash_after_claim_before_network_cannot_infer_never_attempted(self):
        order = self.register()['order']['id']
        self.worker.claim()  # durable started; whether network ran is unknowable
        self.cancel(order)
        self.advance(21)
        self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'closing')
        self.assertEqual([c[0] for c in self.adapter.calls], ['query'])

    def test_late_worker_does_not_ack_new_lease_and_obeys_current_cancellation(self):
        order = self.register()['order']['id']
        old = self.worker.claim()
        paid = self.adapter.paid(old['binding'])
        self.cancel(order)
        self.advance(21)
        new = self.worker.claim()
        self.assertFalse(self.worker.finish(old, paid))
        self.assertEqual(self.scalar('SELECT lease_token FROM campaign_payment_outbox WHERE kind=?', ('payment',)), new['lease'])
        self.assertEqual(self.row(order)['payment_status'], 'refund_pending')
        self.worker.finish(new, paid)
        self.assertEqual(self.scalar("SELECT state FROM campaign_payment_outbox WHERE kind='payment'"), 'done')

    def test_network_runs_outside_sqlite_write_lock(self):
        order = self.register()['order']['id']
        def hook(method, binding):
            with closing(sqlite3.connect(self.store.path, timeout=0.1)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("INSERT INTO campaign_settings(key,value) VALUES('test_network_unlocked','1')")
                db.commit()
        self.adapter.hook = hook
        self.worker.run_once()
        self.assertEqual(self.scalar("SELECT value FROM campaign_settings WHERE key='test_network_unlocked'"), '1')

    def test_adapter_exception_is_private_and_reconciles_instead_of_recreating(self):
        order = self.register()['order']['id']
        self.adapter.hook = lambda *args: (_ for _ in ()).throw(RuntimeError('sensitive-provider-response'))
        self.worker.run_once()
        self.adapter.hook = None
        self.advance()
        self.worker.run_once()
        self.assertEqual([c[0] for c in self.adapter.calls], ['create', 'query'])
        self.assertEqual(self.row(order)['payment_status'], 'pending_payment')
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM campaign_payment_anomalies WHERE code LIKE '%sensitive%'"), 0)

    def test_worker_rejects_adapter_scope_switch(self):
        self.register()
        self.adapter.scope = PaymentScope('test-provider', 'different-merchant', 'isolated-app')
        with self.assertRaises(ValueError):
            self.worker.run_once()
        self.assertEqual(self.adapter.calls, [])

    def test_refund_create_crash_recovers_by_query_original_refund(self):
        order, _ = self.pay()
        self.cancel(order)
        work = self.worker.claim()
        if work is None or work.get('localFence'):
            work = self.worker.claim()
        self.assertEqual(work['kind'], 'refund')
        pending = self.worker.execute(work)
        succeeded = self.adapter.refunded(pending.binding)
        self.advance(21)
        for _ in range(3):
            self.worker.run_once()
        self.assertEqual(self.row(order)['payment_status'], 'refunded')
        self.assertEqual(sum(c[0] == 'refund-create' for c in self.adapter.calls), 1)
        self.assertEqual(self.scalar('SELECT refund_id FROM campaign_payment_refunds'), succeeded.binding.refund_id)

    def test_worker_cannot_consume_observation_from_another_valid_binding(self):
        self.events.save({'capacity': 2}, self.event['id'])
        first = self.register()['order']['id']
        second = self.register(MemberPrincipal(1, 102), '13900000002')['order']['id']
        work = self.worker.claim()
        other = second if work['order_id'] == first else first
        with self.assertRaises(ValueError):
            self.worker.finish(work, self.adapter.paid(self.binding(other)))
        self.assertEqual(self.row(first)['payment_status'], 'pending_payment')
        self.assertEqual(self.row(second)['payment_status'], 'pending_payment')

    def test_misrouted_query_cannot_trigger_close_side_effect(self):
        self.events.save({'capacity': 2}, self.event['id'])
        first, _ = self.create_at_provider()
        second = self.register(MemberPrincipal(1, 102), '13900000002')['order']['id']
        self.cancel(first)
        self.advance()
        with self.store.connect() as db:
            db.execute('UPDATE campaign_payment_outbox SET available_at=? WHERE order_id=?', (self.now + 1000, second))
        with patch.object(self.adapter, 'query_payment', return_value=self.adapter.observe(self.binding(second))):
            self.worker.run_once()
        self.assertEqual(self.row(first)['payment_status'], 'closing')
        self.assertEqual(self.row(second)['payment_status'], 'pending_payment')
        self.assertEqual(sum(call[0] == 'close' for call in self.adapter.calls), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM campaign_payment_inbox'), 1)

    def test_refund_reference_cannot_release_two_orders(self):
        self.events.save({'capacity': 2}, self.event['id'])
        first, _ = self.pay()
        second_member = MemberPrincipal(1, 102)
        second = self.register(second_member, '13900000002')['order']['id']
        self.domain.accept(self.adapter.paid(self.binding(second)))
        self.cancel(first)
        self.events.cancel_member(second_member, second, str(uuid.uuid4()), ORIGIN)
        with self.store.connect() as db:
            refunds = {row['order_id']: RefundBinding(self.binding(row['order_id']), row['refund_id'], row['transaction_id'])
                       for row in db.execute('SELECT * FROM campaign_payment_refunds')}
        self.domain.accept(self.adapter.refunded(refunds[first], provider_refund_id='one-provider-refund'))
        self.domain.accept(self.adapter.refunded(refunds[second], provider_refund_id='one-provider-refund'))
        self.assertEqual(self.row(first)['payment_status'], 'refunded')
        self.assertEqual(self.row(second)['payment_status'], 'refund_pending')
        self.assertEqual(self.events.get(self.event['id'])['event']['remaining'], 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM campaign_payment_anomalies WHERE code='refund_ownership_conflict'"), 1)


class PaymentMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'before-payments.sqlite3'
        self.backups = Path(self.tmp.name) / 'backups'
        self.store = CampaignStore(self.path, clock=lambda: 1792674000)
        self.store.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        self.member = MemberPrincipal(1, 101)
        event = self.store.events.save(event_body(1792674000))['event']
        self.order = self.store.events.register_member(self.member, event['id'], registration_body(), ORIGIN)['order']['id']
        self.token = self.store.events.token(self.order)
        with self.store.connect() as db:
            self.before = [tuple(row) for row in db.execute('SELECT * FROM campaign_registrations')]
            for table in TABLES:
                db.execute('DROP TABLE ' + table)
            db.execute('DELETE FROM leanclub_schema_migrations WHERE version=?', (VERSION,))

    def tearDown(self):
        self.tmp.cleanup()

    def test_startup_and_dry_run_never_migrate_existing_database(self):
        restarted = CampaignStore(self.path, clock=self.store.clock)
        self.assertFalse(restarted.events.payments.ready)
        self.assertEqual(migrate(self.path, self.backups)['state'], 'migration-required')
        self.assertFalse(self.backups.exists())
        with self.assertRaises(ValueError):
            migrate(self.path, self.backups, apply=True)
        with restarted.connect() as db:
            self.assertEqual(self.before, [tuple(row) for row in db.execute('SELECT * FROM campaign_registrations')])

    def test_backup_additive_migration_preserves_orders_token_and_repeats_noop(self):
        result = migrate(self.path, self.backups, apply=True, confirmed_stopped=True)
        self.assertTrue(result['changed'])
        with closing(sqlite3.connect(result['backup'])) as db:
            self.assertFalse(payment_schema_ready(db))
            self.assertEqual(self.before, list(db.execute('SELECT * FROM campaign_registrations')))
        restarted = CampaignStore(self.path, clock=self.store.clock)
        self.assertTrue(restarted.events.payments.ready)
        self.assertIsNone(restarted.events.payment_adapter)
        self.assertEqual(restarted.events.token(self.order), self.token)
        with restarted.connect() as db:
            self.assertEqual(self.before, [tuple(row) for row in db.execute('SELECT * FROM campaign_registrations')])
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])
        self.assertFalse(migrate(self.path, self.backups, True, True)['changed'])
        self.assertEqual(len(list(self.backups.glob('*.sqlite3'))), 1)

    def test_failure_mid_schema_transaction_rolls_back_and_retains_backup(self):
        from payment_schema import create_payment_schema
        def fail(db, now):
            create_payment_schema(db, now)
            raise RuntimeError('isolated-rollback')
        with patch('migrate_payments.create_payment_schema', side_effect=fail):
            with self.assertRaises(RuntimeError):
                migrate(self.path, self.backups, True, True)
        with self.store.connect() as db:
            self.assertFalse(payment_schema_ready(db))
            self.assertFalse(set(TABLES) & {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual(self.before, [tuple(row) for row in db.execute('SELECT * FROM campaign_registrations')])
        self.assertEqual(len(list(self.backups.glob('*.sqlite3'))), 1)

    def test_partial_schema_refuses_automatic_completion(self):
        with self.store.connect() as db:
            db.execute('CREATE TABLE campaign_payment_outbox (id TEXT)')
        with self.assertRaises(ValueError):
            migrate(self.path, self.backups, True, True)
        self.assertFalse(self.backups.exists())


if __name__ == '__main__':
    unittest.main()
