"""Opt-in public discussion, separate from private campaign feedback."""
from contextlib import nullcontext
import re
import unicodedata
import uuid

from campaign_feedback import CITIES, iso
from store import Problem

STATUSES = frozenset(('visible', 'hidden'))
COMMENT_ID = re.compile(r'[a-f0-9]{32}')
CURSOR = re.compile(r'([0-9]{1,12})\.([a-f0-9]{32})')
PAGE_SIZE = 20


def text_value(value, minimum, maximum, label, multiline=False):
    if not isinstance(value, str):
        raise Problem(400, label)
    value = value.replace('\r\n', '\n')
    allowed_controls = ('\u200c', '\u200d', '\n') if multiline else ('\u200c', '\u200d')
    if any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') and char not in allowed_controls for char in value):
        raise Problem(400, '文字含不可见控制字符，请删除后重试。')
    value = value.strip()
    if not minimum <= len(value) <= maximum or sum(unicodedata.category(char)[0] in 'LNPS' for char in value) < minimum:
        raise Problem(400, label)
    return value


def validate(data):
    if not isinstance(data, dict) or set(data) - {'requestId', 'content', 'nickname', 'city', 'publishConsent'}:
        raise Problem(400, '评论格式无效。')
    if data.get('publishConsent') is not True:
        raise Problem(400, '请确认将这条评论公开展示在评论区和弹幕中。')
    request_id = data.get('requestId')
    try:
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id.lower():
            raise ValueError()
    except (ValueError, AttributeError):
        raise Problem(400, '提交标识无效，请刷新后重试。')
    content = text_value(data.get('content'), 2, 240, '请写下2–240字的评论。', multiline=True)
    nickname = text_value(data.get('nickname', ''), 0, 20, '昵称请控制在20字以内。') or '薄肌同好'
    if not any(unicodedata.category(char)[0] in 'LNPS' for char in nickname):
        raise Problem(400, '请填写可见的昵称，或留空。')
    city = data.get('city')
    if city is not None and (not isinstance(city, str) or city not in CITIES):
        raise Problem(400, '请选择深圳、珠海、广州，或不指定城市。')
    return request_id.lower(), content, nickname, city


class CampaignComments:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            # Deliberately no import from campaign_feedback: those submissions
            # were private and did not grant public-display consent.
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_comments (
                    id TEXT PRIMARY KEY,
                    visitor TEXT NOT NULL REFERENCES campaign_visitors(token_hash),
                    request_id TEXT NOT NULL,
                    content TEXT NOT NULL CHECK(length(content) BETWEEN 2 AND 240),
                    nickname TEXT NOT NULL CHECK(length(nickname) BETWEEN 1 AND 20),
                    city TEXT CHECK(city IS NULL OR city IN ('shenzhen','zhuhai','guangzhou')),
                    publish_consent INTEGER NOT NULL CHECK(publish_consent=1),
                    status TEXT NOT NULL DEFAULT 'visible' CHECK(status IN ('visible','hidden')),
                    created INTEGER NOT NULL,
                    UNIQUE(visitor,request_id));
                CREATE INDEX IF NOT EXISTS campaign_comments_order ON campaign_comments(created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS campaign_comments_status ON campaign_comments(status,created DESC,id DESC);
            ''')

    @staticmethod
    def item(row, admin=False):
        result = {key: row[key] for key in ('id', 'content', 'nickname', 'city')}
        result['createdAt'] = iso(row['created'])
        if admin:
            result['status'] = row['status']
        return result

    def submit(self, visitor, ip, data, db=None):
        request_id, content, nickname, city = validate(data)
        now = int(self.store.clock())
        ip_key = self.store.ip_hash(ip)
        own_connection = db is None
        with self.store.connect() if own_connection else nullcontext(db) as db:
            if own_connection:
                db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM campaign_comments WHERE visitor=? AND request_id=?',
                             (visitor, request_id)).fetchone()
            if old:
                if (old['content'], old['nickname'], old['city']) != (content, nickname, city):
                    raise Problem(409, '这次评论已保存。修改内容后，请作为新评论提交。')
                return {'ok': True, 'item': self.item(old, admin=True), 'duplicate': True}
            db.execute('DELETE FROM campaign_rate_events WHERE stamp<?', (now - 86400,))
            for key, kind, short_limit, day_limit in ((visitor, 'comments-visitor', 3, 10),
                                                     (ip_key, 'comments-ip', 10, 30)):
                counts = db.execute('''SELECT COUNT(*),COALESCE(SUM(stamp>?),0) FROM campaign_rate_events
                    WHERE key_hash=? AND kind=? AND stamp>?''',
                                    (now - 600, key, kind, now - 86400)).fetchone()
                if counts[0] >= day_limit or counts[1] >= short_limit:
                    raise Problem(429, '评论较频繁，请稍后再分享。已发布的评论无需重复提交。')
            comment_id = uuid.uuid4().hex
            db.execute('''INSERT INTO campaign_comments(id,visitor,request_id,content,nickname,city,publish_consent,created)
                VALUES(?,?,?,?,?,?,1,?)''', (comment_id, visitor, request_id, content, nickname, city, now))
            self.store.notifications.enqueue(db, 'comment', city, None, now,
                                             snapshot={'content': content, 'nickname': nickname})
            for key, kind in ((visitor, 'comments-visitor'), (ip_key, 'comments-ip')):
                db.execute('INSERT INTO campaign_rate_events VALUES(?,?,?)', (key, kind, now))
            row = db.execute('SELECT * FROM campaign_comments WHERE id=?', (comment_id,)).fetchone()
            return {'ok': True, 'item': self.item(row, admin=True), 'duplicate': False}

    def public_list(self, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Problem(400, '每次可读取1–50条评论。')
        condition, args = " WHERE status='visible'", ()
        if cursor is not None:
            match = CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
            if match is None:
                raise Problem(400, '评论分页标识无效，请刷新后重试。')
            stamp, item_id = int(match[1]), match[2]
            condition += ' AND (created<? OR (created=? AND id<?))'
            args = (stamp, stamp, item_id)
        with self.store.connect() as db:
            db.execute('BEGIN')
            total = db.execute("SELECT COUNT(*) FROM campaign_comments WHERE status='visible'").fetchone()[0]
            rows = db.execute('SELECT * FROM campaign_comments' + condition + ' ORDER BY created DESC,id DESC LIMIT ?',
                              args + (limit + 1,)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = '%s.%s' % (rows[-1]['created'], rows[-1]['id']) if more else None
        return {'items': [self.item(row) for row in rows], 'total': total, 'hasMore': more, 'nextCursor': next_cursor}

    def list(self, status='all', page=1):
        if not isinstance(status, str) or status not in STATUSES | {'all'}:
            raise Problem(400, '评论状态无效。')
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise Problem(400, '页码须为1–1000000之间的整数。')
        condition, args = ('', ()) if status == 'all' else (' WHERE status=?', (status,))
        with self.store.connect() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM campaign_comments' + condition, args).fetchone()[0]
            rows = db.execute('SELECT * FROM campaign_comments' + condition + ' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',
                              args + (PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
        return {'items': [self.item(row, admin=True) for row in rows], 'total': total,
                'page': page, 'pageSize': PAGE_SIZE, 'totalPages': max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}

    def set_status(self, comment_id, status):
        if not isinstance(comment_id, str) or not COMMENT_ID.fullmatch(comment_id):
            raise Problem(400, '评论标识无效。')
        if not isinstance(status, str) or status not in STATUSES:
            raise Problem(400, '评论状态无效。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM campaign_comments WHERE id=?', (comment_id,)).fetchone() is None:
                raise Problem(404, '这条评论不存在。')
            db.execute('UPDATE campaign_comments SET status=? WHERE id=?', (status, comment_id))
            row = db.execute('SELECT * FROM campaign_comments WHERE id=?', (comment_id,)).fetchone()
        return {'item': self.item(row, admin=True)}
