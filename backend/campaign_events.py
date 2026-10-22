"""Paid event order reservation and authenticated, atomic admission.

Ticket secrets are derived from a database-local key; no bearer token is stored.
All capacity, cancellation and check-in decisions use a write transaction.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import io
import re
import secrets
import unicodedata
from urllib.parse import parse_qs, urlsplit
import uuid

import qrcode
from qrcode.image.pil import PilImage

from store import Problem

CITIES = frozenset(('shenzhen', 'zhuhai', 'guangzhou'))
STATUSES = frozenset(('draft', 'published', 'closed', 'cancelled'))
ID = re.compile(r'[a-f0-9]{32}')
TICKET = re.compile(r'([a-f0-9]{32})\.([A-Za-z0-9_-]{43})')
NUMBER = re.compile(r'LC-[A-Z2-7]{12}')
FIELDS = {'title', 'subtitle', 'description', 'city', 'venue', 'address', 'startsAt',
          'endsAt', 'registrationClosesAt', 'capacity', 'status', 'posterCity', 'priceFen'}
PAYMENT_STATUSES = ('pending_payment', 'paid', 'expired', 'cancelled', 'refund_pending', 'refunded')


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
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_events (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, subtitle TEXT NOT NULL,
                    description TEXT NOT NULL, city TEXT NOT NULL, venue TEXT NOT NULL,
                    address TEXT NOT NULL, starts INTEGER NOT NULL, ends INTEGER NOT NULL,
                    closes INTEGER NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>0),
                    status TEXT NOT NULL CHECK(status IN ('draft','published','closed','cancelled')),
                    poster_city TEXT NOT NULL, created INTEGER NOT NULL, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_registrations (
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES campaign_events(id),
                    visitor TEXT NOT NULL REFERENCES campaign_visitors(token_hash),
                    request_id TEXT NOT NULL, ticket_number TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, phone TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('registered','checked_in','cancelled')),
                    created INTEGER NOT NULL, checked_in INTEGER, cancelled INTEGER,
                    checked_by TEXT, check_request_id TEXT,
                    UNIQUE(visitor,request_id));
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
            COALESCE(SUM(payment_status='pending_payment' AND payment_expires>?),0),
            COALESCE(SUM(payment_status='refund_pending'),0)
            FROM campaign_registrations WHERE event_id=? AND status!='cancelled'""", (int(self.store.clock()), row['id'])).fetchone()
        item = {key: row[key] for key in ('id', 'title', 'subtitle', 'description', 'city', 'venue', 'address', 'capacity', 'status')}
        remaining = max(0, row['capacity'] - counts[0] - counts[2] - counts[3])
        payment_ready = bool(self.payment_adapter is not None and self.payment_adapter.ready)
        return item | {'startsAt': iso(row['starts']), 'endsAt': iso(row['ends']),
                       'registrationClosesAt': iso(row['closes']), 'posterCity': row['poster_city'],
                       'createdAt': iso(row['created']), 'updatedAt': iso(row['updated']),
                       'registeredCount': counts[0], 'checkedInCount': counts[1], 'pendingCount': counts[2],
                       'refundPendingCount': counts[3], 'remaining': remaining,
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

    def save(self, data, event_id=None):
        if not isinstance(data, dict) or not data or set(data) - FIELDS:
            raise Problem(400, '活动字段无效。')
        now = int(self.store.clock())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.event(db, event_id) if event_id else None
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
                if values['capacity'] < values['registeredCount'] + values['pendingCount'] + values['refundPendingCount']:
                    raise Problem(409, '人数上限不能少于当前已占用名额。')
                if old['status'] == 'cancelled' and values['status'] != 'cancelled':
                    raise Problem(409, '已取消活动不能重新启用，请创建新活动。')
                if values['status'] == 'draft' and db.execute('SELECT 1 FROM campaign_registrations WHERE event_id=? LIMIT 1', (event_id,)).fetchone():
                    raise Problem(409, '已有报名的活动不能退回草稿，可关闭报名或取消活动。')
                db.execute('''UPDATE campaign_events SET title=?,subtitle=?,description=?,city=?,venue=?,address=?,
                    starts=?,ends=?,closes=?,capacity=?,status=?,poster_city=?,updated=?,price_fen=? WHERE id=?''',
                           tuple(values[k] for k in ('title', 'subtitle', 'description', 'city', 'venue', 'address')) +
                           (starts, ends, closes, values['capacity'], values['status'], values['posterCity'], now, values['priceFen'], event_id))
                if values['status'] == 'cancelled' and old['status'] != 'cancelled':
                    db.execute("UPDATE campaign_registrations SET payment_status='cancelled',status='cancelled',cancelled=? WHERE event_id=? AND payment_status='pending_payment'", (now, event_id))
                    db.execute("UPDATE campaign_registrations SET payment_status='refund_pending',cancelled=? WHERE event_id=? AND payment_status='paid'", (now, event_id))
            else:
                event_id = uuid.uuid4().hex
                db.execute('INSERT INTO campaign_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
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
        row = dict(row)
        if row['payment_status'] == 'pending_payment' and row['payment_expires'] <= self.store.clock():
            row.update(payment_status='expired', status='cancelled')
        return row

    def expire(self, db):
        db.execute("UPDATE campaign_registrations SET payment_status='expired',status='cancelled' WHERE payment_status='pending_payment' AND payment_expires<=?", (int(self.store.clock()),))

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

    def receipt(self, db, row, origin, visitor=None, qr=True):
        row = self.effective_row(row)
        event = self.event(db, row['event_id'])
        token = self.token(row['id'])
        check_url = origin + '/check-in#ticket=' + token
        paid = row['payment_status'] == 'paid' and event['status'] != 'cancelled'
        result = {'event': self.event_item(db, event), 'registration': self.registration_item(row),
                  'order': {'id': row['id'], 'status': row['payment_status'], 'amountFen': row['amount_fen'],
                            'currency': 'CNY', 'expiresAt': iso(row['payment_expires']), 'paidAt': iso(row['paid_at'])},
                  'orderToken': token, 'orderUrl': origin + '/ticket#ticket=' + token,
                  'ticketToken': token if paid else None, 'ticketUrl': origin + '/ticket#ticket=' + token,
                  'checkInUrl': check_url if paid else None, 'qrDataUrl': None,
                  'canCancel': row['visitor'] == visitor and row['status'] == 'registered' and row['payment_status'] in ('pending_payment', 'paid') and self.store.clock() < event['starts']}
        if qr and paid:
            image = qrcode.make(check_url, image_factory=PilImage, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
            output = io.BytesIO()
            image.save(output, format='PNG')
            result['qrDataUrl'] = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
        return result

    def register(self, visitor, event_id, data, origin):
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
        now = int(self.store.clock())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            event = self.event(db, event_id, public=True)
            old = db.execute('SELECT * FROM campaign_registrations WHERE visitor=? AND request_id=?', (visitor, rid)).fetchone()
            if old:
                if (old['event_id'], old['name'], old['phone']) != (event_id, name, phone):
                    raise Problem(409, '该提交已完成，修改报名请使用新的提交标识。')
                return self.receipt(db, old, origin, visitor) | {'duplicate': True}
            old = db.execute("SELECT * FROM campaign_registrations WHERE event_id=? AND phone=? AND status!='cancelled'", (event_id, phone)).fetchone()
            if old:
                if old['visitor'] == visitor:
                    return self.receipt(db, old, origin, visitor) | {'duplicate': True}
                raise Problem(409, '该手机号已报名，请使用原报名浏览器查看入场券。')
            if event['status'] != 'published' or event['price_fen'] < 1 or now >= event['closes']:
                raise Problem(409, '本场活动已停止报名。')
            if self.payment_adapter is None or not self.payment_adapter.ready:
                raise Problem(409, '报名收款尚未开通，请稍后再来。')
            if expected_price != event['price_fen']:
                raise Problem(409, '报名价格已调整，请刷新活动并重新确认价格。')
            if self.event_item(db, event)['remaining'] <= 0:
                raise Problem(409, '本场名额已满，期待下一次见面。')
            identifier = uuid.uuid4().hex
            number = 'LC-' + base64.b32encode(secrets.token_bytes(8)).decode()[:12]
            db.execute('''INSERT INTO campaign_registrations(id,event_id,visitor,request_id,ticket_number,name,phone,status,created,
                payment_status,amount_fen,payment_expires)
                VALUES(?,?,?,?,?,?,?,'registered',?,'pending_payment',?,?)''',
                       (identifier, event_id, visitor, rid, number, name, phone, now, event['price_fen'], min(now + 1800, event['closes'])))
            row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (identifier,)).fetchone()
            return self.receipt(db, row, origin, visitor) | {'duplicate': False}

    def ticket(self, token, origin, visitor=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self.receipt(db, self.lookup(db, token, origin), origin, visitor)

    def mine(self, visitor, origin):
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT * FROM campaign_registrations WHERE visitor=? ORDER BY created DESC,id DESC LIMIT 100', (visitor,)).fetchall()
            return {'tickets': [self.receipt(db, row, origin, visitor) for row in rows]}

    def cancel(self, token, origin, visitor):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.expire(db)
            row = self.lookup(db, token, origin)
            event = self.event(db, row['event_id'])
            if row['visitor'] != visitor:
                raise Problem(403, '请使用原报名浏览器取消报名。')
            if row['payment_status'] not in ('cancelled', 'expired', 'refund_pending', 'refunded'):
                if row['status'] == 'checked_in' or self.store.clock() >= event['starts']:
                    raise Problem(409, '活动已开始或入场券已核销，不能取消。')
                if row['payment_status'] == 'paid':
                    db.execute("UPDATE campaign_registrations SET payment_status='refund_pending',cancelled=? WHERE id=?", (int(self.store.clock()), row['id']))
                else:
                    db.execute("UPDATE campaign_registrations SET status='cancelled',payment_status='cancelled',cancelled=? WHERE id=?", (int(self.store.clock()), row['id']))
                row = db.execute('SELECT * FROM campaign_registrations WHERE id=?', (row['id'],)).fetchone()
            return self.receipt(db, row, origin, visitor)

    def registrations(self, event_id, status='all', page=1):
        if not isinstance(status, str) or status not in ('all', 'registered', 'checked_in') + PAYMENT_STATUSES or type(page) is not int or not 1 <= page <= 1000000:
            raise Problem(400, '报名状态或页码无效。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            event = self.event_item(db, self.event(db, event_id))
            effective_payment = "CASE WHEN payment_status='pending_payment' AND payment_expires<=? THEN 'expired' ELSE payment_status END"
            grouping = db.execute('SELECT ' + effective_payment + ',COUNT(*) FROM campaign_registrations WHERE event_id=? GROUP BY 1', (int(self.store.clock()), event_id)).fetchall()
            payments = {row[0]: row[1] for row in grouping}
            condition, args = 'event_id=?', (event_id,)
            if status in ('registered', 'checked_in'):
                condition += " AND payment_status='paid' AND status=?"
                args += (status,)
            elif status != 'all':
                condition += ' AND (' + effective_payment + ')=?'
                args += (int(self.store.clock()), status)
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
