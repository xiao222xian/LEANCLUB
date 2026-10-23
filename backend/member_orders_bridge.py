"""Private MEMBER-only bridge; independent from employee operations and cookies.

Java authenticates an enabled Yudao member. This service verifies the purpose-
bound signature and scopes every domain lookup to its signed tenant/member pair.
There are no payment-success, account-claim or arbitrary URL actions here.
"""
import hashlib
import hmac
import ipaddress
import json
import re
import sqlite3
import time

from campaign_events import MemberPrincipal
from campaign_http import CampaignAPI
from store import Problem

PATH = '/internal/leanclub/member-orders'
MAX_BODY = 32 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
ACTIONS = frozenset(('member-register', 'member-orders', 'member-order', 'member-cancel'))
MAX_ID = 9223372036854775807


def canonical(raw, timestamp, nonce, tenant, actor):
    return '\n'.join(('LCM1', 'POST', PATH, timestamp, nonce, tenant, 'MEMBER', actor,
                      hashlib.sha256(raw).hexdigest())).encode('utf-8')


class MemberOrdersBridge:
    def __init__(self, store, origin, secret='', tenant='1', clock=time.time):
        self.store, self.origin, self.clock = store, origin, clock
        self.tenant = str(tenant)
        if not re.fullmatch(r'[1-9][0-9]{0,18}', self.tenant) or int(self.tenant) > MAX_ID:
            raise ValueError('Invalid member order tenant.')
        if secret and not re.fullmatch(r'[a-f0-9]{64}', secret):
            raise ValueError('The member order key must be 32 random bytes encoded as lowercase hex.')
        self.key = secret.encode('utf-8')
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS leanclub_member_bridge_nonces (
                    nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS leanclub_member_operations_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL,
                    member_id TEXT NOT NULL, tenant TEXT NOT NULL,
                    action TEXT NOT NULL, status INTEGER NOT NULL);
            ''')

    def authenticate(self, headers, raw, peer):
        if not self.key:
            raise Problem(503, '会员报名服务尚未连接，请稍后再试。')
        try:
            if not ipaddress.ip_address(peer).is_loopback:
                raise ValueError()
            values = []
            for name in ('X-LC-Timestamp', 'X-LC-Nonce', 'X-LC-Tenant', 'X-LC-Actor-Type', 'X-LC-Actor', 'X-LC-Signature'):
                entries = headers.get_all(name, [])
                if len(entries) != 1:
                    raise ValueError()
                values.append(entries[0])
            timestamp, nonce, tenant, actor_type, actor, signature = values
            if (not re.fullmatch(r'[0-9]{10}', timestamp)
                    or not re.fullmatch(r'[a-f0-9]{32}', nonce)
                    or tenant != self.tenant or actor_type != 'MEMBER'
                    or not re.fullmatch(r'[1-9][0-9]{0,18}', actor) or int(actor) > MAX_ID
                    or not re.fullmatch(r'[a-f0-9]{64}', signature)
                    or abs(int(self.clock()) - int(timestamp)) > 60):
                raise ValueError()
            expected = hmac.new(self.key, canonical(raw, timestamp, nonce, tenant, actor), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
        except (ValueError, TypeError):
            raise Problem(403, '会员报名服务身份校验失败。')
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM leanclub_member_bridge_nonces WHERE expires < ?', (int(self.clock()),))
                db.execute('INSERT INTO leanclub_member_bridge_nonces VALUES(?,?)', (nonce, int(self.clock()) + 121))
        except sqlite3.IntegrityError:
            raise Problem(409, '会员请求已处理，请重新发起查询确认状态。')
        return MemberPrincipal(int(tenant), int(actor))

    @staticmethod
    def parse(raw):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def invalid(_):
            raise ValueError()
        try:
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
            if (not isinstance(data, dict) or set(data) != {'action', 'params'}
                    or not isinstance(data['action'], str) or data['action'] not in ACTIONS
                    or not isinstance(data['params'], dict)):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise Problem(400, '会员订单操作或参数格式无效。')
        return data['action'], data['params']

    @staticmethod
    def fields(params, required, optional=()):
        if not set(required).issubset(params) or set(params) - set(required) - set(optional):
            raise Problem(400, '会员订单操作参数不正确。')

    def dispatch(self, action, params, principal):
        events = self.store.events
        if action == 'member-register':
            self.fields(params, ('eventId', 'requestId', 'name', 'phone', 'consent', 'expectedPriceFen'))
        elif action == 'member-cancel':
            self.fields(params, ('id', 'requestId'))
        elif action == 'member-orders':
            self.fields(params, (), ('pageNo', 'pageSize', 'status'))
        else:
            self.fields(params, ('id',))
        if action in ('member-register', 'member-cancel'):
            # This key comes exclusively from the verified signature, never from
            # a submitted phone, IP header or chosen member ID.
            self.store.limit('member-order:%s:%s' % (principal.tenant_id, principal.member_id),
                             'member-order-write', 30, 60)
        if action == 'member-register':
            data = params.copy()
            event_id = data.pop('eventId')
            return events.register_member(principal, event_id, data, self.origin)
        if action == 'member-orders':
            return events.member_orders(principal, self.origin, params.get('pageNo', 1),
                                        params.get('pageSize', 20), params.get('status', 'all'))
        if action == 'member-order':
            return events.member_order(principal, params['id'], self.origin)
        if action == 'member-cancel':
            return events.cancel_member(principal, params['id'], params['requestId'], self.origin)
        raise Problem(400, '不支持此会员订单操作。')

    def execute(self, headers, raw, peer):
        if len(raw) > MAX_BODY:
            raise Problem(413, '会员订单请求过大。')
        principal = self.authenticate(headers, raw, peer)
        action, params = self.parse(raw)
        status = 500
        try:
            result = self.dispatch(action, params, principal)
            if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_RESPONSE:
                raise Problem(502, '会员订单返回内容过大，请减少查询范围。')
            status = 200
            return result
        except Problem as exc:
            status = exc.status
            raise
        finally:
            # No name, phone, bearer, request body, QR, ticket or claim secret.
            with self.store.connect() as db:
                db.execute('''INSERT INTO leanclub_member_operations_audit
                    (created,member_id,tenant,action,status) VALUES(?,?,?,?,?)''',
                           (int(self.clock()), str(principal.member_id), self.tenant, action, status))

    def post(self, handler):
        if handler.path != PATH:
            raise Problem(404, '接口不存在。')
        if not self.key:
            raise Problem(503, '会员报名服务尚未连接，请稍后再试。')
        if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise Problem(415, '请求须为 JSON 格式。')
        raw = CampaignAPI.read_body(handler, MAX_BODY)
        return handler.send_json(200, self.execute(handler.headers, raw, handler.client_address[0]))
