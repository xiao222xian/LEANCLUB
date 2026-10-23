"""Payment lifecycle core; no HTTP route, provider implementation or running worker.

Only a trusted in-process adapter can supply observations. Local PayOrder CLOSED,
ORDERNOTEXIST, elapsed time, failed requests and expired leases are NOT proof that
money cannot arrive. No provider call is made while a database transaction is open.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import uuid

from payment_schema import payment_schema_ready
from store import Problem

HOLDING = ('pending_payment', 'closing', 'paid', 'refund_pending')
TERMINAL = ('expired', 'cancelled', 'refunded')


@dataclass(frozen=True)
class PaymentScope:
    provider: str
    merchant_id: str
    app_id: str
    currency: str = 'CNY'

    def __post_init__(self):
        if self.currency != 'CNY' or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', v)
                for v in (self.provider, self.merchant_id, self.app_id)):
            raise ValueError('Invalid adapter scope.')


@dataclass(frozen=True)
class PaymentBinding:
    order_id: str
    merchant_order_id: str
    attempt_id: str
    out_trade_no: str
    provider: str
    merchant_id: str
    app_id: str
    currency: str
    amount_fen: int


@dataclass(frozen=True)
class PaymentObservation:
    binding: PaymentBinding
    event_key: str
    status: str  # pending | paid | confirmed_closed | unknown
    observed_at: int
    source: str  # provider_query | verified_callback | provider_close | provider_create
    transaction_id: str = ''
    paid_at: int | None = None
    # These attestations must come from provider-specific trusted adapter code.
    # Never map a local status or an untrusted HTTP boolean to these fields.
    existing_terminal: bool = False
    all_attempts_closed: bool = False
    creation_fenced: bool = False


@dataclass(frozen=True)
class RefundBinding:
    payment: PaymentBinding
    refund_id: str
    transaction_id: str


@dataclass(frozen=True)
class RefundObservation:
    binding: RefundBinding
    event_key: str
    status: str  # pending | succeeded | failed | unknown
    observed_at: int
    source: str
    provider_refund_id: str = ''


def adapter_ready(adapter):
    return (adapter is not None and getattr(adapter, 'ready', None) is True
            and isinstance(getattr(adapter, 'scope', None), PaymentScope)
            and all(callable(getattr(adapter, method, None)) for method in
                    ('create_payment', 'query_payment', 'close_payment', 'create_refund', 'query_refund')))


def binding_from(row):
    return PaymentBinding(**{key: row[key] for key in PaymentBinding.__dataclass_fields__})


class PaymentLifecycle:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            self.ready = payment_schema_ready(db)

    def now(self):
        return int(self.store.clock())

    def anomaly(self, db, order_id, code):
        if self.ready:
            db.execute('INSERT OR IGNORE INTO campaign_payment_anomalies VALUES(?,?,?)', (order_id, code, self.now()))

    def enqueue(self, db, order_id, kind):
        # Keep an existing lease: cancellation during create I/O must not cause a
        # concurrent new creation. Wake a completed command only when necessary.
        db.execute('''INSERT INTO campaign_payment_outbox
            (id,order_id,kind,state,available_at,created) VALUES(?,?,?,'pending',?,?)
            ON CONFLICT(order_id,kind) DO UPDATE SET state='pending',available_at=MIN(available_at,excluded.available_at)''',
                   (uuid.uuid4().hex, order_id, kind, self.now(), self.now()))

    def bind(self, db, row, adapter):
        if not self.ready or not adapter_ready(adapter):
            raise Problem(409, '报名收款尚未开通，请稍后再来。')
        scope = adapter.scope
        attempt = uuid.uuid4().hex
        db.execute('''INSERT INTO campaign_payment_bindings
            (order_id,merchant_order_id,attempt_id,out_trade_no,provider,merchant_id,app_id,currency,amount_fen,create_state,created)
            VALUES(?,?,?,?,?,?,?,?,?,'unstarted',?)''',
                   (row['id'], row['id'], attempt, attempt, scope.provider, scope.merchant_id, scope.app_id,
                    scope.currency, row['amount_fen'], self.now()))
        self.enqueue(db, row['id'], 'payment')

    def request_close(self, db, row, reason):
        if reason not in ('timeout', 'user_cancel', 'event_cancel', 'unfulfillable'):
            raise ValueError('Invalid close reason.')
        if row['payment_status'] in TERMINAL:
            return
        binding = db.execute('SELECT * FROM campaign_payment_bindings WHERE order_id=?', (row['id'],)).fetchone() if self.ready else None
        if binding:
            # Cancellation has priority over timeout forever; a late payment
            # must not resurrect a cancelled activity or owner's ticket.
            priority = {'timeout': 0, 'unfulfillable': 1, 'user_cancel': 2, 'event_cancel': 3}
            old_reason = binding['close_reason']
            selected = reason if old_reason is None or priority[reason] > priority[old_reason] else old_reason
            db.execute('UPDATE campaign_payment_bindings SET close_reason=?,version=version+1 WHERE order_id=?', (selected, row['id']))
        status = 'refund_pending' if row['payment_status'] in ('paid', 'refund_pending') else 'closing'
        db.execute('UPDATE campaign_registrations SET payment_status=?,cancelled=COALESCE(cancelled,?) WHERE id=?',
                   (status, self.now() if reason != 'timeout' else None, row['id']))
        if not binding:
            # Old rows were never coupled to a durable channel attempt. We do
            # not infer "never sent" from absence of metadata after migration.
            self.anomaly(db, row['id'], 'legacy_unbound_' + status)
        elif status == 'refund_pending':
            self.ensure_refund(db, row['id'])
        else:
            self.enqueue(db, row['id'], 'payment')

    def expire(self, db):
        for row in db.execute("SELECT * FROM campaign_registrations WHERE payment_status='pending_payment' AND payment_expires<=?", (self.now(),)).fetchall():
            self.request_close(db, row, 'timeout')

    def ensure_refund(self, db, order_id):
        tx = db.execute('SELECT transaction_id FROM campaign_payment_transactions WHERE order_id=?', (order_id,)).fetchone()
        if tx is None:
            self.anomaly(db, order_id, 'refund_missing_verified_payment')
            return
        binding = db.execute('SELECT provider,merchant_id FROM campaign_payment_bindings WHERE order_id=?', (order_id,)).fetchone()
        db.execute('''INSERT OR IGNORE INTO campaign_payment_refunds
            (order_id,refund_id,transaction_id,provider,merchant_id,state,created) VALUES(?,?,?,?,?,'unstarted',?)''',
                   (order_id, uuid.uuid4().hex, tx['transaction_id'], binding['provider'], binding['merchant_id'], self.now()))
        self.enqueue(db, order_id, 'refund')

    def _validate(self, db, observation, kind):
        cls = PaymentObservation if kind == 'payment' else RefundObservation
        if type(observation) is not cls:
            raise ValueError('Invalid trusted observation type.')
        if kind == 'refund' and type(observation.binding) is not RefundBinding:
            raise ValueError('Invalid refund binding.')
        target = observation.binding if kind == 'payment' else observation.binding.payment
        if type(target) is not PaymentBinding or type(target.amount_fen) is not int:
            raise ValueError('Invalid observation binding.')
        row = db.execute('SELECT * FROM campaign_payment_bindings WHERE order_id=?', (target.order_id,)).fetchone()
        if row is None or target != binding_from(row):
            raise ValueError('Observation does not match immutable payment binding.')
        if (type(observation.observed_at) is not int or not 0 < observation.observed_at <= self.now() + 60
                or observation.source not in ('provider_query', 'verified_callback', 'provider_close', 'provider_create')
                or not isinstance(observation.event_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', observation.event_key)):
            raise ValueError('Missing trusted provider evidence.')
        if kind == 'payment':
            if observation.status not in ('pending', 'paid', 'confirmed_closed', 'unknown'):
                raise ValueError('Invalid payment status.')
            if observation.status == 'paid' and (not isinstance(observation.transaction_id, str)
                    or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', observation.transaction_id)
                    or type(observation.paid_at) is not int or not 0 < observation.paid_at <= observation.observed_at + 60):
                raise ValueError('Missing payment transaction evidence.')
            if observation.status == 'confirmed_closed' and (observation.source not in ('provider_close', 'provider_query', 'verified_callback')
                    or observation.existing_terminal is not True or observation.all_attempts_closed is not True or observation.creation_fenced is not True):
                raise ValueError('Closure is not a proven provider terminal state.')
        else:
            refund = db.execute('SELECT * FROM campaign_payment_refunds WHERE order_id=?', (target.order_id,)).fetchone()
            if (type(observation.binding) is not RefundBinding or refund is None
                    or (observation.binding.refund_id, observation.binding.transaction_id) != (refund['refund_id'], refund['transaction_id'])
                    or observation.status not in ('pending', 'succeeded', 'failed', 'unknown')):
                raise ValueError('Invalid refund binding or status.')
            if observation.status == 'succeeded' and (not isinstance(observation.provider_refund_id, str)
                    or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}', observation.provider_refund_id)):
                raise ValueError('Missing refund transaction evidence.')
        return row

    def _inbox(self, db, observation, kind):
        target = observation.binding if kind == 'payment' else observation.binding.payment
        digest = hashlib.sha256(json.dumps(asdict(observation), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        old = db.execute('SELECT body_hash FROM campaign_payment_inbox WHERE provider=? AND merchant_id=? AND event_key=?',
                         (target.provider, target.merchant_id, observation.event_key)).fetchone()
        if old:
            if old['body_hash'] != digest:
                raise ValueError('Provider event identity reused with a different payload.')
            return False
        db.execute('INSERT INTO campaign_payment_inbox VALUES(?,?,?,?,?,?,?,?,?)',
                   (target.provider, target.merchant_id, observation.event_key, target.order_id, kind,
                    digest, observation.status, observation.observed_at, self.now()))
        return True

    def accept(self, observation):
        """Trusted adapter/callback boundary only; intentionally not HTTP exposed."""
        if not self.ready:
            raise ValueError('Explicit payment migration required.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._accept(db, observation)

    def _accept(self, db, observation):
        kind = 'refund' if type(observation) is RefundObservation else 'payment'
        binding = self._validate(db, observation, kind)
        if not self._inbox(db, observation, kind):
            return False
        if kind == 'refund':
            self._refund(db, binding, observation)
        else:
            self._payment(db, binding, observation)
        event_id = db.execute('SELECT event_id FROM campaign_registrations WHERE id=?', (binding['order_id'],)).fetchone()[0]
        self.audit_capacity(db, event_id)
        return True

    def audit_capacity(self, db, event_id):
        capacity = db.execute('SELECT capacity FROM campaign_events WHERE id=?', (event_id,)).fetchone()[0]
        held = db.execute("SELECT id FROM campaign_registrations WHERE event_id=? AND payment_status IN ('pending_payment','closing','paid','refund_pending')", (event_id,)).fetchall()
        if len(held) > capacity:
            for row in held:
                self.anomaly(db, row['id'], 'capacity_conflict_requires_reconciliation')

    def _payment(self, db, binding, observation):
        row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (binding['order_id'],)).fetchone()
        if observation.status == 'paid':
            existing = db.execute('''SELECT * FROM campaign_payment_transactions WHERE order_id=? OR
                (provider=? AND merchant_id=? AND transaction_id=?)''',
                (row['id'], binding['provider'], binding['merchant_id'], observation.transaction_id)).fetchall()
            if existing and any(tx['order_id'] != row['id'] or tx['transaction_id'] != observation.transaction_id for tx in existing):
                self.anomaly(db, row['id'], 'transaction_ownership_conflict')
                return
            db.execute('INSERT OR IGNORE INTO campaign_payment_transactions VALUES(?,?,?,?,?)',
                (binding['provider'], binding['merchant_id'], observation.transaction_id, row['id'], observation.paid_at))
            # A repeat observation never changes paid_at, refund intent or a
            # checked-in/refunded record. New event IDs are common on retries.
            if row['payment_status'] in ('paid', 'refund_pending', 'refunded'):
                if row['payment_status'] == 'refund_pending':
                    self.ensure_refund(db, row['id'])
                return
            event = db.execute('SELECT * FROM campaign_events WHERE id=?', (row['event_id'],)).fetchone()
            occupied = db.execute("SELECT COUNT(*) FROM campaign_registrations WHERE event_id=? AND id!=? AND payment_status IN ('pending_payment','closing','paid','refund_pending')",
                                  (row['event_id'], row['id'])).fetchone()[0]
            fulfill = (row['payment_status'] in ('pending_payment', 'closing') and row['status'] != 'cancelled'
                       and binding['close_reason'] in (None, 'timeout') and event['status'] in ('published', 'closed')
                       and self.now() < event['ends'] and occupied < event['capacity'])
            state = 'paid' if fulfill else 'refund_pending'
            # Store a scoped, collision-resistant reference; the unique provider
            # transaction identity above, not this display field, owns the money.
            reference = hashlib.sha256((binding['provider'] + '\n' + binding['merchant_id'] + '\n' + observation.transaction_id).encode()).hexdigest()
            db.execute('''UPDATE campaign_registrations SET payment_status=?,paid_at=COALESCE(paid_at,?),
                payment_reference=COALESCE(payment_reference,?),paid_by='verified-adapter' WHERE id=?''',
                (state, observation.paid_at, reference, row['id']))
            if not fulfill:
                self.anomaly(db, row['id'], 'paid_requires_refund')
                self.ensure_refund(db, row['id'])
        elif observation.status == 'confirmed_closed':
            if row['payment_status'] in ('paid', 'refund_pending', 'refunded'):
                self.anomaly(db, row['id'], 'closed_after_payment')
                return
            state = 'expired' if binding['close_reason'] in (None, 'timeout') else 'cancelled'
            db.execute("UPDATE campaign_registrations SET payment_status=?,status='cancelled' WHERE id=?", (state, row['id']))
            db.execute("UPDATE campaign_payment_bindings SET create_state='fenced' WHERE order_id=?", (row['id'],))
        elif observation.status == 'pending' and binding['create_state'] != 'fenced':
            db.execute("UPDATE campaign_payment_bindings SET create_state='observed' WHERE order_id=?", (row['id'],))
        elif observation.status == 'unknown':
            self.anomaly(db, row['id'], 'payment_outcome_unknown')

    def _refund(self, db, binding, observation):
        row = db.execute('SELECT * FROM campaign_payment_refunds WHERE order_id=?', (binding['order_id'],)).fetchone()
        if row['state'] == 'succeeded':
            if observation.status == 'succeeded' and row['provider_refund_id'] != observation.provider_refund_id:
                self.anomaly(db, binding['order_id'], 'refund_reference_changed')
            return
        if observation.status == 'succeeded':
            other = db.execute('SELECT order_id FROM campaign_payment_refunds WHERE provider=? AND merchant_id=? AND provider_refund_id=?',
                               (binding['provider'], binding['merchant_id'], observation.provider_refund_id)).fetchone()
            if other and other['order_id'] != binding['order_id']:
                self.anomaly(db, binding['order_id'], 'refund_ownership_conflict')
                return
            db.execute("UPDATE campaign_payment_refunds SET state='succeeded',provider_refund_id=?,succeeded_at=? WHERE order_id=?",
                       (observation.provider_refund_id, observation.observed_at, binding['order_id']))
            db.execute("UPDATE campaign_registrations SET payment_status='refunded',status='cancelled',refund_at=?,refund_reference=? WHERE id=?",
                       (observation.observed_at, observation.provider_refund_id, binding['order_id']))
        else:
            db.execute("UPDATE campaign_payment_refunds SET state='pending' WHERE order_id=?", (binding['order_id'],))
            if observation.status == 'failed':
                self.anomaly(db, binding['order_id'], 'refund_failed_requires_reconciliation')


class PaymentWorker:
    """One bounded step; caller explicitly schedules it. Nothing auto-starts.

    Expired leases only query the SAME binding. Uncertain creation never creates
    another attempt, nor retries create based on ORDERNOTEXIST. The conservative
    unresolved case is surfaced for provider-specific recovery/manual review.
    """
    def __init__(self, lifecycle, adapter, lease_seconds=60):
        if not lifecycle.ready or not adapter_ready(adapter) or type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError('Payment worker requires migrated schema and trusted adapter.')
        self.domain, self.adapter, self.lease_seconds = lifecycle, adapter, lease_seconds

    def claim(self):
        d = self.domain
        with d.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            d.expire(db)
            task = db.execute("SELECT * FROM campaign_payment_outbox WHERE state='pending' AND available_at<=? AND lease_until<=? ORDER BY created,id LIMIT 1",
                              (d.now(), d.now())).fetchone()
            if task is None:
                return None
            raw = db.execute('SELECT * FROM campaign_payment_bindings WHERE order_id=?', (task['order_id'],)).fetchone()
            if raw is None:
                d.anomaly(db, task['order_id'], 'work_missing_binding')
                db.execute("UPDATE campaign_payment_outbox SET state='done',last_error='missing_binding' WHERE id=?", (task['id'],))
                return {'localFence': True}
            binding = binding_from(raw)
            if PaymentScope(binding.provider, binding.merchant_id, binding.app_id, binding.currency) != self.adapter.scope:
                raise ValueError('Worker adapter scope differs from persisted binding.')
            row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (task['order_id'],)).fetchone()
            if task['kind'] == 'payment' and row['payment_status'] not in ('pending_payment', 'closing'):
                db.execute("UPDATE campaign_payment_outbox SET state='done' WHERE id=?", (task['id'],))
                return {'localFence': True}
            # Atomic local fence before any create ever started. A claimed
            # create always marks started in the same transaction, so no old
            # worker can subsequently create this safely released order.
            if task['kind'] == 'payment' and raw['create_state'] == 'unstarted' and row['payment_status'] == 'closing':
                state = 'expired' if raw['close_reason'] == 'timeout' else 'cancelled'
                db.execute("UPDATE campaign_payment_bindings SET create_state='fenced',version=version+1 WHERE order_id=?", (row['id'],))
                db.execute("UPDATE campaign_registrations SET status='cancelled',payment_status=? WHERE id=?", (state, row['id']))
                db.execute("UPDATE campaign_payment_outbox SET state='done',lease_token=NULL,lease_until=0 WHERE id=?", (task['id'],))
                return {'localFence': True}
            first = False
            refund = None
            if task['kind'] == 'payment' and raw['create_state'] == 'unstarted':
                first = True
                db.execute("UPDATE campaign_payment_bindings SET create_state='started' WHERE order_id=?", (row['id'],))
            elif task['kind'] == 'refund':
                refund_row = db.execute('SELECT * FROM campaign_payment_refunds WHERE order_id=?', (row['id'],)).fetchone()
                if refund_row is None or refund_row['state'] == 'succeeded':
                    db.execute("UPDATE campaign_payment_outbox SET state='done' WHERE id=?", (task['id'],))
                    return {'localFence': True}
                refund = RefundBinding(binding, refund_row['refund_id'], refund_row['transaction_id'])
                first = refund_row['state'] == 'unstarted'
                if first:
                    db.execute("UPDATE campaign_payment_refunds SET state='started' WHERE order_id=?", (row['id'],))
            lease = uuid.uuid4().hex
            db.execute('UPDATE campaign_payment_outbox SET lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=?',
                       (lease, d.now() + self.lease_seconds, task['id']))
            return dict(task) | {'lease': lease, 'version': raw['version'], 'binding': binding,
                                 'refund': refund, 'first': first, 'closing': row['payment_status'] == 'closing'}

    def execute(self, work):
        # No sqlite connection/transaction is alive at this boundary.
        if work['kind'] == 'refund':
            return self.adapter.create_refund(work['refund']) if work['first'] else self.adapter.query_refund(work['refund'])
        if work['first']:
            return self.adapter.create_payment(work['binding'])
        observation = self.adapter.query_payment(work['binding'])
        if work['closing'] and type(observation) is PaymentObservation and observation.status == 'pending':
            # A valid observation for another order is still a misrouted result.
            # Reject it before a close side effect, not merely in finish().
            if observation.binding != work['binding']:
                raise ValueError('Query observation belongs to another work item.')
            with self.domain.store.connect() as db:
                self.domain._validate(db, observation, 'payment')
            return self.adapter.close_payment(work['binding'])
        return observation

    def finish(self, work, observation=None, error=None):
        d = self.domain
        if observation is not None:
            expected_type = PaymentObservation if work['kind'] == 'payment' else RefundObservation
            expected_binding = work['binding'] if work['kind'] == 'payment' else work['refund']
            if type(observation) is not expected_type or observation.binding != expected_binding:
                raise ValueError('Observation belongs to another work item.')
        with d.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM campaign_payment_outbox WHERE id=?', (work['id'],)).fetchone()
            current = db.execute('SELECT * FROM campaign_payment_bindings WHERE order_id=?', (work['order_id'],)).fetchone()
            if current is None or binding_from(current) != work['binding']:
                raise ValueError('Work binding changed.')
            stale = task['lease_token'] != work['lease'] or current['version'] != work['version']
            # Financial evidence from a late worker remains accountable, but
            # current cancellation intent always governs its resulting state.
            if observation is not None:
                d._accept(db, observation)
            if stale:
                d.anomaly(db, work['order_id'], 'stale_worker_result_reconciled')
            if task['lease_token'] != work['lease']:
                return False  # never acknowledge or steal a newer worker's lease
            row = db.execute('SELECT payment_status FROM campaign_registrations WHERE id=?', (work['order_id'],)).fetchone()
            done = (row['payment_status'] not in ('pending_payment', 'closing')) if work['kind'] == 'payment' else row['payment_status'] == 'refunded'
            db.execute('''UPDATE campaign_payment_outbox SET state=?,lease_token=NULL,lease_until=0,
                available_at=?,last_error=? WHERE id=?''',
                       ('done' if done else 'pending', d.now() + min(60, 5 * task['attempts']), error, task['id']))
            if error:
                d.anomaly(db, work['order_id'], error)
            return True

    def run_once(self):
        work = self.claim()
        if work is None:
            return False
        if work.get('localFence'):
            return True
        try:
            observation = self.execute(work)
        except Exception:
            # Provider errors may contain personal information or credentials.
            self.finish(work, error='adapter_outcome_unknown')
        else:
            try:
                self.finish(work, observation)
            except (ValueError, TypeError):
                self.finish(work, error='invalid_adapter_evidence')
        return True
