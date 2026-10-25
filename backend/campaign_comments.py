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
THREAD_PREVIEW_SIZE = 3
COMMENT_SELECT = '''SELECT c.*,p.nickname AS parent_nickname,p.status AS parent_status,
    p.content AS parent_content,p.reply_content AS parent_reply_content
    FROM campaign_comments c LEFT JOIN campaign_comments p ON p.id=c.parent_id'''


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
    if not isinstance(data, dict) or set(data) - {'requestId', 'content', 'nickname', 'city', 'publishConsent', 'parentId', 'replyToOfficial'}:
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
    normalized_name = ''.join(char for char in unicodedata.normalize('NFKC', nickname).casefold()
                              if not char.isspace() and unicodedata.category(char) != 'Cf')
    if normalized_name in ('薄肌学院', 'leanschool'):
        raise Problem(400, '这个昵称用于主办方回复，请换一个昵称。')
    city = data.get('city')
    if city is not None and (not isinstance(city, str) or city not in CITIES):
        raise Problem(400, '请选择深圳、珠海、广州，或不指定城市。')
    parent_id, official = data.get('parentId'), data.get('replyToOfficial', False)
    if parent_id is not None and (not isinstance(parent_id, str) or not COMMENT_ID.fullmatch(parent_id)):
        raise Problem(400, '回复对象无效，请重新选择。')
    if type(official) is not bool or official and parent_id is None:
        raise Problem(400, '回复对象无效，请重新选择。')
    return request_id.lower(), content, nickname, city, parent_id, official


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
            # Additive migration: existing comments and their visibility stay intact.
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(campaign_comments)')}
            for name, definition in (
                ('reply_content', 'TEXT CHECK(reply_content IS NULL OR length(reply_content) BETWEEN 1 AND 500)'),
                ('reply_updated', 'INTEGER'),
                ('reply_revision', 'INTEGER NOT NULL DEFAULT 0'),
                ('root_id', 'TEXT REFERENCES campaign_comments(id)'),
                ('parent_id', 'TEXT REFERENCES campaign_comments(id)'),
                ('reply_to_official', 'INTEGER NOT NULL DEFAULT 0 CHECK(reply_to_official IN (0,1))'),
            ):
                if name not in columns:
                    db.execute('ALTER TABLE campaign_comments ADD COLUMN ' + name + ' ' + definition)
            db.execute('UPDATE campaign_comments SET root_id=id WHERE root_id IS NULL')
            db.execute('''CREATE INDEX IF NOT EXISTS campaign_comments_roots
                ON campaign_comments(parent_id,status,created DESC,id DESC)''')
            db.execute('''CREATE INDEX IF NOT EXISTS campaign_comments_thread
                ON campaign_comments(root_id,status,created,id) WHERE parent_id IS NOT NULL''')

    @staticmethod
    def item(row, admin=False):
        result = {key: row[key] for key in ('id', 'content', 'nickname', 'city')}
        result['createdAt'] = iso(row['created'])
        result['reply'] = ({'content': row['reply_content'], 'updatedAt': iso(row['reply_updated'])}
                           if row['reply_content'] is not None else None)
        result.update(rootId=row['root_id'], parentId=row['parent_id'],
                      replyToOfficial=bool(row['reply_to_official']), replyToName=None)
        if row['parent_id'] is not None:
            result['replyToName'] = ('已隐藏的留言' if row['parent_status'] != 'visible' else
                                     '薄肌学院' if row['reply_to_official'] else row['parent_nickname'])
        if admin:
            result['status'] = row['status']
            result['replyRevision'] = row['reply_revision']
            parent_content = row['parent_reply_content'] if row['reply_to_official'] else row['parent_content']
            result['parentContent'] = parent_content[:160] if parent_content else None
        return result

    @staticmethod
    def _row(db, comment_id):
        return db.execute(COMMENT_SELECT + ' WHERE c.id=?', (comment_id,)).fetchone()

    def submit(self, visitor, ip, data, db=None):
        request_id, content, nickname, city, parent_id, official = validate(data)
        now = int(self.store.clock())
        ip_key = self.store.ip_hash(ip)
        own_connection = db is None
        with self.store.connect() if own_connection else nullcontext(db) as db:
            if own_connection:
                db.execute('BEGIN IMMEDIATE')
            old = db.execute(COMMENT_SELECT + ' WHERE c.visitor=? AND c.request_id=?',
                             (visitor, request_id)).fetchone()
            if old:
                if (old['content'], old['nickname'], old['city'], old['parent_id'], bool(old['reply_to_official'])) != (content, nickname, city, parent_id, official):
                    raise Problem(409, '这次评论已保存。修改内容后，请作为新评论提交。')
                return {'ok': True, 'item': self.item(old) | {'status': old['status']}, 'duplicate': True}
            parent = self._row(db, parent_id) if parent_id is not None else None
            root_id = None
            if parent_id is not None:
                if parent is None or parent['status'] != 'visible':
                    raise Problem(404, '这条留言已不可回复，请刷新后重试。')
                root_id = parent['root_id']
                root = self._row(db, root_id)
                if root is None or root['status'] != 'visible' or root['parent_id'] is not None:
                    raise Problem(404, '这条讨论已不可回复，请刷新后重试。')
                if official and parent['reply_content'] is None:
                    raise Problem(409, '这条主办方回复已撤回，请刷新后重试。')
            db.execute('DELETE FROM campaign_rate_events WHERE stamp<?', (now - 86400,))
            quotas = ((visitor, 'comment-replies-visitor', 12, 60), (ip_key, 'comment-replies-ip', 60, 300)) if parent_id else (
                (visitor, 'comments-visitor', 3, 10), (ip_key, 'comments-ip', 10, 30))
            for key, kind, short_limit, day_limit in quotas:
                counts = db.execute('''SELECT COUNT(*),COALESCE(SUM(stamp>?),0) FROM campaign_rate_events
                    WHERE key_hash=? AND kind=? AND stamp>?''',
                                    (now - 600, key, kind, now - 86400)).fetchone()
                if counts[0] >= day_limit or counts[1] >= short_limit:
                    raise Problem(429, '评论较频繁，请稍后再分享。已发布的评论无需重复提交。')
            comment_id = uuid.uuid4().hex
            db.execute('''INSERT INTO campaign_comments(id,visitor,request_id,content,nickname,city,publish_consent,created,
                root_id,parent_id,reply_to_official) VALUES(?,?,?,?,?,?,1,?,?,?,?)''',
                (comment_id, visitor, request_id, content, nickname, city, now, root_id or comment_id, parent_id, int(official)))
            self.store.notifications.enqueue(db, 'comment', city, None, now,
                                             snapshot={'content': content, 'nickname': nickname})
            for key, kind, _, _ in quotas:
                db.execute('INSERT INTO campaign_rate_events VALUES(?,?,?)', (key, kind, now))
            row = self._row(db, comment_id)
            return {'ok': True, 'item': self.item(row) | {'status': row['status']}, 'duplicate': False}

    @staticmethod
    def _pagination(limit, cursor):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Problem(400, '每次可读取1–50条评论。')
        if cursor is not None:
            match = CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
            if match is None:
                raise Problem(400, '评论分页标识无效，请刷新后重试。')
            return int(match[1]), match[2]
        return None

    def _thread_page(self, db, root_id, limit, position):
        condition = " WHERE c.root_id=? AND c.parent_id IS NOT NULL AND c.status='visible'"
        args = (root_id,)
        total = db.execute('SELECT COUNT(*) FROM campaign_comments c' + condition, args).fetchone()[0]
        if position is not None:
            stamp, item_id = position
            condition += ' AND (c.created>? OR (c.created=? AND c.id>?))'
            args += (stamp, stamp, item_id)
        rows = db.execute(COMMENT_SELECT + condition + ' ORDER BY c.created,c.id LIMIT ?', args + (limit + 1,)).fetchall()
        more, rows = len(rows) > limit, rows[:limit]
        cursor = '%s.%s' % (rows[-1]['created'], rows[-1]['id']) if more else None
        return {'items': [self.item(row) for row in rows], 'total': total, 'hasMore': more, 'nextCursor': cursor}

    def public_replies(self, root_id, limit=20, cursor=None):
        if not isinstance(root_id, str) or not COMMENT_ID.fullmatch(root_id):
            raise Problem(400, '评论标识无效。')
        position = self._pagination(limit, cursor)
        with self.store.connect() as db:
            db.execute('BEGIN')
            root = self._row(db, root_id)
            if root is None or root['status'] != 'visible' or root['parent_id'] is not None:
                raise Problem(404, '这条讨论已不可查看。')
            return self._thread_page(db, root_id, limit, position)

    def public_list(self, limit=20, cursor=None):
        position = self._pagination(limit, cursor)
        condition, args = " WHERE c.status='visible' AND c.parent_id IS NULL", ()
        if position is not None:
            stamp, item_id = position
            condition += ' AND (c.created<? OR (c.created=? AND c.id<?))'
            args = (stamp, stamp, item_id)
        with self.store.connect() as db:
            db.execute('BEGIN')
            total = db.execute("SELECT COUNT(*) FROM campaign_comments WHERE status='visible' AND parent_id IS NULL").fetchone()[0]
            rows = db.execute(COMMENT_SELECT + condition + ' ORDER BY c.created DESC,c.id DESC LIMIT ?',
                              args + (limit + 1,)).fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            items = []
            for row in rows:
                thread = self._thread_page(db, row['id'], THREAD_PREVIEW_SIZE, None)
                items.append(self.item(row) | {'replies': thread['items'], 'replyCount': thread['total'],
                                               'repliesNextCursor': thread['nextCursor']})
            next_cursor = '%s.%s' % (rows[-1]['created'], rows[-1]['id']) if more else None
            return {'items': items, 'total': total, 'hasMore': more, 'nextCursor': next_cursor}

    def list(self, status='all', page=1, reply='all'):
        if not isinstance(status, str) or status not in STATUSES | {'all'}:
            raise Problem(400, '评论状态无效。')
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise Problem(400, '页码须为1–1000000之间的整数。')
        if not isinstance(reply, str) or reply not in ('all', 'unreplied', 'replied'):
            raise Problem(400, '回复筛选无效。')
        condition, args = (' WHERE 1=1', ()) if status == 'all' else (' WHERE c.status=?', (status,))
        if reply != 'all':
            condition += ' AND c.reply_content IS ' + ('NULL' if reply == 'unreplied' else 'NOT NULL')
        with self.store.connect() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM campaign_comments c' + condition, args).fetchone()[0]
            rows = db.execute(COMMENT_SELECT + condition + ' ORDER BY c.created DESC,c.id DESC LIMIT ? OFFSET ?',
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
            row = self._row(db, comment_id)
        return {'item': self.item(row, admin=True)}

    def set_reply(self, comment_id, data):
        if not isinstance(comment_id, str) or not COMMENT_ID.fullmatch(comment_id):
            raise Problem(400, '评论标识无效。')
        if (not isinstance(data, dict) or set(data) != {'content', 'revision'} or
                type(data['revision']) is not int or not 0 <= data['revision'] <= 2**53 - 1):
            raise Problem(400, '回复格式无效，请刷新后重试。')
        # null is an explicit withdrawal; an accidentally empty editor cannot remove a reply.
        content = data['content']
        if content is not None:
            content = text_value(content, 1, 500, '回复请控制在1–500字。', multiline=True)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, comment_id)
            if row is None:
                raise Problem(404, '这条评论不存在。')
            # Retrying a saved response after a network timeout is harmless.
            if row['reply_content'] == content:
                return {'item': self.item(row, admin=True)}
            if row['reply_revision'] != data['revision']:
                raise Problem(409, '回复已被更新，请重新载入后编辑。')
            db.execute('''UPDATE campaign_comments SET reply_content=?,reply_updated=?,
                reply_revision=reply_revision+1 WHERE id=?''', (content, int(self.store.clock()), comment_id))
            row = self._row(db, comment_id)
        return {'item': self.item(row, admin=True)}
