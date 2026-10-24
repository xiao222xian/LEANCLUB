"""One active campaign group QR, with private drafts and lossless image storage."""
import base64
import binascii
import io
import re
import secrets
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from store import Problem


class CampaignGroups:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_groups (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, image BLOB NOT NULL,
                    image_version INTEGER NOT NULL DEFAULT 1, expires INTEGER,
                    created INTEGER NOT NULL, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_group_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    active_id TEXT REFERENCES campaign_groups(id), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO campaign_group_config VALUES(1,NULL,0);
                CREATE TABLE IF NOT EXISTS campaign_group_events (
                    event_id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES campaign_groups(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('view','save')), created INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS campaign_group_events_summary ON campaign_group_events(group_id,kind);
                CREATE TABLE IF NOT EXISTS campaign_group_audit (
                    id INTEGER PRIMARY KEY, group_id TEXT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, created INTEGER NOT NULL);
            ''')

    @staticmethod
    def image(value):
        match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)', value or '') if isinstance(value, str) else None
        if not match:
            raise Problem(400, '请选择 PNG 或 JPG 二维码。')
        try:
            raw = base64.b64decode(match[2], validate=True)
            if len(raw) > 2 * 1024**2:
                raise ValueError()
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.format not in ('PNG', 'JPEG') or getattr(source, 'n_frames', 1) != 1:
                        raise ValueError()
                    if not (64 <= source.width <= 4000 and 64 <= source.height <= 4000 and source.width * source.height <= 8_000_000):
                        raise ValueError()
                    source.load()
                    rgba = ImageOps.exif_transpose(source).convert('RGBA')
                    clean = Image.new('RGB', rgba.size, 'white')
                    clean.paste(rgba, mask=rgba.getchannel('A'))
                    output = io.BytesIO()
                    clean.save(output, 'PNG')
                    if output.tell() > 3 * 1024**2:
                        raise ValueError()
                    return output.getvalue()
        except (ValueError, binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise Problem(400, '图片无法读取。请选择 2MB 以内、64–4000 像素的 PNG 或 JPG。')

    def active(self):
        with self.store.connect() as db:
            row = db.execute('SELECT g.id,g.image_version,g.expires FROM campaign_groups g JOIN campaign_group_config c ON c.active_id=g.id WHERE g.expires IS NULL OR g.expires>?', (int(self.store.clock()),)).fetchone()
        if not row:
            return None
        return {'id': row['id'], 'imageVersion': row['image_version'], 'expiresAt': row['expires'],
                'imageUrl': '/api/campaign/group-qr/%s/%s' % (row['id'], row['image_version'])}

    def listing(self):
        with self.store.connect() as db:
            config = db.execute('SELECT * FROM campaign_group_config').fetchone()
            rows = db.execute('SELECT id,label,image_version,expires,updated FROM campaign_groups ORDER BY updated DESC,id').fetchall()
            counts = {(r['group_id'], r['kind']): r['n'] for r in db.execute('SELECT group_id,kind,COUNT(*) n FROM campaign_group_events GROUP BY group_id,kind')}
        now = int(self.store.clock())
        return {'revision': config['revision'], 'activeId': config['active_id'], 'items': [
            {'id': r['id'], 'label': r['label'], 'imageVersion': r['image_version'], 'expiresAt': r['expires'],
             'expired': r['expires'] is not None and r['expires'] <= now, 'updated': r['updated'],
             'imageUrl': '/api/admin/group-qrs/%s/image?v=%s' % (r['id'], r['image_version']),
             'views': counts.get((r['id'], 'view'), 0), 'saves': counts.get((r['id'], 'save'), 0)} for r in rows]}

    def media(self, group_id, version=None, private=False, with_cache=False):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM campaign_groups WHERE id=?', (group_id,)).fetchone()
            active = db.execute('SELECT active_id FROM campaign_group_config').fetchone()[0]
        if not row or (not private and (active != group_id or str(row['image_version']) != version or row['expires'] is not None and row['expires'] <= self.store.clock())):
            raise Problem(404, '二维码已更新或暂未开放。')
        ttl = 0 if private else 30
        if row['expires'] is not None:
            ttl = min(ttl, max(0, int(row['expires'] - self.store.clock())))
        return (row['image'], ttl) if with_cache else row['image']

    def save(self, data, actor):
        action = data.get('action')
        if action not in ('save', 'activate', 'disable', 'delete') or type(data.get('revision')) is not int:
            raise Problem(400, '操作无效，请刷新重试。')
        group_id = data.get('id')
        if group_id is not None and (not isinstance(group_id, str) or not re.fullmatch('[a-f0-9]{32}', group_id)):
            raise Problem(400, '二维码编号无效。')
        now = int(self.store.clock())
        image = None
        if action == 'save':
            label, expires = data.get('label'), data.get('expiresAt')
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60:
                raise Problem(400, '请填写60字以内的内部备注。')
            if expires is not None and (type(expires) is not int or expires < 0 or expires > 4102444800):
                raise Problem(400, '失效时间无效。')
            if data.get('image') is not None:
                image = self.image(data['image'])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            config = db.execute('SELECT * FROM campaign_group_config').fetchone()
            if config['revision'] != data['revision']:
                raise Problem(409, '配置已被更新，请刷新后重试。')
            row = db.execute('SELECT * FROM campaign_groups WHERE id=?', (group_id,)).fetchone() if group_id else None
            if group_id and not row:
                raise Problem(404, '二维码不存在。')
            if action == 'save':
                if not row:
                    if image is None:
                        raise Problem(400, '请先上传二维码。')
                    if db.execute('SELECT COUNT(*) FROM campaign_groups').fetchone()[0] >= 30:
                        raise Problem(400, '最多保留30张二维码，请先移除不用的图片。')
                    group_id = secrets.token_hex(16)
                    db.execute('INSERT INTO campaign_groups VALUES(?,?,?,1,?,?,?)', (group_id, label.strip(), image, expires, now, now))
                else:
                    db.execute('UPDATE campaign_groups SET label=?,image=?,image_version=?,expires=?,updated=? WHERE id=?',
                               (label.strip(), image or row['image'], row['image_version'] + int(image is not None), expires, now, group_id))
            elif action == 'activate':
                if not row or row['expires'] is not None and row['expires'] <= now:
                    raise Problem(400, '请先更新二维码的失效时间。')
                db.execute('UPDATE campaign_group_config SET active_id=?', (group_id,))
            elif action == 'disable':
                db.execute('UPDATE campaign_group_config SET active_id=NULL')
            elif action == 'delete':
                if not row:
                    raise Problem(404, '二维码不存在。')
                if config['active_id'] == group_id:
                    raise Problem(409, '请先停用或切换正在展示的二维码。')
                db.execute('DELETE FROM campaign_groups WHERE id=?', (group_id,))
            db.execute('UPDATE campaign_group_config SET revision=revision+1')
            db.execute('INSERT INTO campaign_group_audit(group_id,actor,action,created) VALUES(?,?,?,?)', (group_id, actor, action, now))
        return self.listing()

    def track(self, data):
        if not isinstance(data.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', data['id']) or data.get('kind') not in ('view', 'save') or not isinstance(data.get('eventId'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', data['eventId']):
            raise Problem(400, '事件无效。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT g.id FROM campaign_groups g JOIN campaign_group_config c ON c.active_id=g.id WHERE g.id=? AND (g.expires IS NULL OR g.expires>?)', (data.get('id'), int(self.store.clock()))).fetchone()
            if row:
                db.execute('INSERT OR IGNORE INTO campaign_group_events VALUES(?,?,?,?)', (data['eventId'], row['id'], data['kind'], int(self.store.clock())))
        return {'ok': True}
