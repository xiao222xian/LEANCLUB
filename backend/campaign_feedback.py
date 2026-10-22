"""Anonymous campaign feedback with transactional deduplication and quotas."""
from contextlib import nullcontext
from datetime import datetime, timezone
import re
import unicodedata
import uuid

from store import Problem

CATEGORIES = frozenset(('activity', 'training', 'other'))
CITIES = frozenset(('shenzhen', 'zhuhai', 'guangzhou'))
STATUSES = frozenset(('new', 'read'))
FEEDBACK_ID = re.compile(r'[a-f0-9]{32}')
PAGE_SIZE = 20


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def validate(data):
    if not isinstance(data, dict) or set(data) - {'requestId', 'content', 'category', 'city'}:
        raise Problem(400, '意见格式无效，请仅提交意见、类型与城市。')
    request_id = data.get('requestId')
    try:
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id.lower():
            raise ValueError()
    except (ValueError, AttributeError):
        raise Problem(400, '提交标识无效，请刷新后重试。')
    content = data.get('content')
    if not isinstance(content, str):
        raise Problem(400, '请写下5–1000字的想法。')
    content = content.replace('\r\n', '\n')
    # A textarea newline is meaningful; other controls and hidden formatting are
    # rejected before trimming so a hidden prefix cannot evade validation.
    if any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') and char not in ('\n', '\u200c', '\u200d') for char in content):
        raise Problem(400, '意见含不可见控制字符，请删除后重试。')
    content = content.strip()
    if not 5 <= len(content) <= 1000 or sum(unicodedata.category(char)[0] in 'LNPS' for char in content) < 5:
        raise Problem(400, '请写下5–1000字的想法。')
    category, city = data.get('category'), data.get('city')
    if not isinstance(category, str) or category not in CATEGORIES:
        raise Problem(400, '请选择活动玩法、训练交流或其他想法。')
    if city is not None and (not isinstance(city, str) or city not in CITIES):
        raise Problem(400, '请选择深圳、珠海、广州，或不指定城市。')
    return request_id.lower(), content, category, city


class CampaignFeedback:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_feedback (
                    id TEXT PRIMARY KEY,
                    visitor TEXT NOT NULL REFERENCES campaign_visitors(token_hash),
                    request_id TEXT NOT NULL,
                    content TEXT NOT NULL CHECK(length(content) BETWEEN 5 AND 1000),
                    category TEXT NOT NULL CHECK(category IN ('activity','training','other')),
                    city TEXT CHECK(city IS NULL OR city IN ('shenzhen','zhuhai','guangzhou')),
                    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','read')),
                    created INTEGER NOT NULL,
                    UNIQUE(visitor,request_id));
                CREATE INDEX IF NOT EXISTS campaign_feedback_order ON campaign_feedback(created DESC,id DESC);
                CREATE INDEX IF NOT EXISTS campaign_feedback_status ON campaign_feedback(status,created DESC,id DESC);
            ''')

    @staticmethod
    def item(row):
        return {key: row[key] for key in ('id', 'content', 'category', 'city', 'status')} | {'createdAt': iso(row['created'])}

    @staticmethod
    def receipt(row, duplicate):
        return {'ok': True, 'id': row['id'], 'createdAt': iso(row['created']), 'duplicate': duplicate}

    def submit(self, visitor, ip, data, db=None):
        request_id, content, category, city = validate(data)
        now = int(self.store.clock())
        # HMAC from the existing private database secret; raw IP is never stored.
        ip_key = self.store.ip_hash(ip)
        own_connection = db is None
        with self.store.connect() if own_connection else nullcontext(db) as db:
            if own_connection:
                db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM campaign_feedback WHERE visitor=? AND request_id=?',
                             (visitor, request_id)).fetchone()
            if old:
                if (old['content'], old['category'], old['city']) != (content, category, city):
                    raise Problem(409, '这次提交已保存。修改意见后，请作为新意见提交。')
                return self.receipt(old, True)
            db.execute('DELETE FROM campaign_rate_events WHERE stamp<?', (now - 86400,))
            for key, kind, short_limit, day_limit in ((visitor, 'feedback-visitor', 3, 10),
                                                      (ip_key, 'feedback-ip', 10, 30)):
                counts = db.execute('''SELECT COUNT(*),COALESCE(SUM(stamp>?),0) FROM campaign_rate_events
                    WHERE key_hash=? AND kind=? AND stamp>?''',
                                    (now - 600, key, kind, now - 86400)).fetchone()
                if counts[0] >= day_limit or counts[1] >= short_limit:
                    raise Problem(429, '提交较频繁，请稍后再分享。之前的意见已保存，无需重复提交。')
            feedback_id = uuid.uuid4().hex
            db.execute('''INSERT INTO campaign_feedback(id,visitor,request_id,content,category,city,created)
                VALUES(?,?,?,?,?,?,?)''', (feedback_id, visitor, request_id, content, category, city, now))
            for key, kind in ((visitor, 'feedback-visitor'), (ip_key, 'feedback-ip')):
                db.execute('INSERT INTO campaign_rate_events VALUES(?,?,?)', (key, kind, now))
            self.store.notifications.enqueue(db, 'feedback', city, None, now,
                                             snapshot={'content': content, 'category': category})
            return self.receipt({'id': feedback_id, 'created': now}, False)

    def list(self, status='all', page=1):
        if not isinstance(status, str) or status not in STATUSES | {'all'}:
            raise Problem(400, '意见状态无效。')
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise Problem(400, '页码须为1–1000000之间的整数。')
        condition, args = ('', ()) if status == 'all' else (' WHERE status=?', (status,))
        with self.store.connect() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM campaign_feedback' + condition, args).fetchone()[0]
            unread = db.execute("SELECT COUNT(*) FROM campaign_feedback WHERE status='new'").fetchone()[0]
            rows = db.execute('SELECT * FROM campaign_feedback' + condition + ' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',
                              args + (PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
        return {'items': [self.item(row) for row in rows], 'total': total, 'unread': unread,
                'page': page, 'pageSize': PAGE_SIZE, 'totalPages': max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}

    def set_status(self, feedback_id, status):
        if not isinstance(feedback_id, str) or not FEEDBACK_ID.fullmatch(feedback_id):
            raise Problem(400, '意见标识无效。')
        if not isinstance(status, str) or status not in STATUSES:
            raise Problem(400, '意见状态无效。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM campaign_feedback WHERE id=?', (feedback_id,)).fetchone() is None:
                raise Problem(404, '这条意见不存在。')
            db.execute('UPDATE campaign_feedback SET status=? WHERE id=?', (status, feedback_id))
            row = db.execute('SELECT * FROM campaign_feedback WHERE id=?', (feedback_id,)).fetchone()
            unread = db.execute("SELECT COUNT(*) FROM campaign_feedback WHERE status='new'").fetchone()[0]
        return {'item': self.item(row), 'unread': unread}
