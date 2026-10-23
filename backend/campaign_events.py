"""Paid event order reservation and authenticated, atomic admission.

Ticket secrets are derived from a database-local key; no bearer token is stored.
All capacity, cancellation and check-in decisions use a write transaction.
"""
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import re
import secrets
import unicodedata
from urllib.parse import parse_qs, urlsplit
import uuid

import qrcode
from qrcode.image.pil import PilImage

from store import Problem
from member_orders_schema import (registration_sql, create_member_metadata,
                                 member_schema_ready, ownership_columns_ready)
from payment_schema import create_payment_schema
from payment_lifecycle import PaymentLifecycle, adapter_ready

CITIES = frozenset(('shenzhen', 'zhuhai', 'guangzhou'))
STATUSES = frozenset(('draft', 'published', 'closed', 'cancelled'))
ID = re.compile(r'[a-f0-9]{32}')
TICKET = re.compile(r'([a-f0-9]{32})\.([A-Za-z0-9_-]{43})')
NUMBER = re.compile(r'LC-[A-Z2-7]{12}')
FIELDS = {'title', 'subtitle', 'description', 'city', 'venue', 'address', 'startsAt',
          'endsAt', 'registrationClosesAt', 'capacity', 'status', 'posterCity', 'priceFen'}
PAYMENT_STATUSES = ('pending_payment', 'closing', 'paid', 'expired', 'cancelled', 'refund_pending', 'refunded')


@dataclass(frozen=True)
class MemberPrincipal:
    """Constructed by the authenticated service bridge, never from client params."""
    tenant_id: int
    member_id: int

    def __post_init__(self):
        if any(type(value) is not int or not 1 <= value <= 9223372036854775807
               for value in (self.tenant_id, self.member_id)):
            raise Problem(403, '会员身份无效。')


def iso(stamp):
    return None if stamp is None else datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def text(value, label, minimum, maximum):
    if not isinstance(value, str) or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') and c not in ('\n', '\u200c', '\u200d') for c in value):
        raise Problem(400, label + '格式无效。')
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise Problem(400, '%s须为%d–%d字。' % (label, minimum, maximum))
    return value


def stamp(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        result = int(parsed.timestamp())
        if not 0 < result < 4102444800:
            raise ValueError()
        return result
    except (ValueError, OverflowError, TypeError):
        raise Problem(400, '活动时间须包含时区且在有效日期范围内。')


def request_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError()
        return value.lower()
    except (ValueError, AttributeError):
        raise Problem(400, '提交标识无效，请刷新重试。')


class CampaignEvents:
    def __init__(self, store):
        self.store = store
        # A real payment adapter must be configured before orders can be opened.
        # Tests can inject an in-process adapter; no HTTP mock-payment path exists.
        self.payment_adapter = None
        with store.connect() as db:
            fresh = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaign_registrations'").fetchone() is None
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_events (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, subtitle TEXT NOT NULL,
                    description TEXT NOT NULL, city TEXT NOT NULL, venue TEXT NOT NULL,
                    address TEXT NOT NULL, starts INTEGER NOT NULL, ends INTEGER NOT NULL,
                    closes INTEGER NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>0),
                    status TEXT NOT NULL CHECK(status IN ('draft','published','closed','cancelled')),
                    poster_city TEXT NOT NULL, created INTEGER NOT NULL, updated INTEGER NOT NULL);
                ''' + registration_sql() + ''';
                CREATE UNIQUE INDEX IF NOT EXISTS campaign_registration_phone ON
                    campaign_registrations(event_id,phone) WHERE status!='cancelled';
                CREATE INDEX IF NOT EXISTS campaign_registration_event ON
                    campaign_registrations(event_id,created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS campaign_registration_visitor ON
                    campaign_registrations(visitor,created DESC);
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(campaign_events)')}
            if 'price_fen' not in columns:
                db.execute('ALTER TABLE campaign_events ADD COLUMN price_fen INTEGER NOT NULL DEFAULT 0')
            if 'version' not in columns:
                db.execute('ALTER TABLE campaign_events ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(campaign_registrations)')}
            for name, declaration in [('payment_status', "TEXT NOT NULL DEFAULT 'pending_payment'"),
                                      ('amount_fen', 'INTEGER NOT NULL DEFAULT 0'), ('payment_expires', 'INTEGER NOT NULL DEFAULT 0'),
                                      ('paid_at', 'INTEGER'), ('payment_reference', 'TEXT'), ('paid_by', 'TEXT'),
                                      ('refund_at', 'INTEGER'), ('refund_reference', 'TEXT')]:
                if name not in columns:
                    db.execute('ALTER TABLE campaign_registrations ADD COLUMN ' + name + ' ' + declaration)
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS campaign_payment_reference ON campaign_registrations(payment_reference) WHERE payment_reference IS NOT NULL')
            db.execute('INSERT OR IGNORE INTO campaign_settings(key,value) VALUES(?,?)',
                       ('events_ticket_secret', secrets.token_hex(32)))
            self.secret = bytes.fromhex(db.execute('SELECT value FROM campaign_settings WHERE key=?',
                                                  ('events_ticket_secret',)).fetchone()[0])
            if len(self.secret) != 32:
                raise ValueError('Invalid event signing key.')
            # Fresh databases use the current schema. Existing databases require
            # the explicit backed-up migration; startup never rebuilds old rows.
            if fresh:
                create_member_metadata(db, int(self.store.clock()))
                create_payment_schema(db, int(self.store.clock()))
            self.has_member_columns = ownership_columns_ready(db)
            self.member_orders_ready = member_schema_ready(db)
        self.payments = PaymentLifecycle(store)

    @staticmethod
    def member_owned(row):
        return 'member_user_id' in row.keys() and row['member_user_id'] is not None

    @classmethod
    def owns_member(cls, row, principal):
        return (isinstance(principal, MemberPrincipal) and cls.member_owned(row)
                and row['member_tenant_id'] == principal.tenant_id
                and row['member_user_id'] == principal.member_id)

    def require_member_schema(self, principal):
        if not isinstance(principal, MemberPrincipal):
            raise Problem(403, '会员身份无效。')
        if not self.member_orders_ready:
            raise Problem(503, '会员报名服务尚未完成迁移，请联系平台管理员。')

    def event(self, db, event_id, public=False):
        if not isinstance(event_id, str) or not ID.fullmatch(event_id):
            raise Problem(404, '活动不存在。')
        row = db.execute('SELECT * FROM campaign_events WHERE id=?', (event_id,)).fetchone()
        if row is None or (public and row['status'] == 'draft'):
            raise Problem(404, '活动不存在。')
        return row

    def event_item(self, db, row):
        counts = db.execute("""SELECT COALESCE(SUM(payment_status='paid'),0),
            COALESCE(SUM(status='checked_in' AND payment_status='paid'),0),
            COALESCE(SUM(payment_status='pending_payment'),0),
            COALESCE(SUM(payment_status='refund_pending'),0),
            COALESCE(SUM(payment_status='closing'),0)
            FROM campaign_registrations WHERE event_id=?""", (row['id'],)).fetchone()
        item = {key: row[key] for key in ('id', 'title', 'subtitle', 'description', 'city', 'venue', 'address', 'capacity', 'status')}
        available = row['capacity'] - counts[0] - counts[2] - counts[3] - counts[4]
        remaining = max(0, available)
        payment_ready = self.payments.ready and adapter_ready(self.payment_adapter)
        return item | {'version': row['version'], 'startsAt': iso(row['starts']), 'endsAt': iso(row['ends']),
                       'registrationClosesAt': iso(row['closes']), 'posterCity': row['poster_city'],
                       'createdAt': iso(row['created']), 'updatedAt': iso(row['updated']),
                       'registeredCount': counts[0], 'checkedInCount': counts[1], 'pendingCount': counts[2],
                       'refundPendingCount': counts[3], 'closingCount': counts[4], 'remaining': remaining,
                       'capacityConflict': available < 0, 'overbookedCount': max(0, -available),
                       'priceFen': row['price_fen'], 'currency': 'CNY', 'paymentReady': payment_ready,
                       'registrationOpen': payment_ready and row['status'] == 'published' and row['price_fen'] > 0 and self.store.clock() < row['closes'] and remaining > 0}

    def list(self, admin=False):
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows = db.execute("SELECT * FROM campaign_events " + ('' if admin else "WHERE status!='draft' ") + 'ORDER BY starts,id').fetchall()
            return {'events': [self.event_item(db, row) for row in rows]}

    def get(self, event_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return {'event': self.event_item(db, self.event(db, event_id, public=True))}

    def save(self, data, event_id=None, expected_version=None):
        if not isinstance(data, dict) or not data or set(data) - FIELDS:
            raise Problem(400, '活动字段无效。')
        now = int(self.store.clock())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.event(db, event_id) if event_id else None
            if old and expected_version is not None and (type(expected_version) is not int or old['version'] != expected_version):
                raise Problem(409, '活动已被其他人修改，请重新载入后再保存。')
            values = self.event_item(db, old) if old else {'subtitle': '', 'description': '', 'status': 'draft'}
            values.update(data)
            if not FIELDS.issubset(values):
                raise Problem(400, '请填写活动标题、城市、时间、地点与人数上限。')
            for key, label, minimum, maximum in [('title', '标题', 2, 60), ('subtitle', '副标题', 0, 100),
                                                  ('description', '活动说明', 0, 5000), ('venue', '场地', 2, 100), ('address', '地址', 2, 200)]:
                values[key] = text(values[key], label, minimum, maximum)
            if any(not isinstance(values[k], str) or values[k] not in CITIES for k in ('city', 'posterCity')):
                raise Problem(400, '请选择深圳、珠海或广州。')
            if not isinstance(values['status'], str) or values['status'] not in STATUSES:
                raise Problem(400, '活动状态无效。')
            if type(values['capacity']) is not int or not 1 <= values['capacity'] <= 10000:
                raise Problem(400, '人数上限须为1–10000的整数。')
            if type(values['priceFen']) is not int or not 1 <= values['priceFen'] <= 10000000:
                raise Problem(400, '报名价格须为1–10000000分的整数。')
            starts, ends, closes = (stamp(values[k]) for k in ('startsAt', 'endsAt', 'registrationClosesAt'))
            if ends <= starts or closes > starts:
                raise Problem(400, '结束时间须晚于开始，报名截止须不晚于开始。')
            if values['status'] == 'published' and (not old or old['status'] != 'published') and (starts <= now or closes <= now):
                raise Problem(400, '请设置未来的活动开始与报名截止时间再发布。')
            if old:
                if values['capacity'] < values['registeredCount'] + values['pendingCount'] + values['refundPendingCount'] + values['closingCount']:
                    raise Problem(409, '人数上限不能少于当前已占用名额。')
                if old['status'] == 'cancelled' and values['status'] != 'cancelled':
                    raise Problem(409, '已取消活动不能重新启用，请创建新活动。')
                if values['status'] == 'draft' and db.execute('SELECT 1 FROM campaign_registrations WHERE event_id=? LIMIT 1', (event_id,)).fetchone():
                    raise Problem(409, '已有报名的活动不能退回草稿，可关闭报名或取消活动。')
                db.execute('''UPDATE campaign_events SET title=?,subtitle=?,description=?,city=?,venue=?,address=?,
                    starts=?,ends=?,closes=?,capacity=?,status=?,poster_city=?,updated=?,price_fen=?,version=version+1 WHERE id=?''',
                           tuple(values[k] for k in ('title', 'subtitle', 'description', 'city', 'venue', 'address')) +
                           (starts, ends, closes, values['capacity'], values['status'], values['posterCity'], now, values['priceFen'], event_id))
                if values['status'] == 'cancelled' and old['status'] != 'cancelled':
                    rows = db.execute("SELECT * FROM campaign_registrations WHERE event_id=? AND payment_status IN ('pending_payment','closing','paid','refund_pending')", (event_id,)).fetchall()
                    for row in rows:
                        self.payments.request_close(db, row, 'event_cancel')
            else:
                event_id = uuid.uuid4().hex
                db.execute('''INSERT INTO campaign_events(id,title,subtitle,description,city,venue,address,
                    starts,ends,closes,capacity,status,poster_city,created,updated,price_fen)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (event_id,) + tuple(values[k] for k in ('title', 'subtitle', 'description', 'city', 'venue', 'address')) +
                           (starts, ends, closes, values['capacity'], values['status'], values['posterCity'], now, now, values['priceFen']))
            return {'event': self.event_item(db, self.event(db, event_id))}

    def token(self, registration_id):
        signature = hmac.new(self.secret, ('event-ticket-v1:' + registration_id).encode(), hashlib.sha256).digest()
        return registration_id + '.' + base64.urlsafe_b64encode(signature).decode().rstrip('=')

    def lookup(self, db, code, origin, short=False):
        if not isinstance(code, str) or len(code) > 1000:
            raise Problem(404, '入场凭证无效，请检查票号。')
        code = code.strip()
        if short and code.startswith(('http://', 'https://')):
            try:
                link, expected = urlsplit(code), urlsplit(origin)
                query = parse_qs(link.fragment, strict_parsing=True)
                if (link.scheme, link.netloc) != (expected.scheme, expected.netloc) or link.path not in ('/check-in', '/ticket') or link.query or set(query) != {'ticket'} or len(query['ticket']) != 1:
                    raise ValueError()
                code = query['ticket'][0]
            except ValueError:
                raise Problem(400, '请扫描本站入场二维码或输入票号。')
        if short and NUMBER.fullmatch(code.upper()):
            row = db.execute('SELECT * FROM campaign_registrations WHERE ticket_number=?', (code.upper(),)).fetchone()
        elif TICKET.fullmatch(code) and hmac.compare_digest(self.token(code.split('.')[0]), code):
            row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (code.split('.')[0],)).fetchone()
        else:
            row = None
        if row is None:
            raise Problem(404, '入场凭证无效，请检查票号。')
        return row

    def effective_row(self, row):
        # Read paths never manufacture a financial terminal state from time.
        return dict(row)

    def expire(self, db):
        self.payments.expire(db)

    def registration_item(self, row, admin=False):
        row = self.effective_row(row)
        item = {key: row[key] for key in ('id', 'name', 'status')}
        item.update(ticketNumber=row['ticket_number'], phoneMasked=row['phone'][:3] + '****' + row['phone'][-4:],
                    createdAt=iso(row['created']), checkedInAt=iso(row['checked_in']), cancelledAt=iso(row['cancelled']),
                    paymentStatus=row['payment_status'], amountFen=row['amount_fen'], currency='CNY',
                    paymentExpiresAt=iso(row['payment_expires']), paidAt=iso(row['paid_at']))
        if admin:
            item['phone'] = row['phone']
        return item

    def receipt(self, db, row, origin, visitor=None, qr=True, member=None, summary=False):
        if member is not None:
            if not self.owns_member(row, member):
                raise Problem(404, '订单不存在。')
        elif self.member_owned(row):
            # A member's QR may be shown to a staff member, but it is never a
            # bearer credential for the old anonymous order-read endpoint.
            raise Problem(404, '入场凭证无效，请检查票号。')
        row = self.effective_row(row)
        event = self.event(db, row['event_id'])
        token = self.token(row['id'])
        check_url = origin + '/check-in#ticket=' + token
        paid = row['payment_status'] == 'paid' and event['status'] != 'cancelled'
        owner = self.owns_member(row, member) if member is not None else visitor is not None and row['visitor'] == visitor
        can_cancel = owner and row['status'] == 'registered' and row['payment_status'] in ('pending_payment', 'paid') and self.store.clock() < event['starts']
        result = {'event': self.event_item(db, event), 'registration': self.registration_item(row),
                  'order': {'id': row['id'], 'status': row['payment_status'], 'amountFen': row['amount_fen'],
                            'currency': 'CNY', 'expiresAt': iso(row['payment_expires']), 'paidAt': iso(row['paid_at'])},
                  'canCancel': bool(can_cancel)}
        if member is not None:
            order_url = origin + '/ticket?order=' + row['id']
            result.update(accessMode='member', orderUrl=order_url)
            if summary:
                return result
            paid = paid and row['status'] == 'registered' and event['status'] != 'draft' and self.store.clock() < event['ends']
            result.update(ticketUrl=order_url, checkInUrl=check_url if paid else None, qrDataUrl=None)
        else:
            result.update(orderToken=token, orderUrl=origin + '/ticket#ticket=' + token,
                          ticketToken=token if paid else None, ticketUrl=origin + '/ticket#ticket=' + token,
                          checkInUrl=check_url if paid else None, qrDataUrl=None)
        if qr and paid:
            image = qrcode.make(check_url, image_factory=PilImage, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
            output = io.BytesIO()
            image.save(output, format='PNG')
            result['qrDataUrl'] = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        return result

    @staticmethod
    def registration_data(data):
        if not isinstance(data, dict) or set(data) != {'requestId', 'name', 'phone', 'consent', 'expectedPriceFen'} or data['consent'] is not True:
            raise Problem(400, '请完整填写报名信息并同意活动联络与现场核验。')
        expected_price = data['expectedPriceFen']
        if type(expected_price) is not int or not 1 <= expected_price <= 10000000:
            raise Problem(400, '请确认本场活动的报名价格。')
        rid = request_id(data['requestId'])
        name = text(data['name'], '称呼', 2, 30)
        phone = data['phone']
        if not isinstance(phone, str) or not re.fullmatch(r'1[3-9][0-9]{9}', phone):
            raise Problem(400, '请输入有效的中国大陆11位手机号。')
        return rid, name, phone, expected_price

    def assert_registration_available(self, db, event, expected_price, now):
        if event['status'] != 'published' or event['price_fen'] < 1 or now >= event['closes']:
            raise Problem(409, '本场活动已停止报名。')
        if not self.payments.ready or not adapter_ready(self.payment_adapter):
            raise Problem(409, '报名收款尚未开通，请稍后再来。')
        if expected_price != event['price_fen']:
            raise Problem(409, '报名价格已调整，请刷新活动并重新确认价格。')
        if self.event_item(db, event)['remaining'] <= 0:
            raise Problem(409, '本场名额已满，期待下一次见面。')

    def insert_registration(self, db, event, rid, name, phone, now, visitor=None, member=None):
        identifier = uuid.uuid4().hex
        number = 'LC-' + base64.b32encode(secrets.token_bytes(8)).decode()[:12]
        columns = 'id,event_id,visitor,request_id,ticket_number,name,phone,status,created,payment_status,amount_fen,payment_expires'
        values = (identifier, event['id'], visitor, rid, number, name, phone, 'registered', now,
                  'pending_payment', event['price_fen'], min(now + 1800, event['closes']))
        if member is not None:
            columns += ',member_tenant_id,member_user_id,owner_bound_at'
            values += (member.tenant_id, member.member_id, now)
        db.execute('INSERT INTO campaign_registrations(' + columns + ') VALUES(' + ','.join('?' for _ in values) + ')', values)
        row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (identifier,)).fetchone()
        self.payments.bind(db, row, self.payment_adapter)
        return row

    def register(self, visitor, event_id, data, origin):
        rid, name, phone, expected_price = self.registration_data(data)
        if not isinstance(visitor, str) or not re.fullmatch(r'[a-f0-9]{64}', visitor):
            raise Problem(403, '请使用原报名浏览器。')
        now = int(self.store.clock())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            event = self.event(db, event_id, public=True)
            anonymous = ' AND member_user_id IS NULL' if self.has_member_columns else ''
            old = db.execute('SELECT * FROM campaign_registrations WHERE visitor=? AND request_id=?' + anonymous, (visitor, rid)).fetchone()
            if old:
                if (old['event_id'], old['name'], old['phone']) != (event_id, name, phone):
                    raise Problem(409, '该提交已完成，修改报名请使用新的提交标识。')
                return self.receipt(db, old, origin, visitor) | {'duplicate': True}
            old = db.execute("SELECT * FROM campaign_registrations WHERE event_id=? AND phone=? AND status!='cancelled'", (event_id, phone)).fetchone()
            if old:
                if not self.member_owned(old) and old['visitor'] == visitor:
                    return self.receipt(db, old, origin, visitor) | {'duplicate': True}
                raise Problem(409, '当前联系信息已有有效报名，请从原报名入口查看。')
            self.assert_registration_available(db, event, expected_price, now)
            row = self.insert_registration(db, event, rid, name, phone, now, visitor=visitor)
            return self.receipt(db, row, origin, visitor) | {'duplicate': False}

    def ticket(self, token, origin, visitor=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self.receipt(db, self.lookup(db, token, origin), origin, visitor)

    def mine(self, visitor, origin):
        with self.store.connect() as db:
            db.execute('BEGIN')
            anonymous = ' AND member_user_id IS NULL' if self.has_member_columns else ''
            rows = db.execute('SELECT * FROM campaign_registrations WHERE visitor=?' + anonymous + ' ORDER BY created DESC,id DESC LIMIT 100', (visitor,)).fetchall()
            return {'tickets': [self.receipt(db, row, origin, visitor) for row in rows]}

    def cancel(self, token, origin, visitor):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            row = self.lookup(db, token, origin)
            if self.member_owned(row):
                raise Problem(404, '入场凭证无效，请检查票号。')
            if visitor is None or row['visitor'] != visitor:
                raise Problem(403, '请使用原报名浏览器取消报名。')
            row = self.cancel_row(db, row)
            return self.receipt(db, row, origin, visitor)

    def cancel_row(self, db, row):
        event = self.event(db, row['event_id'])
        if row['payment_status'] not in ('cancelled', 'expired', 'refund_pending', 'refunded'):
            if row['status'] == 'checked_in' or self.store.clock() >= event['starts']:
                raise Problem(409, '活动已开始或入场券已核销，不能取消。')
            self.payments.request_close(db, row, 'user_cancel')
            row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (row['id'],)).fetchone()
        return row

    @staticmethod
    def command_hash(action, params):
        raw = json.dumps({'v': 1, 'action': action, 'params': params}, sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()

    def owned_member_row(self, db, principal, identifier):
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise Problem(404, '订单不存在。')
        row = db.execute('''SELECT * FROM campaign_registrations
            WHERE id=? AND member_tenant_id=? AND member_user_id=?''',
                         (identifier, principal.tenant_id, principal.member_id)).fetchone()
        if row is None:
            raise Problem(404, '订单不存在。')
        return row

    def member_command(self, db, principal, action, rid, fingerprint):
        command = db.execute('''SELECT * FROM campaign_member_requests
            WHERE tenant_id=? AND member_user_id=? AND action=? AND request_id=?''',
                             (principal.tenant_id, principal.member_id, action, rid)).fetchone()
        if command is None:
            return None
        if not hmac.compare_digest(command['request_hash'], fingerprint):
            raise Problem(409, '该提交标识已用于其他内容，请先查看原订单。')
        return self.owned_member_row(db, principal, command['registration_id'])

    def record_member_command(self, db, principal, action, rid, fingerprint, identifier):
        db.execute('''INSERT INTO campaign_member_requests
            (tenant_id,member_user_id,action,request_id,request_hash,registration_id,created)
            VALUES(?,?,?,?,?,?,?)''', (principal.tenant_id, principal.member_id, action,
                                      rid, fingerprint, identifier, int(self.store.clock())))

    def register_member(self, principal, event_id, data, origin):
        self.require_member_schema(principal)
        rid, name, phone, price = self.registration_data(data)
        if not isinstance(event_id, str) or not ID.fullmatch(event_id):
            raise Problem(404, '活动不存在。')
        fingerprint = self.command_hash('register', {'eventId': event_id, 'name': name,
            'phone': phone, 'consent': True, 'expectedPriceFen': price})
        now = int(self.store.clock())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            old = self.member_command(db, principal, 'register', rid, fingerprint)
            if old is not None:
                # The original quotation wins on a replay, even when the public
                # event has since closed or changed price. No second reservation.
                return self.receipt(db, old, origin, member=principal) | {'duplicate': True}
            event = self.event(db, event_id, public=True)
            if db.execute('''SELECT 1 FROM campaign_registrations
                    WHERE event_id=? AND member_tenant_id=? AND member_user_id=? AND status!='cancelled' ''',
                          (event_id, principal.tenant_id, principal.member_id)).fetchone():
                raise Problem(409, '你已有本场有效订单，请在“我的报名”查看。')
            if db.execute("SELECT 1 FROM campaign_registrations WHERE event_id=? AND phone=? AND status!='cancelled'",
                          (event_id, phone)).fetchone():
                raise Problem(409, '当前联系信息已有有效报名，请从原报名入口查看。')
            self.assert_registration_available(db, event, price, now)
            row = self.insert_registration(db, event, rid, name, phone, now, member=principal)
            self.record_member_command(db, principal, 'register', rid, fingerprint, row['id'])
            return self.receipt(db, row, origin, member=principal) | {'duplicate': False}

    def member_orders(self, principal, origin, page_no=1, page_size=20, status='all'):
        self.require_member_schema(principal)
        if (type(page_no) is not int or not 1 <= page_no <= 1_000_000
                or type(page_size) is not int or not 1 <= page_size <= 20
                or not isinstance(status, str) or status not in ('all', 'registered', 'checked_in') + PAYMENT_STATUSES):
            raise Problem(400, '筛选或页码参数无效。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            condition, args = 'member_tenant_id=? AND member_user_id=?', (principal.tenant_id, principal.member_id)
            if status in ('registered', 'checked_in'):
                condition += " AND payment_status='paid' AND status=?"
                args += (status,)
            elif status != 'all':
                condition += " AND payment_status=?"
                args += (status,)
            total = db.execute('SELECT COUNT(*) FROM campaign_registrations WHERE ' + condition, args).fetchone()[0]
            rows = db.execute('SELECT * FROM campaign_registrations WHERE ' + condition + ' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',
                              args + (page_size, (page_no - 1) * page_size)).fetchall()
            return {'list': [self.receipt(db, row, origin, member=principal, summary=True) for row in rows],
                    'total': total, 'pageNo': page_no, 'pageSize': page_size}

    def member_order(self, principal, identifier, origin):
        self.require_member_schema(principal)
        with self.store.connect() as db:
            db.execute('BEGIN')
            row = self.owned_member_row(db, principal, identifier)
            return self.receipt(db, row, origin, member=principal)

    def cancel_member(self, principal, identifier, rid, origin):
        self.require_member_schema(principal)
        rid = request_id(rid)
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise Problem(404, '订单不存在。')
        fingerprint = self.command_hash('cancel', {'id': identifier})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            old = self.member_command(db, principal, 'cancel', rid, fingerprint)
            if old is not None:
                return self.receipt(db, old, origin, member=principal) | {'duplicate': True}
            row = self.owned_member_row(db, principal, identifier)
            terminal = row['payment_status'] in ('closing', 'cancelled', 'expired', 'refund_pending', 'refunded')
            row = self.cancel_row(db, row)
            self.record_member_command(db, principal, 'cancel', rid, fingerprint, row['id'])
            return self.receipt(db, row, origin, member=principal) | {'duplicate': terminal}

    def registrations(self, event_id, status='all', page=1):
        if not isinstance(status, str) or status not in ('all', 'registered', 'checked_in') + PAYMENT_STATUSES or type(page) is not int or not 1 <= page <= 1000000:
            raise Problem(400, '报名状态或页码无效。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            event = self.event_item(db, self.event(db, event_id))
            effective_payment = "payment_status"
            grouping = db.execute('SELECT ' + effective_payment + ',COUNT(*) FROM campaign_registrations WHERE event_id=? GROUP BY 1', (event_id,)).fetchall()
            payments = {row[0]: row[1] for row in grouping}
            condition, args = 'event_id=?', (event_id,)
            if status in ('registered', 'checked_in'):
                condition += " AND payment_status='paid' AND status=?"
                args += (status,)
            elif status != 'all':
                condition += ' AND (' + effective_payment + ')=?'
                args += (status,)
            total = db.execute('SELECT COUNT(*) FROM campaign_registrations WHERE ' + condition, args).fetchone()[0]
            rows = db.execute('SELECT * FROM campaign_registrations WHERE ' + condition + ' ORDER BY created DESC,id DESC LIMIT 20 OFFSET ?', args + ((page - 1) * 20,)).fetchall()
            stats = dict.fromkeys(PAYMENT_STATUSES, 0)
            stats.update(payments)
            stats.update(registered=event['registeredCount'] - event['checkedInCount'], checkedIn=event['checkedInCount'], total=sum(payments.values()))
            return {'event': event, 'items': [self.registration_item(row, True) for row in rows], 'total': total,
                    'page': page, 'pageSize': 20, 'totalPages': max(1, (total + 19) // 20),
                    'stats': stats}

    def eligibility(self, row, event):
        row = self.effective_row(row)
        if event['status'] == 'cancelled':
            return '本场活动已取消。'
        if row['status'] == 'cancelled':
            return '这张入场券已取消。'
        if row['payment_status'] != 'paid':
            return '此订单尚未确认收款或正在退款，不能核销。'
        if row['status'] == 'checked_in':
            return '这张入场券已核销，请勿重复入场。'
        if event['status'] == 'draft':
            return '本场活动未发布。'
        if self.store.clock() < event['starts'] - 7200:
            return '核销将于活动开始前2小时开放。'
        if self.store.clock() >= event['ends']:
            return '本场活动已结束，不能核销。'
        return ''

    def preview(self, code, origin):
        with self.store.connect() as db:
            db.execute('BEGIN')
            row = self.lookup(db, code, origin, short=True)
            event = self.event(db, row['event_id'])
            reason = self.eligibility(row, event)
            return {'event': self.event_item(db, event), 'registration': self.registration_item(row, True),
                    'eligible': not reason, 'reason': reason}

    def check_in(self, code, rid, origin, username):
        rid = request_id(rid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            row = self.lookup(db, code, origin, short=True)
            event = self.event(db, row['event_id'])
            duplicate = row['status'] == 'checked_in'
            if not duplicate:
                reason = self.eligibility(row, event)
                if reason:
                    raise Problem(409, reason)
                db.execute("UPDATE campaign_registrations SET status='checked_in',checked_in=?,checked_by=?,check_request_id=? WHERE id=? AND status='registered'",
                           (int(self.store.clock()), username, rid, row['id']))
                row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (row['id'],)).fetchone()
            return {'event': self.event_item(db, event), 'registration': self.registration_item(row, True), 'duplicate': duplicate}
