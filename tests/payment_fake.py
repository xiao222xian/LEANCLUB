"""In-process test adapter only. Never imported by application/runtime code."""
from dataclasses import replace
import uuid

from payment_lifecycle import (PaymentScope, PaymentObservation, RefundObservation)


class FakePaymentAdapter:
    ready = True
    scope = PaymentScope('test-provider', 'isolated-merchant', 'isolated-app')

    def __init__(self, clock=lambda: 1792674000):
        self.clock = clock
        self.payments = {}
        self.refunds = {}
        self.calls = []
        self.hook = None

    def observe(self, binding, status='pending', **changes):
        return PaymentObservation(binding, uuid.uuid4().hex, status, int(self.clock()), 'provider_query', **changes)

    def paid(self, binding, transaction_id=None):
        obs = self.observe(binding, 'paid', transaction_id=transaction_id or uuid.uuid4().hex, paid_at=int(self.clock()))
        self.payments[binding.out_trade_no] = obs
        return obs

    def call(self, name, binding):
        self.calls.append((name, binding))
        if self.hook:
            self.hook(name, binding)

    def create_payment(self, binding):
        self.call('create', binding)
        return self.payments.setdefault(binding.out_trade_no, self.observe(binding))

    def query_payment(self, binding):
        self.call('query', binding)
        return self.payments.get(binding.out_trade_no, self.observe(binding, 'unknown'))

    def close_payment(self, binding):
        self.call('close', binding)
        existing = self.payments.get(binding.out_trade_no)
        if existing is None or existing.status == 'paid':
            return existing or self.observe(binding, 'unknown')
        observation = self.observe(binding, 'confirmed_closed', existing_terminal=True, all_attempts_closed=True, creation_fenced=True)
        self.payments[binding.out_trade_no] = observation
        return observation

    def create_refund(self, binding):
        self.call('refund-create', binding)
        return self.refunds.setdefault(binding.refund_id, RefundObservation(binding, uuid.uuid4().hex, 'pending', int(self.clock()), 'provider_create'))

    def query_refund(self, binding):
        self.call('refund-query', binding)
        return self.refunds.get(binding.refund_id, RefundObservation(binding, uuid.uuid4().hex, 'unknown', int(self.clock()), 'provider_query'))

    def refunded(self, binding, **changes):
        observation = RefundObservation(binding, uuid.uuid4().hex, 'succeeded', int(self.clock()), 'provider_query', uuid.uuid4().hex)
        observation = replace(observation, **changes)
        self.refunds[binding.refund_id] = observation
        return observation
