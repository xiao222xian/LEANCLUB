"""School community operations in the existing database and admin session."""
import base64
import io
import json
import re
import time
from PIL import Image, UnidentifiedImageError
from store import Problem, Store


class ClubManagement:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS club_directory_meta (
                    club_id TEXT PRIMARY KEY, en TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '', alias TEXT NOT NULL DEFAULT '',
                    school_id INTEGER, visible INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'submitted');
                CREATE TABLE IF NOT EXISTS club_imports (
                    source TEXT PRIMARY KEY, digest TEXT NOT NULL, imported INTEGER NOT NULL,
                    clubs INTEGER NOT NULL, events INTEGER NOT NULL, total_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS club_import_records (
                    source TEXT NOT NULL, original_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(source,original_id));
                CREATE TABLE IF NOT EXISTS club_history_events (
                    source TEXT NOT NULL, source_rowid INTEGER NOT NULL,
                    ip TEXT, kind TEXT, school TEXT, stamp INTEGER,
                    PRIMARY KEY(source,source_rowid));
                CREATE TABLE IF NOT EXISTS club_management_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, club_id TEXT NOT NULL,
                    actor TEXT NOT NULL, created INTEGER NOT NULL, action TEXT NOT NULL,
                    fields TEXT NOT NULL, revision INTEGER NOT NULL);
            ''')

    def listing(self):
        with self.store.connect() as db:
            items = [dict(r) for r in db.execute('''SELECT c.id,c.name,c.count,c.updated,c.revision,
                COALESCE(m.region,'') region,COALESCE(m.visible,1) visible,
                COALESCE(m.source,'submitted') source, m.school_id schoolId,
                length(c.contact)>0 hasContact,length(c.qr)>0 hasQr
                FROM clubs c LEFT JOIN club_directory_meta m ON m.club_id=c.id
                ORDER BY c.count DESC,c.name,c.id''')]
            imports = [dict(r) for r in db.execute('SELECT source,imported,clubs,events,total_count totalCount FROM club_imports')]
        return {'items': items, 'imports': imports}

    def detail(self, club_id):
        with self.store.connect() as db:
            row = db.execute('''SELECT c.id,c.name,c.contact,c.count,c.qr,c.updated,c.revision,
                COALESCE(m.en,'') en,COALESCE(m.region,'') region,COALESCE(m.alias,'') alias,
                COALESCE(m.visible,1) visible,COALESCE(m.source,'submitted') source,m.school_id schoolId
                FROM clubs c LEFT JOIN club_directory_meta m ON m.club_id=c.id WHERE c.id=?''', (club_id,)).fetchone()
            if not row:
                raise Problem(404, '这份学校资料不存在。')
            audit = [dict(r) for r in db.execute('SELECT created,actor,action,fields,revision FROM club_management_audit WHERE club_id=? ORDER BY id DESC LIMIT 30', (club_id,))]
        return {'club': dict(row), 'audit': audit}

    def history(self, page):
        if type(page) is not int or not 1 <= page <= 100000:
            raise Problem(400, '页码无效。')
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute('''SELECT e.kind,e.stamp,c.name school,e.ip
                FROM club_history_events e LEFT JOIN clubs c ON c.id=e.school
                ORDER BY e.stamp DESC,e.source,e.source_rowid DESC LIMIT 30 OFFSET ?''', ((page-1)*30,))]
            total = db.execute('SELECT COUNT(*) FROM club_history_events').fetchone()[0]
        for row in rows:
            # Preserve original addresses in the restricted archive, mask in the UI.
            ip = row.pop('ip') or ''
            row['network'] = '.'.join(ip.split('.')[:2]) + '.*.*' if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', ip) else '已隐藏'
        return {'items': rows, 'page': page, 'total': total}

    def save(self, club_id, data, actor, qr=None):
        fields = {'name', 'contact', 'count', 'en', 'region', 'alias', 'visible', 'revision', 'schoolId', 'removeQr'}
        if not isinstance(data, dict) or set(data) != (fields if qr is None else {'revision'}):
            raise Problem(400, '学校资料字段不完整，请刷新后重试。')
        if type(data.get('revision')) is not int or data['revision'] < 1:
            raise Problem(400, '资料版本无效。')
        if qr is None:
            for key, limit in [('name', 100), ('contact', 300), ('en', 200), ('region', 40), ('alias', 200)]:
                if not isinstance(data[key], str) or len(data[key].strip()) > limit:
                    raise Problem(400, '资料文字过长或格式无效。')
            if len(data['name'].strip()) < 2 or type(data['count']) is not int or not 0 <= data['count'] <= 100000:
                raise Problem(400, '请填写学校名称和0至100000之间的社群规模。')
            if type(data['visible']) is not bool or type(data['removeQr']) is not bool:
                raise Problem(400, '展示状态无效。')
            if data['schoolId'] is not None and (type(data['schoolId']) is not int or data['schoolId'] < 1):
                raise Problem(400, '学校目录编号无效。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM clubs WHERE id=?', (club_id,)).fetchone()
            if not old:
                raise Problem(404, '这份学校资料不存在。')
            if old['revision'] != data['revision']:
                raise Problem(409, '资料已更新，请重新打开后再修改。')
            revision = old['revision'] + 1
            now = int(time.time())
            if qr is not None:
                db.execute('UPDATE clubs SET qr=?,updated=?,revision=? WHERE id=?', (qr, now, revision, club_id))
                changed = ['qr']
            else:
                name = data['name'].strip()
                key = Store.name_key(name)
                if db.execute('SELECT 1 FROM clubs WHERE name_key=? AND id<>?', (key, club_id)).fetchone():
                    raise Problem(409, '该学校已有资料，请先核对，避免重复。')
                db.execute('UPDATE clubs SET name=?,name_key=?,contact=?,count=?,qr=?,updated=?,revision=? WHERE id=?',
                    (name, key, data['contact'].strip(), data['count'], '' if data['removeQr'] else old['qr'], now, revision, club_id))
                db.execute('''INSERT INTO club_directory_meta(club_id,en,region,alias,visible,school_id) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(club_id) DO UPDATE SET en=excluded.en,region=excluded.region,alias=excluded.alias,
                    visible=excluded.visible,school_id=excluded.school_id''',
                    (club_id, data['en'].strip(), data['region'].strip(), data['alias'].strip(), int(data['visible']), data['schoolId']))
                changed = sorted(fields - {'revision'})
            db.execute('INSERT INTO club_management_audit(club_id,actor,created,action,fields,revision) VALUES(?,?,?,?,?,?)',
                       (club_id, actor, now, 'qr' if qr is not None else 'save', json.dumps(changed), revision))
        return self.detail(club_id)

    @staticmethod
    def image(raw, content_type):
        if content_type not in ('image/png', 'image/jpeg') or len(raw) > 2 * 1024**2:
            raise Problem(400, '请选择2MB以内的PNG或JPG二维码。')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG', 'JPEG') or image.format != ('PNG' if content_type == 'image/png' else 'JPEG'):
                    raise ValueError()
                if not (32 <= image.width <= 6000 and 32 <= image.height <= 6000 and image.width * image.height <= 24000000):
                    raise ValueError()
                image.verify()
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise Problem(400, '二维码图片无法读取。')
        return 'data:' + content_type + ';base64,' + base64.b64encode(raw).decode('ascii')
