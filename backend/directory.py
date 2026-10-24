"""Reviewed directory submissions; published records keep the existing clubs authority."""
import base64
import json
import http.client
import os
import re
import secrets
import time

from club_management import ClubManagement
from store import Store, Problem


class Directory:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            existing = {r[1] for r in db.execute('PRAGMA table_info(club_directory_meta)')}
            for name, definition in {
                'kind': "TEXT NOT NULL DEFAULT 'group'", 'country': "TEXT NOT NULL DEFAULT ''",
                'city': "TEXT NOT NULL DEFAULT ''", 'description': "TEXT NOT NULL DEFAULT ''",
                'school_name': "TEXT NOT NULL DEFAULT ''", 'school_ref': "TEXT NOT NULL DEFAULT ''",
                'contact_public': 'INTEGER NOT NULL DEFAULT 1', 'profile_public': 'INTEGER NOT NULL DEFAULT 1',
                'qr_label': "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE club_directory_meta ADD COLUMN {name} {definition}')
            db.executescript('''CREATE TABLE IF NOT EXISTS directory_drafts (
                id TEXT PRIMARY KEY, edit_hash TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                status TEXT NOT NULL, version INTEGER NOT NULL, base_revision INTEGER NOT NULL,
                note TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, updated INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS directory_draft_status ON directory_drafts(status,updated);''')

    @staticmethod
    def validate(data):
        if not isinstance(data, dict):
            raise Problem(400, '资料格式无效。')
        result = {}
        for field, limit in [('name', 100), ('country', 60), ('city', 100), ('description', 600),
                             ('schoolName', 100), ('contact', 300), ('en', 200), ('alias', 200), ('qrLabel', 40)]:
            value = data.get(field, '')
            if not isinstance(value, str) or len(value.strip()) > limit or '\x00' in value:
                raise Problem(400, '请检查资料文字的长度。')
            result[field] = value.strip()
        if not result['name'] or not result['country']:
            raise Problem(400, '请填写名称和国家 / 地区。')
        result['kind'] = data.get('kind')
        if result['kind'] not in ('school', 'group', 'person'):
            raise Problem(400, '请选择资料类型。')
        if result['kind'] != 'school' and not result['contact']:
            raise Problem(400, '请留下一个联系渠道。')
        for field in ('contactPublic', 'profilePublic'):
            if type(data.get(field)) is not bool:
                raise Problem(400, '请确认资料的公开范围。')
            result[field] = data[field]
        result['count'] = data.get('count', 0)
        if type(result['count']) is not int or not 0 <= result['count'] <= 100000:
            raise Problem(400, '人数请填写0至100000之间的整数。')
        result['schoolId'] = data.get('schoolId')
        if result['schoolId'] is not None and (type(result['schoolId']) is not int or result['schoolId'] < 1):
            raise Problem(400, '学校关联无效。')
        if result['kind'] != 'group':
            result['count'] = 0
        if result['kind'] == 'school':
            result['schoolName'], result['schoolId'] = '', None
        qr = data.get('qr', '')
        if not isinstance(qr, str) or len(qr) > 2800000:
            raise Problem(400, '二维码图片请控制在2MB以内。')
        if qr:
            match = re.fullmatch(r'data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)', qr)
            if not match:
                raise Problem(400, '二维码请选择PNG或JPG图片。')
            try:
                qr = ClubManagement.image(base64.b64decode(match[2], validate=True), match[1])
            except ValueError:
                raise Problem(400, '二维码图片无法读取。')
        result['qr'] = qr
        return result

    @staticmethod
    def key(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{30,100}', value):
            raise Problem(403, '管理链接无效，请使用提交时保存的链接。')
        return Store.key_hash(value)

    @staticmethod
    def _record(db, record_id):
        r = db.execute('''SELECT c.*,m.kind,m.country,m.city,m.description,m.school_name,
            m.school_ref,m.contact_public,m.profile_public,m.qr_label,m.en,m.alias,m.region,
            m.school_id,m.visible FROM clubs c LEFT JOIN club_directory_meta m ON m.club_id=c.id
            WHERE c.id=?''', (record_id,)).fetchone()
        if not r:
            return None
        payload = dict(kind=r['kind'] or 'group', name=r['name'], country=r['country'] or r['region'] or '',
                       city=r['city'] or '', description=r['description'] or '', schoolName=r['school_name'] or '',
                       schoolId=r['school_id'], contact=r['contact'], count=r['count'], qr=r['qr'],
                       qrLabel=r['qr_label'] or '', en=r['en'] or '', alias=r['alias'] or '',
                       contactPublic=r['contact_public'] != 0, profilePublic=r['profile_public'] != 0)
        return dict(id=r['id'], payload=payload, revision=r['revision'], updated=r['updated'],
                    visible=r['visible'] != 0, edit_hash=r['edit_hash'], schoolRef=r['school_ref'] or '')

    def _detail(self, db, record_id):
        draft = db.execute('SELECT * FROM directory_drafts WHERE id=?', (record_id,)).fetchone()
        record = self._record(db, record_id)
        if not draft and not record:
            raise Problem(404, '这份资料不存在。')
        return dict(id=record_id, profile=json.loads(draft['payload']) if draft else record['payload'],
                    revision=draft['version'] if draft else record['revision'],
                    status=draft['status'] if draft else ('published' if record['visible'] else 'hidden'),
                    note=draft['note'] if draft else '', updated=draft['updated'] if draft else record['updated'],
                    visible=bool(record and record['visible'] and record['payload']['profilePublic']))

    def manage(self, key):
        hashed = self.key(key)
        with self.store.connect() as db:
            row = db.execute('SELECT id FROM directory_drafts WHERE edit_hash=?', (hashed,)).fetchone()
            if not row:
                row = db.execute('SELECT id FROM clubs WHERE edit_hash=?', (hashed,)).fetchone()
            if not row:
                raise Problem(404, '还没有提交资料，或管理链接已失效。')
            return self._detail(db, row['id'])

    def submit(self, data):
        hashed = self.key(data.get('editKey'))
        payload = self.validate(data.get('profile'))
        revision = data.get('revision')
        if type(revision) is not int or revision < 0:
            raise Problem(400, '资料版本无效。')
        # A catalogue number from a browser is only a hint; bind it after a
        # bounded, loopback-only lookup. A catalogue outage must not block free text.
        if payload['schoolId'] is not None:
            with self.store.connect() as db:
                linked = db.execute('SELECT id FROM clubs WHERE edit_hash=?', (hashed,)).fetchone()
                trusted = self._record(db, linked['id'])['payload'] if linked else None
            if not trusted or (trusted['schoolId'], trusted['schoolName']) != (payload['schoolId'], payload['schoolName']):
                payload['schoolId'] = self.resolve_school(payload['schoolId'], payload['schoolName'])
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        now = int(time.time())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            draft = db.execute('SELECT * FROM directory_drafts WHERE edit_hash=?', (hashed,)).fetchone()
            old = db.execute('SELECT id,revision FROM clubs WHERE edit_hash=?', (hashed,)).fetchone()
            if draft and draft['payload'] == serialized and revision == draft['version'] - 1:
                return self._detail(db, draft['id'])
            expected = draft['version'] if draft else old['revision'] if old else 0
            if revision != expected:
                raise Problem(409, '资料已更新，请重新打开管理链接后再修改。')
            record_id = draft['id'] if draft else old['id'] if old else 'entry-' + secrets.token_hex(12)
            base_revision = old['revision'] if old else 0
            # Withdrawal of consent takes effect immediately, even while edits await review.
            if old and (not payload['contactPublic'] or not payload['profilePublic']):
                db.execute('INSERT OR IGNORE INTO club_directory_meta(club_id) VALUES(?)', (record_id,))
                db.execute('''UPDATE club_directory_meta SET contact_public=MIN(contact_public,?),
                    profile_public=MIN(profile_public,?),visible=MIN(visible,?) WHERE club_id=?''',
                           (int(payload['contactPublic']), int(payload['profilePublic']), int(payload['profilePublic']), record_id))
                if not payload['contactPublic']:
                    # Keep the legacy public table safe even if application code is rolled back.
                    db.execute("UPDATE clubs SET contact='',qr='',revision=revision+1,updated=? WHERE id=?", (now, record_id))
                    base_revision += 1
            db.execute('''INSERT INTO directory_drafts(id,edit_hash,payload,status,version,base_revision,created,updated)
                VALUES(?,?,?,'pending',?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
                status='pending',version=excluded.version,base_revision=excluded.base_revision,note='',updated=excluded.updated''',
                       (record_id, hashed, serialized, expected + 1, base_revision, now, now))
            self._audit(db, record_id, 'owner', 'submit', expected + 1)
            return self._detail(db, record_id)

    @staticmethod
    def resolve_school(school_id, name):
        if not name:
            return None
        connection = http.client.HTTPConnection('127.0.0.1', int(os.environ.get('LEANCLUB_YUDAO_PORT', '48080')), timeout=3)
        try:
            connection.request('GET', '/app-api/leanclub/school/get?id=' + str(school_id),
                               headers={'tenant-id': os.environ.get('LEANCLUB_PUBLIC_TENANT_ID', '1')})
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                return None
            data = json.loads(raw)
            school = data.get('data') or {}
            if data.get('code') == 0 and school.get('id') == school_id and Store.name_key(school.get('name', '')) == Store.name_key(name):
                return school_id
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        return None

    @staticmethod
    def _audit(db, record_id, actor, action, revision):
        db.execute('INSERT INTO club_management_audit(club_id,actor,created,action,fields,revision) VALUES(?,?,?,?,?,?)',
                   (record_id, actor, int(time.time()), action, '[]', revision))

    def _publish(self, db, record_id, payload, hashed):
        now = int(time.time())
        old = db.execute('SELECT revision FROM clubs WHERE id=?', (record_id,)).fetchone()
        revision = old['revision'] + 1 if old else 1
        # Identity is the stable record ID, never the display name. Homonyms are valid.
        db.execute('''INSERT INTO clubs(id,name,name_key,contact,count,qr,edit_hash,updated,revision)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,name_key=excluded.name_key,
            contact=excluded.contact,count=excluded.count,qr=excluded.qr,updated=excluded.updated,revision=excluded.revision''',
                   (record_id, payload['name'], 'directory:' + record_id,
                    payload['contact'] if payload['contactPublic'] else '', payload['count'],
                    payload['qr'] if payload['contactPublic'] else '', hashed, now, revision))
        db.execute('''INSERT INTO club_directory_meta(club_id,en,region,alias,school_id,visible,kind,country,city,
            description,school_name,contact_public,profile_public,qr_label) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(club_id) DO UPDATE SET en=excluded.en,region=excluded.region,alias=excluded.alias,
            school_id=excluded.school_id,visible=excluded.visible,kind=excluded.kind,country=excluded.country,
            city=excluded.city,description=excluded.description,school_name=excluded.school_name,
            contact_public=excluded.contact_public,profile_public=excluded.profile_public,qr_label=excluded.qr_label''',
                   (record_id, payload['en'], payload['country'], payload['alias'], payload['schoolId'], int(payload['profilePublic']),
                    payload['kind'], payload['country'], payload['city'], payload['description'], payload['schoolName'],
                    int(payload['contactPublic']), int(payload['profilePublic']), payload['qrLabel']))
        if payload['schoolName'] and not payload['schoolId'] and payload['kind'] == 'group' and payload['profilePublic']:
            school = db.execute('''SELECT c.id FROM clubs c JOIN club_directory_meta m ON m.club_id=c.id
                WHERE m.kind='school' AND c.name=? AND m.country=? AND m.city=? AND m.visible=1 AND m.profile_public=1 LIMIT 1''',
                                (payload['schoolName'], payload['country'], payload['city'])).fetchone()
            school_id = school['id'] if school else 'entry-' + secrets.token_hex(12)
            if not school:
                institution = dict(payload, kind='school', name=payload['schoolName'], schoolName='', contact='',
                                   count=0, qr='', description='', contactPublic=False, profilePublic=True, en='', alias='')
                self._publish(db, school_id, institution, Store.key_hash(secrets.token_urlsafe(32)))
                self._audit(db, school_id, 'review', 'linked-school', 1)
            db.execute('UPDATE club_directory_meta SET school_ref=? WHERE club_id=?', (school_id, record_id))
        else:
            db.execute("UPDATE club_directory_meta SET school_ref='' WHERE club_id=?", (record_id,))
        return revision

    def review(self, record_id, data, actor):
        if data.get('action') not in ('publish', 'changes', 'hide'):
            raise Problem(400, '请选择审核操作。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._detail(db, record_id)
            if type(data.get('revision')) is not int or data['revision'] != current['revision']:
                raise Problem(409, '资料已更新，请重新打开后再处理。')
            draft = db.execute('SELECT * FROM directory_drafts WHERE id=?', (record_id,)).fetchone()
            old = self._record(db, record_id)
            note = data.get('note', '')
            if not isinstance(note, str) or len(note.strip()) > 300:
                raise Problem(400, '审核意见请控制在300字以内。')
            action = data['action']
            if action == 'changes' and not note.strip():
                raise Problem(400, '请填写需要补充的内容。')
            payload = self.validate(data.get('profile', current['profile'])) if action == 'publish' else current['profile']
            for field in ('contactPublic', 'profilePublic'):
                if not current['profile'][field] and payload[field]:
                    raise Problem(400, '不能替提交人扩大公开范围，请让本人修改。')
            if action == 'publish' and draft and old and old['revision'] != draft['base_revision']:
                raise Problem(409, '已发布资料另有修改，请让提交人重新确认后再审核。')
            hashed = draft['edit_hash'] if draft else old['edit_hash']
            base = old['revision'] if old else 0
            if action == 'publish':
                base = self._publish(db, record_id, payload, hashed)
            elif action == 'hide':
                db.execute('UPDATE club_directory_meta SET visible=0 WHERE club_id=?', (record_id,))
                # Legacy records might not have metadata yet.
                if old:
                    db.execute('INSERT OR IGNORE INTO club_directory_meta(club_id,visible) VALUES(?,0)', (record_id,))
            version = current['revision'] + 1
            status = {'publish': 'published', 'hide': 'hidden', 'changes': 'changes'}[action]
            now = int(time.time())
            db.execute('''INSERT INTO directory_drafts(id,edit_hash,payload,status,version,base_revision,note,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status=excluded.status,
                version=excluded.version,base_revision=excluded.base_revision,note=excluded.note,updated=excluded.updated''',
                       (record_id, hashed, json.dumps(payload, ensure_ascii=False, sort_keys=True), status,
                        version, base, note.strip(), now, now))
            self._audit(db, record_id, actor, action, version)
            return self._detail(db, record_id)

    def listing(self, private=False):
        with self.store.connect() as db:
            if private:
                ids = [r[0] for r in db.execute('SELECT id FROM clubs UNION SELECT id FROM directory_drafts')]
                result = []
                for record_id in ids:
                    detail = self._detail(db, record_id)
                    p = detail.pop('profile')
                    result.append(dict(detail, **{k: p[k] for k in ('kind', 'name', 'country', 'city', 'count', 'schoolName')}))
                return sorted(result, key=lambda x: (x['status'] != 'pending', -x['updated'], x['name']))
            rows = db.execute('''SELECT c.id,c.name,c.count,c.updated,COALESCE(m.kind,'group') kind,
                COALESCE(NULLIF(m.country,''),m.region,'') country,COALESCE(m.city,'') city,
                COALESCE(m.en,'') en,COALESCE(m.alias,'') alias,COALESCE(m.school_name,'') schoolName,
                COALESCE(m.school_ref,'') schoolRef,m.school_id schoolId FROM clubs c
                LEFT JOIN club_directory_meta m ON m.club_id=c.id
                WHERE COALESCE(m.visible,1)=1 AND COALESCE(m.profile_public,1)=1 ORDER BY c.count DESC,c.name,c.id''')
            return [dict(r) for r in rows]

    def create(self, data, actor):
        payload = self.validate(data.get('profile'))
        created = self.submit({'editKey': secrets.token_urlsafe(32), 'revision': 0, 'profile': payload})
        return self.review(created['id'], {'revision': created['revision'], 'action': 'publish'}, actor)

    def detail(self, record_id, private=False):
        with self.store.connect() as db:
            if private:
                result = self._detail(db, record_id)
                result['audit'] = [dict(r) for r in db.execute('''SELECT created,actor,action,revision
                    FROM club_management_audit WHERE club_id=? ORDER BY id DESC LIMIT 20''', (record_id,))]
                p = result['profile']
                result['similar'] = [dict(r) for r in db.execute('''SELECT c.id,c.name,COALESCE(m.country,m.region,'') country
                    FROM clubs c LEFT JOIN club_directory_meta m ON m.club_id=c.id
                    WHERE c.name=? AND c.id<>? LIMIT 10''', (p['name'], record_id))]
                return result
            record = self._record(db, record_id)
            if not record or not record['visible'] or not record['payload']['profilePublic']:
                raise Problem(404, '这份资料暂未公开。')
            p = record['payload']
            if not p['contactPublic']:
                p['contact'], p['qr'] = '', ''
            return dict(id=record_id, profile=p, updated=record['updated'], schoolRef=record['schoolRef'])
