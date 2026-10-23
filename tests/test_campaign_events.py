"""Isolated paid-order invariants; synthetic receipts never reach payment services."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore
from campaign_events import iso
from store import Problem
from payment_fake import FakePaymentAdapter
from payment_lifecycle import PaymentWorker
import test_campaign as http_support

ORIGIN = 'http://127.0.0.1:4180'


def event_body(now, **changes):
    return dict(title='隔离测试活动', subtitle='仅自动化测试', description='不对公众开放的测试数据',
                city='shenzhen', venue='合成场地', address='合成测试地址', startsAt=iso(now + 3600),
                endsAt=iso(now + 7200), registrationClosesAt=iso(now + 3000),
                capacity=5, posterCity='shenzhen', priceFen=9900, status='published') | changes


def registration_body(**changes):
    return dict(requestId=str(uuid.uuid4()), name='测试用户', phone='13900000001', consent=True, expectedPriceFen=9900) | changes


def synthetic_paid(store, registration_id):
    """Only for isolated test databases; not a runtime payment adapter or endpoint."""
    path = Path(store.path).resolve()
    if 'events-qa' not in str(path) and 'tmp' not in str(path).lower() and 'temp' not in str(path).lower():
        raise ValueError('Synthetic payment fixtures require an isolated temporary database.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE campaign_registrations SET payment_status='paid',paid_at=?,payment_reference=?,paid_by='isolated-test' WHERE id=? AND payment_status='pending_payment'",
                   (int(store.clock()), 'synthetic-' + uuid.uuid4().hex, registration_id))


class EventsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.store = CampaignStore(Path(self.tmp.name) / 'events.sqlite3', clock=lambda: self.now)
        self.events = self.store.events
        self.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        self.visitor = self.store.visitor(None)[0]
        self.event = self.events.save(event_body(self.now))['event']

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, body=None, visitor=None, event=None):
        return self.events.register(visitor or self.visitor, event or self.event['id'], body or registration_body(), ORIGIN)

    def assert_problem(self, status, method, *args):
        with self.assertRaises(Problem) as error:
            method(*args)
        self.assertEqual(error.exception.status, status)

    def paid(self):
        receipt = self.register()
        synthetic_paid(self.store, receipt['registration']['id'])
        return self.events.ticket(receipt['orderToken'], ORIGIN, self.visitor)

    def test_pending_order_has_no_ticket_or_qr_and_price_snapshot(self):
        ticket = self.register()
        self.assertEqual(ticket['order']['status'], 'pending_payment')
        self.assertEqual(ticket['order']['amountFen'], 9900)
        self.assertIsNone(ticket['ticketToken'])
        self.assertIsNone(ticket['qrDataUrl'])
        self.assertIsNone(ticket['checkInUrl'])
        self.assertEqual(ticket['event']['registeredCount'], 0)
        self.assertEqual(ticket['event']['pendingCount'], 1)
        self.assertEqual(ticket['event']['remaining'], 4)
        self.events.save({'priceFen': 19900}, self.event['id'])
        changed = self.events.ticket(ticket['orderToken'], ORIGIN)
        self.assertEqual(changed['order']['amountFen'], 9900)
        self.assertEqual(changed['event']['priceFen'], 19900)

    def test_changed_price_rejected_before_new_order_but_original_request_recovers(self):
        body = registration_body()
        first = self.register(body)
        self.events.save({'priceFen': 19900}, self.event['id'])
        self.assertEqual(self.register(body)['order'], first['order'])
        self.assert_problem(409, self.register, registration_body(phone='13900000002'))
        self.assertEqual(self.events.registrations(self.event['id'])['total'], 1)
        second = self.register(registration_body(phone='13900000002', expectedPriceFen=19900))
        self.assertEqual(second['order']['amountFen'], 19900)
        self.assert_problem(400, self.register, registration_body(phone='13900000003', expectedPriceFen=19900, paid=True))
        for invalid in (None, True, 0, 99.0, '9900'):
            self.assert_problem(400, self.register, registration_body(expectedPriceFen=invalid))

    def test_payment_not_configured_blocks_order_and_public_flag(self):
        self.events.payment_adapter = None
        self.assertFalse(self.events.get(self.event['id'])['event']['paymentReady'])
        self.assertFalse(self.events.get(self.event['id'])['event']['registrationOpen'])
        self.assert_problem(409, self.register)
        self.assertEqual(self.events.registrations(self.event['id'])['total'], 0)

    def test_uuid_idempotency_changed_payload_and_cross_event_rejected(self):
        data = registration_body()
        ticket = self.register(data)
        self.assertEqual(self.register(data), ticket | {'duplicate': True})
        self.assert_problem(409, self.register, data | {'name': '另一个人'})
        other = self.events.save(event_body(self.now))['event']['id']
        self.assert_problem(409, self.register, data, None, other)
        self.assertEqual(self.events.registrations(other)['total'], 0)

    def test_same_phone_only_original_visitor_recovers_ticket(self):
        ticket = self.register()
        self.assertTrue(self.register()['duplicate'])
        other = self.store.visitor(None)[0]
        self.assert_problem(409, self.register, None, other)
        self.assertEqual(self.events.mine(other, ORIGIN)['tickets'], [])
        self.assertEqual(self.events.mine(self.visitor, ORIGIN)['tickets'][0]['orderToken'], ticket['orderToken'])

    def test_parallel_capacity_last_seat_is_never_oversold(self):
        self.events.save({'capacity': 1}, self.event['id'])
        visitors = [self.store.visitor(None)[0] for _ in range(8)]
        def work(index):
            try:
                return self.register(registration_body(phone='139000000%02d' % index), visitors[index])
            except Problem as error:
                return error.status
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(8)))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(results.count(409), 7)
        self.assertEqual(self.events.get(self.event['id'])['event']['remaining'], 0)

    def test_parallel_same_request_creates_one_order(self):
        data = registration_body()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.register(data), range(8)))
        self.assertEqual(len({item['orderToken'] for item in results}), 1)
        self.assertEqual(sum(not item['duplicate'] for item in results), 1)

    def test_expiry_does_not_release_until_durable_local_fence_and_cannot_check_in(self):
        self.events.save({'capacity': 1}, self.event['id'])
        receipt = self.register()
        self.now += 1800
        expired = self.events.ticket(receipt['orderToken'], ORIGIN)
        self.assertEqual(expired['order']['status'], 'pending_payment')
        self.assertEqual(expired['event']['pendingCount'], 1)
        self.assertEqual(expired['event']['remaining'], 0)
        self.assertIsNone(expired['qrDataUrl'])
        self.assert_problem(409, self.events.check_in, receipt['orderToken'], str(uuid.uuid4()), ORIGIN, 'test-admin')
        # No create was ever attempted; worker atomically fences the never-sent
        # command before freeing the seat. A mere timestamp cannot do this.
        PaymentWorker(self.events.payments, self.events.payment_adapter).run_once()
        self.assertEqual(self.events.registrations(self.event['id'], 'expired')['total'], 1)
        replacement = self.register()
        self.assertNotEqual(replacement['orderToken'], receipt['orderToken'])

    def test_reservation_expiry_never_exceeds_registration_close(self):
        self.events.save({'registrationClosesAt': iso(self.now + 60)}, self.event['id'])
        receipt = self.register()
        self.assertEqual(receipt['order']['expiresAt'], iso(self.now + 60))
        self.now += 60
        self.assert_problem(409, self.register, registration_body(phone='13900000002'))

    def test_cancel_pending_only_owner_can_cancel_and_waits_for_worker(self):
        receipt = self.register()
        self.assert_problem(403, self.events.cancel, receipt['orderToken'], ORIGIN, self.store.visitor(None)[0])
        result = self.events.cancel(receipt['orderToken'], ORIGIN, self.visitor)
        self.assertEqual(result['order']['status'], 'closing')
        self.assertEqual(result['event']['remaining'], 4)
        self.assertEqual(self.events.cancel(receipt['orderToken'], ORIGIN, self.visitor), result)
        PaymentWorker(self.events.payments, self.events.payment_adapter).run_once()
        self.assertEqual(self.events.ticket(receipt['orderToken'], ORIGIN)['order']['status'], 'cancelled')

    def test_paid_refund_request_invalidates_ticket_but_does_not_claim_refunded(self):
        receipt = self.paid()
        result = self.events.cancel(receipt['orderToken'], ORIGIN, self.visitor)
        self.assertEqual(result['order']['status'], 'refund_pending')
        self.assertIsNone(result['qrDataUrl'])
        self.assertEqual(result['event']['remaining'], 4)
        self.assertEqual(result['event']['refundPendingCount'], 1)
        self.assertFalse(self.events.preview(receipt['checkInUrl'], ORIGIN)['eligible'])
        self.assert_problem(409, self.events.check_in, receipt['orderToken'], str(uuid.uuid4()), ORIGIN, 'test-admin')

    def test_paid_qr_png_contains_fragment_url_without_personal_fields(self):
        import qrcode
        with patch('campaign_events.qrcode.make', wraps=qrcode.make) as make:
            receipt = self.paid()
        encoded_url = make.call_args.args[0]
        self.assertEqual(encoded_url, receipt['checkInUrl'])
        self.assertIn('/check-in#ticket=', encoded_url)
        self.assertNotIn('?', encoded_url)
        self.assertNotIn('13900000001', encoded_url)
        raw = base64.b64decode(receipt['qrDataUrl'].split(',')[1])
        image = Image.open(io.BytesIO(raw))
        self.assertEqual(image.format, 'PNG')
        self.assertGreater(image.width, 200)
        self.assertEqual(image.width, image.height)
        self.assertNotIn('phone', receipt['registration'])

    def test_atomic_check_in_duplicate_does_not_change_admission_time(self):
        receipt = self.paid()
        self.assertTrue(self.events.preview(receipt['checkInUrl'], ORIGIN)['eligible'])
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.events.check_in(receipt['registration']['ticketNumber'], str(uuid.uuid4()), ORIGIN, 'test-admin'), range(8)))
        self.assertEqual(sum(not item['duplicate'] for item in results), 1)
        self.assertEqual({item['registration']['checkedInAt'] for item in results}, {iso(self.now)})
        self.assert_problem(409, self.events.cancel, receipt['orderToken'], ORIGIN, self.visitor)
        self.assertFalse(self.events.preview(receipt['orderToken'], ORIGIN)['eligible'])

    def test_check_in_time_bounds_draft_closed_and_cancelled_event(self):
        receipt = self.paid()
        self.now -= 3601
        self.assertFalse(self.events.preview(receipt['orderToken'], ORIGIN)['eligible'])
        self.now += 3601
        self.events.save({'status': 'closed'}, self.event['id'])
        self.assertTrue(self.events.preview(receipt['orderToken'], ORIGIN)['eligible'])
        self.assert_problem(409, self.events.save, {'status': 'draft'}, self.event['id'])
        self.events.save({'status': 'cancelled'}, self.event['id'])
        self.assertEqual(self.events.ticket(receipt['orderToken'], ORIGIN)['order']['status'], 'refund_pending')
        self.assertFalse(self.events.preview(receipt['orderToken'], ORIGIN)['eligible'])
        self.assert_problem(409, self.events.save, {'status': 'published'}, self.event['id'])

    def test_bad_tokens_short_codes_public_and_foreign_qr_denied(self):
        receipt = self.paid()
        for code in ('invalid', '0' * 32 + '.' + 'A' * 43, receipt['orderToken'][:-1] + '!', receipt['registration']['ticketNumber']):
            self.assert_problem(404, self.events.ticket, code, ORIGIN)
        self.assert_problem(400, self.events.preview, receipt['checkInUrl'].replace(ORIGIN, 'https://foreign.test'), ORIGIN)
        self.assert_problem(400, self.events.preview, receipt['checkInUrl'] + '&ticket=duplicate', ORIGIN)

    def test_event_input_and_registration_validation(self):
        for changes in ({'priceFen': 0}, {'priceFen': True}, {'priceFen': 99.5}, {'priceFen': 10000001},
                        {'capacity': 0}, {'capacity': True}, {'startsAt': '2026-12-01T12:00'},
                        {'endsAt': iso(self.now)}, {'status': 'bad'}, {'city': 'beijing'}, {'title': '\x00bad'}):
            self.assert_problem(400, self.events.save, event_body(self.now, **changes))
        for changes in ({'consent': False}, {'name': '甲'}, {'phone': 'abc'}, {'phone': 13900000001},
                        {'requestId': 'bad'}, {'status': 'paid'}, {'name': '\x00测试者'}):
            self.assert_problem(400, self.register, registration_body(**changes))
        draft = self.events.save(event_body(self.now, status='draft'))['event']
        self.assertNotIn(draft['id'], [row['id'] for row in self.events.list()['events']])
        self.assert_problem(404, self.register, None, None, draft['id'])

    def test_schema_upgrade_preserves_old_votes_feedback_auth_and_signing_key(self):
        self.store.cast_vote(self.visitor, 'zhuhai', '192.0.2.1')
        self.store.feedback.submit(self.visitor, '192.0.2.1', {'requestId': str(uuid.uuid4()), 'content': '希望有新手训练课程', 'category': 'training', 'city': None})
        self.store.create_admin('test-admin', 'synthetic-password-123')
        token, session = self.store.new_session('test-admin')
        with self.store.connect() as db:
            from payment_schema import TABLES, VERSION
            for table in TABLES:
                db.execute('DROP TABLE ' + table)
            db.execute('DELETE FROM leanclub_schema_migrations WHERE version=?', (VERSION,))
            db.execute('DROP TABLE campaign_registrations')
            db.execute('DROP TABLE campaign_events')
            db.execute("DELETE FROM campaign_settings WHERE key='events_ticket_secret'")
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables}
        upgraded = CampaignStore(self.store.path, clock=lambda: self.now)
        with upgraded.connect() as db:
            after = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid') if not (table == 'campaign_settings' and row[0] == 'events_ticket_secret')] for table in tables}
        # Fresh payment coordination adds its migration marker only.
        after['leanclub_schema_migrations'] = [row for row in after['leanclub_schema_migrations'] if row[0] != VERSION]
        self.assertEqual(before, after)
        self.assertEqual(upgraded.session(token), session)
        self.assertEqual(upgraded.stats()['totalVotes'], 1)
        self.assertEqual(upgraded.feedback.list()['total'], 1)
        self.assertEqual(upgraded.events.list()['events'], [])


class EventsHTTPTests(unittest.TestCase):
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

    def test_public_order_roundtrip_auth_csrf_and_no_get_mutations(self):
        for path in ('/api/admin/events', '/api/admin/events/' + '0' * 32 + '/registrations'):
            self.assertEqual(self.request('GET', path)[0], 401)
        self.assertEqual(self.request('POST', '/api/admin/events', event_body(self.store.clock()))[0], 401)
        self.setup_admin()
        headers = {'X-CSRF-Token': self.csrf}
        body = event_body(self.store.clock())
        self.assertEqual(self.request('POST', '/api/admin/events', body)[0], 403)
        self.assertEqual(self.request('POST', '/api/admin/events', body, headers | {'Origin': 'https://foreign.test'})[0], 403)
        code, result, _ = self.request('POST', '/api/admin/events', body, headers)
        self.assertEqual(code, 201)
        event_id = result['event']['id']
        endpoint = '/api/campaign/events/' + event_id + '/register'
        self.assertEqual(self.request('POST', endpoint, registration_body())[0], 410)
        self.store.events.payment_adapter = FakePaymentAdapter(self.store.clock)
        self.assertEqual(self.request('POST', endpoint, registration_body(), {'Origin': 'https://foreign.test'})[0], 403)
        data = registration_body()
        self.assertEqual(self.request('POST', endpoint, data)[0], 410)
        # Existing anonymous orders remain readable with their original cookie.
        self.request('GET', '/api/campaign')
        visitor = self.store.visitor(self.cookies['lean_visitor'])[0]
        order = self.store.events.register(visitor, event_id, data, self.origin)
        self.assertIsNone(order['qrDataUrl'])
        self.assertEqual(self.request('GET', endpoint)[0], 404)
        self.assertEqual(self.request('GET', '/api/admin/events/check-in')[0], 404)
        self.assertEqual(self.request('POST', '/api/campaign/events/ticket', {'orderToken': order['orderToken']})[0], 200)
        self.assertEqual(self.request('GET', '/api/campaign/events/mine')[1]['tickets'][0]['order']['id'], order['order']['id'])
        self.assertEqual(self.request('POST', '/api/admin/events/check-in', {'code': order['orderToken'], 'requestId': str(uuid.uuid4())}, headers)[0], 409)
        synthetic_paid(self.store, order['order']['id'])
        self.assertEqual(self.request('POST', '/api/admin/events/check-in/preview', {'code': order['orderToken']})[0], 403)
        self.assertTrue(self.request('POST', '/api/admin/events/check-in/preview', {'code': order['orderToken']}, headers)[1]['eligible'])
        checked = self.request('POST', '/api/admin/events/check-in', {'code': order['orderToken'], 'requestId': str(uuid.uuid4())}, headers)
        self.assertEqual(checked[0], 200)
        self.assertFalse(checked[1]['duplicate'])
        self.assertTrue(self.request('POST', '/api/admin/events/check-in', {'code': order['orderToken'], 'requestId': str(uuid.uuid4())}, headers)[1]['duplicate'])

    def test_admin_filters_reject_bad_parameters_and_client_paid_is_never_trusted(self):
        self.setup_admin()
        headers = {'X-CSRF-Token': self.csrf}
        event_id = self.request('POST', '/api/admin/events', event_body(self.store.clock()), headers)[1]['event']['id']
        path = '/api/admin/events/' + event_id + '/registrations'
        for query in ('page=0', 'page=-1', 'page=1&page=2', 'status=nope', 'status=paid&status=expired', 'extra=1'):
            self.assertEqual(self.request('GET', path + '?' + query)[0], 400)
        self.assertEqual(self.request('GET', path + '?status=paid&page=1')[0], 200)
        self.assertEqual(self.request('POST', '/api/campaign/events/' + event_id + '/register', registration_body(status='paid'))[0], 410)
        for endpoint in ('/api/campaign/events/pay', '/api/campaign/events/confirm-payment', '/api/admin/events/confirm-payment'):
            self.assertIn(self.request('POST', endpoint, {'status': 'paid'}, headers)[0], (400, 404))


if __name__ == '__main__':
    unittest.main()
