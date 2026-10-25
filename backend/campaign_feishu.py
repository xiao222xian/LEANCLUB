"""Private Feishu configuration and a durable, asynchronous campaign outbox.

Aggregate vote counts, public comments and private feedback are sent. Credentials never
enter public responses, outbox records, or exception messages. Outbox rows share
the submission transaction, so a network failure cannot undo a submission. Delivery is at least once:
after an ambiguous network timeout, Feishu may receive the same notification twice.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.client import HTTPSConnection
import json
import re
import threading
import uuid

from store import Problem


CITIES = (('shenzhen', '深圳'), ('zhuhai', '珠海'), ('guangzhou', '广州'))
WEBHOOK = re.compile(r'https://open\.feishu\.cn(?::443)?/open-apis/bot/v2/hook/[A-Za-z0-9_-]{10,128}')
MAX_ATTEMPTS = 5
RETRY_SECONDS = (5, 30, 120, 600)
SHANGHAI = timezone(timedelta(hours=8))
NOTIFICATION_KINDS = frozenset(('new', 'test', 'summary', 'feedback', 'comment'))
MANUAL_KINDS = frozenset(('test', 'summary'))


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None


def validate_webhook(value):
    if not isinstance(value, str) or not WEBHOOK.fullmatch(value.strip()):
        raise Problem(400, '请输入飞书群机器人的 HTTPS Webhook 地址。')
    return value.strip().replace('open.feishu.cn:443/', 'open.feishu.cn/')


class DeliveryFailure(Exception):
    """A safe, user-facing message, never the upstream body or URL."""
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def build_payload(event, signing_secret='', now=None):
    if event['kind'] not in NOTIFICATION_KINDS:
        raise ValueError('Unsupported notification kind.')
    snapshot = json.loads(event['snapshot'])
    city_name = dict(CITIES).get(event['city'])
    if event['kind'] in ('feedback', 'comment'):
        public_comment = event['kind'] == 'comment'
        category = snapshot['nickname'] if public_comment else {'activity': '活动玩法', 'training': '训练交流', 'other': '其他想法'}[snapshot['category']]
        moment = datetime.fromtimestamp(event['created'], SHANGHAI).strftime('%Y.%m.%d %H:%M:%S')
        def plain(content, size='normal'):
            # Feishu JSON 2.0 plain-text div; never interpret user text as markup.
            # https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/plain-text
            return {'tag': 'div', 'text': {'tag': 'plain_text', 'content': content, 'text_size': size}}
        elements = [plain(category + (' · ' + city_name if city_name else ''), 'heading-2'),
                    plain(snapshot['content']), {'tag': 'hr'}, plain(moment + ' · 北京时间', 'notation')]
        payload = {'msg_type': 'interactive', 'card': {
            'schema': '2.0', 'config': {'update_multi': True},
            'header': {'title': {'tag': 'plain_text', 'content': 'LEAN SCHOOL · 薄肌学院'},
                       'subtitle': {'tag': 'plain_text', 'content': '收到一条公开留言' if public_comment else '收到一条新想法'}, 'template': 'turquoise'},
            'body': {'direction': 'vertical', 'padding': '20px 16px 16px 16px',
                     'vertical_spacing': '12px', 'elements': elements}}}
        return sign_payload(payload, signing_secret, now)
    titles = {'new': '新的一票', 'summary': '当前票数汇总', 'test': '连接测试'}
    heading = '%s +1' % city_name if event['kind'] == 'new' else titles[event['kind']]
    total = sum(snapshot.values())

    def text(content, size='normal'):
        return {'tag': 'markdown', 'content': content, 'text_align': 'center', 'text_size': size}

    columns = [{'tag': 'column', 'width': 'weighted', 'weight': 1,
                'vertical_spacing': '4px', 'elements': [
                    text(name), text('**%s**' % format(snapshot[city], ','), 'heading-1'), text('票', 'notation')]}
               for city, name in CITIES]
    moment = datetime.fromtimestamp(event['created'], SHANGHAI).strftime('%Y.%m.%d %H:%M:%S')
    payload = {'msg_type': 'interactive', 'card': {
        'schema': '2.0',
        'config': {'update_multi': True},
        'header': {'title': {'tag': 'plain_text', 'content': 'LEAN SCHOOL · 薄肌学院'},
                   'subtitle': {'tag': 'plain_text', 'content': titles[event['kind']]},
                   'template': 'turquoise' if event['kind'] == 'new' else 'grey'},
        'body': {'direction': 'vertical', 'padding': '20px 16px 16px 16px', 'vertical_spacing': '12px',
                 'elements': [text('**%s**' % heading, 'heading-2'),
                              text('**%s**' % format(total, ','), 'heading-0'),
                              text('累计有效票', 'notation'),
                              {'tag': 'hr'},
                              {'tag': 'column_set', 'flex_mode': 'none', 'horizontal_spacing': '12px', 'columns': columns},
                              {'tag': 'hr'}, text(moment + ' · 北京时间', 'notation')]}}}
    return sign_payload(payload, signing_secret, now)


def sign_payload(payload, signing_secret, now):
    if signing_secret:
        stamp = str(int(now))
        key = (stamp + '\n' + signing_secret).encode('utf-8')
        payload.update(timestamp=stamp, sign=base64.b64encode(hmac.new(key, b'', hashlib.sha256).digest()).decode('ascii'))
    return payload


def deliver(webhook, payload):
    """Fixed HTTPS host, no redirects, proxies, cookies, or raw error logging."""
    webhook = validate_webhook(webhook)
    connection = HTTPSConnection('open.feishu.cn', timeout=5)
    try:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        connection.request('POST', webhook[len('https://open.feishu.cn'):], body=raw,
                           headers={'Content-Type': 'application/json; charset=utf-8', 'User-Agent': 'LeanSchool/1.0'})
        response = connection.getresponse()
        body = response.read(65537)
        if not 200 <= response.status < 300:
            if 300 <= response.status < 400:
                raise DeliveryFailure('飞书返回重定向，已停止发送；请检查 Webhook。', retryable=False)
            raise DeliveryFailure('飞书服务响应异常（HTTP %s）。' % response.status,
                                  retryable=response.status == 429 or response.status >= 500)
        if len(body) > 65536:
            raise DeliveryFailure('飞书返回内容异常，请稍后重试。')
        try:
            result = json.loads(body)
            code = result.get('code', result.get('StatusCode')) if isinstance(result, dict) else None
        except (ValueError, UnicodeError):
            raise DeliveryFailure('飞书返回内容异常，请稍后重试。')
        if type(code) is not int:
            raise DeliveryFailure('未收到飞书成功确认，请稍后重试。')
        if code != 0:
            messages = {19021: '签名校验失败，请检查签名密钥与服务器时间。',
                        19022: '飞书拒绝了服务器 IP，请检查机器人 IP 白名单。',
                        19024: '飞书关键词校验失败，请将机器人关键词设为 LEAN SCHOOL 或薄肌学院。',
                        11232: '飞书发送频率受限，稍后自动重试。'}
            message = messages.get(code, '飞书未接受消息（错误码 %s）。' % code)
            raise DeliveryFailure(message, retryable=code not in (19021, 19022, 19024))
    except DeliveryFailure:
        raise
    except Exception:
        # URLs / credentials may appear in library exception text. Do not expose it.
        raise DeliveryFailure('暂时无法连接飞书，稍后自动重试。') from None
    finally:
        connection.close()


class FeishuNotifications:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_feishu_config (
                    id INTEGER PRIMARY KEY CHECK(id=1), webhook TEXT NOT NULL DEFAULT '',
                    signing_secret TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0, updated INTEGER,
                    next_send_at REAL NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO campaign_feishu_config(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS campaign_feishu_outbox (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, city TEXT, previous_city TEXT,
                    snapshot TEXT NOT NULL, created INTEGER NOT NULL, revision INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    available REAL NOT NULL, lease_until REAL, lease_token TEXT,
                    error TEXT, sent INTEGER);
                CREATE INDEX IF NOT EXISTS campaign_feishu_queue
                    ON campaign_feishu_outbox(status,available,created);
            ''')
            self.cancel_unsupported(db)

    @staticmethod
    def cancel_unsupported(db):
        # Older releases queued change notifications. Never resume them after
        # upgrading, including an interrupted lease or an administrator retry.
        db.execute("""UPDATE campaign_feishu_outbox SET status='cancelled',error=NULL,lease_until=NULL,lease_token=NULL
            WHERE kind NOT IN ('new','test','summary','feedback','comment') AND status IN ('pending','retrying','failed','sending')""")

    @staticmethod
    def enqueue(db, kind, city, previous_city, now, snapshot=None):
        if kind not in NOTIFICATION_KINDS:
            return None
        config = db.execute('SELECT * FROM campaign_feishu_config WHERE id=1').fetchone()
        if not config['webhook'] or (kind not in MANUAL_KINDS and not config['enabled']):
            return None
        if kind == 'feedback':
            if not isinstance(snapshot, dict) or set(snapshot) != {'content', 'category'}:
                raise ValueError('Feedback notifications require the submitted text and category.')
        elif kind == 'comment':
            if not isinstance(snapshot, dict) or set(snapshot) != {'content', 'nickname'}:
                raise ValueError('Comment notifications require the submitted text and nickname.')
        else:
            snapshot = {city_id: 0 for city_id, _ in CITIES}
            snapshot.update({row['city']: row['n'] for row in db.execute('SELECT city,COUNT(*) AS n FROM campaign_votes GROUP BY city')})
        event_id = uuid.uuid4().hex
        db.execute('''INSERT INTO campaign_feishu_outbox
            (id,kind,city,previous_city,snapshot,created,revision,available) VALUES(?,?,?,?,?,?,?,?)''',
                   (event_id, kind, city, previous_city, json.dumps(snapshot, ensure_ascii=False), now, config['revision'], now))
        # Keep delivery history useful but bounded in time. Unsent events survive.
        db.execute("DELETE FROM campaign_feishu_outbox WHERE status IN ('sent','cancelled') AND created<?", (now - 30 * 86400,))
        return event_id

    def settings(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            config = db.execute('SELECT * FROM campaign_feishu_config WHERE id=1').fetchone()
            last = db.execute('SELECT * FROM campaign_feishu_outbox ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
            counts = {row['status']: row['n'] for row in db.execute('SELECT status,COUNT(*) AS n FROM campaign_feishu_outbox GROUP BY status')}
        delivery = None if last is None else {
            'id': last['id'], 'kind': last['kind'], 'status': last['status'],
            'createdAt': timestamp(last['created']), 'sentAt': timestamp(last['sent']),
            'attempts': last['attempts'], 'error': last['error']}
        return {'enabled': bool(config['enabled']), 'configured': bool(config['webhook']),
                'webhookMasked': 'https://open.feishu.cn/open-apis/bot/v2/hook/••••••••' if config['webhook'] else '',
                'hasSigningSecret': bool(config['signing_secret']), 'updatedAt': timestamp(config['updated']),
                'lastDelivery': delivery,
                'pendingCount': sum(counts.get(status, 0) for status in ('pending', 'sending', 'retrying')),
                'failedCount': counts.get('failed', 0)}

    def save(self, data):
        allowed = {'enabled', 'webhook', 'signingSecret', 'clearSigningSecret', 'clearWebhook'}
        if set(data) - allowed or type(data.get('enabled')) is not bool:
            raise Problem(400, '推送设置格式无效。')
        for key in ('clearSigningSecret', 'clearWebhook'):
            if key in data and type(data[key]) is not bool:
                raise Problem(400, '推送设置格式无效。')
        for key in ('webhook', 'signingSecret'):
            if key in data and (not isinstance(data[key], str) or len(data[key]) > 512):
                raise Problem(400, 'Webhook 或签名密钥格式无效。')
        webhook = data.get('webhook', '').strip()
        secret = data.get('signingSecret', '').strip()
        if webhook:
            webhook = validate_webhook(webhook)
        if secret and (not 8 <= len(secret) <= 256 or any(ord(char) <= 32 or ord(char) >= 127 for char in secret)):
            raise Problem(400, '请输入有效的机器人签名密钥。')
        if ((data.get('clearWebhook') and (webhook or secret or data['enabled']))
                or (data.get('clearSigningSecret') and secret)):
            raise Problem(400, '清除与保存设置不能同时进行。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM campaign_feishu_config WHERE id=1').fetchone()
            saved_webhook = '' if data.get('clearWebhook') else webhook or current['webhook']
            saved_secret = '' if data.get('clearWebhook') or data.get('clearSigningSecret') else secret or current['signing_secret']
            # A different robot cannot accidentally inherit the previous robot's signing key.
            if webhook and webhook != current['webhook'] and not secret:
                saved_secret = ''
            if data['enabled'] and not saved_webhook:
                raise Problem(400, '先保存飞书机器人 Webhook，再开启推送。')
            changed = (saved_webhook, saved_secret, int(data['enabled'])) != (current['webhook'], current['signing_secret'], current['enabled'])
            if changed:
                db.execute('''UPDATE campaign_feishu_config SET webhook=?,signing_secret=?,enabled=?,
                    revision=revision+1,updated=? WHERE id=1''',
                           (saved_webhook, saved_secret, int(data['enabled']), int(self.store.clock())))
                # Old events must never cross into a newly configured group or resume after opting out.
                db.execute("UPDATE campaign_feishu_outbox SET status='cancelled',error=NULL WHERE status IN ('pending','retrying','failed')")
        return self.settings()

    def queue_test(self):
        return self.queue_manual('test')

    def queue_summary(self):
        return self.queue_manual('summary')

    def queue_manual(self, kind):
        if kind not in MANUAL_KINDS:
            raise ValueError('Unsupported manual notification kind.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            event_id = self.enqueue(db, kind, None, None, int(self.store.clock()))
            if event_id is None:
                raise Problem(400, '请先保存飞书机器人 Webhook。')
        return {'queued': True, 'notificationId': event_id, 'settings': self.settings()}

    def retry_failed(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.cancel_unsupported(db)
            config = db.execute('SELECT * FROM campaign_feishu_config WHERE id=1').fetchone()
            if not config['webhook']:
                raise Problem(400, '请先保存飞书机器人 Webhook。')
            cursor = db.execute('''UPDATE campaign_feishu_outbox SET status='pending',attempts=0,error=NULL,available=?
                WHERE status='failed' AND revision=? AND kind IN ('new','test','summary','feedback','comment')
                AND (?=1 OR kind IN ('test','summary'))''',
                                (self.store.clock(), config['revision'], config['enabled']))
            count = cursor.rowcount
        return {'queuedCount': count, 'settings': self.settings()}

    def claim(self):
        now = self.store.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.cancel_unsupported(db)
            config = dict(db.execute('SELECT * FROM campaign_feishu_config WHERE id=1').fetchone())
            # Recover work after a terminated process. A lease owner alone may complete its delivery.
            db.execute("""UPDATE campaign_feishu_outbox SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'retrying' END,
                error='上次发送未完成，正在恢复。',lease_token=NULL WHERE status='sending' AND lease_until<?""", (MAX_ATTEMPTS, now))
            db.execute("""UPDATE campaign_feishu_outbox SET status='cancelled',error=NULL
                WHERE status IN ('pending','retrying','failed') AND revision<>?""", (config['revision'],))
            if not config['webhook'] or config['next_send_at'] > now:
                return None
            event = db.execute("""SELECT * FROM campaign_feishu_outbox WHERE status IN ('pending','retrying')
                AND available<=? AND revision=? AND kind IN ('new','test','summary','feedback','comment')
                AND (?=1 OR kind IN ('test','summary'))
                ORDER BY created,rowid LIMIT 1""", (now, config['revision'], config['enabled'])).fetchone()
            if event is None:
                return None
            event = dict(event)
            event['lease_token'] = uuid.uuid4().hex
            event['attempts'] += 1
            db.execute("""UPDATE campaign_feishu_outbox SET status='sending',attempts=?,lease_until=?,lease_token=? WHERE id=?""",
                       (event['attempts'], now + 30, event['lease_token'], event['id']))
            # 80/minute across workers, below the documented 100/minute bot limit.
            db.execute('UPDATE campaign_feishu_config SET next_send_at=? WHERE id=1', (now + .75,))
            return event, config

    def deliver_one(self, sender=deliver):
        claimed = self.claim()
        if claimed is None:
            return False
        event, config = claimed
        failure = None
        try:
            sender(config['webhook'], build_payload(event, config['signing_secret'], self.store.clock()))
        except DeliveryFailure as exc:
            failure = exc
        except Exception:
            failure = DeliveryFailure('发送未完成，稍后自动重试。')
        now = self.store.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revision = db.execute('SELECT revision FROM campaign_feishu_config WHERE id=1').fetchone()[0]
            if failure is None:
                status, error, available, sent = 'sent', None, now, int(now)
            elif revision != event['revision']:
                status, error, available, sent = 'cancelled', None, now, None
            else:
                retrying = failure.retryable and event['attempts'] < MAX_ATTEMPTS
                status = 'retrying' if retrying else 'failed'
                error, sent = str(failure), None
                available = now + RETRY_SECONDS[event['attempts'] - 1] if retrying else now
            db.execute('''UPDATE campaign_feishu_outbox SET status=?,error=?,available=?,sent=?,lease_until=NULL,lease_token=NULL
                WHERE id=? AND status='sending' AND lease_token=?''',
                       (status, error, available, sent, event['id'], event['lease_token']))
        return True


class FeishuWorker:
    def __init__(self, notifications):
        self.notifications = notifications
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name='campaign-feishu', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=6)

    def run(self):
        while not self.stopped.is_set():
            try:
                self.notifications.deliver_one()
            except Exception:
                # Database contention or shutdown is recoverable from the durable queue.
                # Never print exceptions that might contain a credential.
                pass
            self.stopped.wait(.75)
