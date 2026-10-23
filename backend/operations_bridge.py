"""Private, signed Yudao operations adapter; never an alternative browser login.

Only a loopback peer with the configured service key can execute the fixed action
set. Java checks the employee permission; this boundary rechecks site ownership,
body integrity, freshness and a durable one-use nonce. No user JWT/cookie crosses.
"""
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import re
import sqlite3
import time

from campaign import MAX_POSTER_BYTES
from campaign_http import CampaignAPI
from store import Problem

PATH = '/internal/leanclub/operations'
MAX_BODY = 12 * 1024 * 1024
ACTIONS = frozenset(('stats', 'feedback-list', 'feedback-status', 'comments-list', 'comments-status', 'posters-list',
                     'poster-position', 'poster-reset', 'poster-upload', 'events-list',
                     'event-save', 'registrations', 'checkin-preview', 'checkin'))


def canonical(raw, timestamp, nonce, tenant, actor):
    return '\n'.join(('LC1', 'POST', PATH, timestamp, nonce, tenant, actor,
                      hashlib.sha256(raw).hexdigest())).encode('utf-8')


class OperationsBridge:
    def __init__(self, store, origin, secret='', tenant='1', clock=time.time):
        self.store, self.origin, self.clock = store, origin, clock
        self.tenant = str(tenant)
        if not re.fullmatch(r'[1-9][0-9]{0,18}', self.tenant):
            raise ValueError('Invalid operations tenant.')
        if secret and not re.fullmatch(r'[a-f0-9]{64}', secret):
            raise ValueError('The operations key must be 32 random bytes encoded as lowercase hex.')
        # The HMAC key is the UTF-8 text of the configured hex, not decoded bytes.
        self.key = secret.encode('utf-8')
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS leanclub_bridge_nonces (
                    nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS leanclub_operations_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL,
                    actor TEXT NOT NULL, tenant TEXT NOT NULL, action TEXT NOT NULL,
                    status INTEGER NOT NULL);
            ''')

    def authenticate(self, headers, raw, peer):
        if not self.key:
            raise Problem(503, '运营服务尚未连接，请联系平台管理员。')
        try:
            if not ipaddress.ip_address(peer).is_loopback:
                raise ValueError()
            values = []
            for name in ('X-LC-Timestamp', 'X-LC-Nonce', 'X-LC-Tenant', 'X-LC-Actor', 'X-LC-Signature'):
                entries = headers.get_all(name, [])
                if len(entries) != 1:
                    raise ValueError()
                values.append(entries[0])
            timestamp, nonce, tenant, actor, signature = values
            if (not re.fullmatch(r'[0-9]{10}', timestamp)
                    or not re.fullmatch(r'[a-f0-9]{32}', nonce)
                    or tenant != self.tenant
                    or not re.fullmatch(r'[1-9][0-9]{0,18}', actor)
                    or not re.fullmatch(r'[a-f0-9]{64}', signature)
                    or abs(int(self.clock()) - int(timestamp)) > 60):
                raise ValueError()
            expected = hmac.new(self.key, canonical(raw, timestamp, nonce, tenant, actor), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
        except ValueError:
            raise Problem(403, '运营服务身份校验失败。')
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM leanclub_bridge_nonces WHERE expires < ?', (int(self.clock()),))
                db.execute('INSERT INTO leanclub_bridge_nonces VALUES(?,?)', (nonce, int(self.clock()) + 121))
        except sqlite3.IntegrityError:
            raise Problem(409, '运营请求已处理，请刷新后重试。')
        return actor

    @staticmethod
    def parse(raw):
        def unique(pairs):
            output = {}
            for key, value in pairs:
                if key in output:
                    raise ValueError()
                output[key] = value
            return output
        def invalid(_):
            raise ValueError()
        try:
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
            if (not isinstance(data, dict) or set(data) != {'action', 'params'}
                    or not isinstance(data['action'], str) or data['action'] not in ACTIONS
                    or not isinstance(data['params'], dict)):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise Problem(400, '运营操作或参数格式无效。')
        return data['action'], data['params']

    @staticmethod
    def fields(params, required, optional=()):
        if not set(required).issubset(params) or set(params) - set(required) - set(optional):
            raise Problem(400, '运营操作参数不正确。')

    @staticmethod
    def page(params):
        page = params.get('page', 1)
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise Problem(400, '页码无效。')
        return page

    @staticmethod
    def public_registration_fields(result):
        # Daily operations need the masked contact, not the raw phone number.
        for item in result.get('items', []):
            item.pop('phone', None)
        if isinstance(result.get('registration'), dict):
            result['registration'].pop('phone', None)
        return result

    def dispatch(self, action, params, actor):
        if action in ('stats', 'posters-list', 'events-list'):
            self.fields(params, ())
            if action == 'stats':
                return self.store.stats()
            if action == 'posters-list':
                return {'cities': self.store.cities()}
            return self.store.events.list(admin=True)
        if action == 'feedback-list':
            self.fields(params, (), ('status', 'page'))
            return self.store.feedback.list(params.get('status', 'all'), self.page(params))
        if action == 'feedback-status':
            self.fields(params, ('id', 'status'))
            return self.store.feedback.set_status(params['id'], params['status'])
        if action == 'comments-list':
            self.fields(params, (), ('status', 'page'))
            return self.store.comments.list(params.get('status', 'all'), self.page(params))
        if action == 'comments-status':
            self.fields(params, ('id', 'status'))
            return self.store.comments.set_status(params['id'], params['status'])
        if action in ('poster-position', 'poster-reset', 'poster-upload'):
            required = {'poster-position': ('city', 'x', 'y'), 'poster-reset': ('city',),
                        'poster-upload': ('city', 'contentType', 'dataBase64')}[action]
            self.fields(params, required)
            if params['city'] not in ('shenzhen', 'zhuhai', 'guangzhou'):
                raise Problem(400, '城市无效。')
            if action == 'poster-position':
                city = self.store.position(params['city'], params['x'], params['y'])
            elif action == 'poster-reset':
                city = self.store.reset_poster(params['city'])
            else:
                if params['contentType'] not in ('image/png', 'image/jpeg', 'image/webp'):
                    raise Problem(415, '请上传 PNG、JPG 或 WebP 静态图片。')
                try:
                    if not isinstance(params['dataBase64'], str):
                        raise ValueError()
                    image = base64.b64decode(params['dataBase64'], validate=True)
                except (ValueError, binascii.Error):
                    raise Problem(400, '图片内容无效。')
                if len(image) > MAX_POSTER_BYTES:
                    raise Problem(413, '图片不能超过 8 MiB。')
                city = self.store.replace_poster(params['city'], image, params['contentType'])
            return {'city': city}
        if action == 'event-save':
            values = params.copy()
            event_id = values.pop('id', None)
            expected = values.pop('expectedVersion', None)
            if event_id is not None and (not isinstance(event_id, str)
                    or not re.fullmatch(r'[a-f0-9]{32}', event_id)
                    or type(expected) is not int or expected < 1):
                raise Problem(400, '请重新载入活动后再保存。')
            if event_id is None and expected is not None:
                raise Problem(400, '新活动不应指定版本。')
            return self.store.events.save(values, event_id, expected_version=expected)
        if action == 'registrations':
            self.fields(params, ('eventId',), ('status', 'page'))
            return self.public_registration_fields(self.store.events.registrations(params['eventId'], params.get('status', 'all'), self.page(params)))
        if action == 'checkin-preview':
            self.fields(params, ('code',))
            return self.public_registration_fields(self.store.events.preview(params['code'], self.origin))
        if action == 'checkin':
            self.fields(params, ('code', 'requestId'))
            return self.public_registration_fields(self.store.events.check_in(params['code'], params['requestId'], self.origin, 'yudao:' + actor))
        raise Problem(400, '不支持此运营操作。')

    def execute(self, headers, raw, peer):
        actor = self.authenticate(headers, raw, peer)
        action, params = self.parse(raw)
        status = 500
        try:
            result = self.dispatch(action, params, actor)
            status = 200
            return result
        except Problem as exc:
            status = exc.status
            raise
        finally:
            # Actor and operation metadata only; no personal data or ticket bodies.
            with self.store.connect() as db:
                db.execute('INSERT INTO leanclub_operations_audit(created,actor,tenant,action,status) VALUES(?,?,?,?,?)',
                           (int(self.clock()), actor, self.tenant, action, status))

    def post(self, handler):
        if handler.path != PATH:
            raise Problem(404, '接口不存在。')
        if not self.key:
            raise Problem(503, '运营服务尚未连接，请联系平台管理员。')
        if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise Problem(415, '请求须为 JSON 格式。')
        raw = CampaignAPI.read_body(handler, MAX_BODY)
        result = self.execute(handler.headers, raw, handler.client_address[0])
        return handler.send_json(200, result)
