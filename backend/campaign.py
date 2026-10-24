"""City campaign data, administrator authentication and validated poster storage.

All campaign tables are namespaced; the pre-existing school directory is untouched.
Visitor and session credentials are opaque random values, stored only as digests.
"""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
import ipaddress
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from campaign_feishu import FeishuNotifications
from campaign_feedback import CampaignFeedback
from campaign_comments import CampaignComments
from campaign_events import CampaignEvents
from campaign_groups import CampaignGroups
from store import Problem

CITIES = (('shenzhen', '深圳'), ('zhuhai', '珠海'), ('guangzhou', '广州'))
CITY_IDS = {city for city, _ in CITIES}
VISITOR_AGE = 365 * 86400
SESSION_AGE = 8 * 3600
SHANGHAI = timezone(timedelta(hours=8))
MAX_POSTER_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
MEDIA_NAME = re.compile(r'[a-f0-9]{32}(?:-640)?\.webp')
USERNAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,39}')
EMAIL_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*")
EMAIL_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
PBKDF2_ROUNDS = 600_000


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def normalized_ip(value):
    """Use one identity for expanded IPv6 and IPv4-mapped IPv6 addresses."""
    try:
        if not isinstance(value, str) or '%' in value:
            raise ValueError()
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.compressed
    except ValueError:
        raise Problem(400, '无法确认网络地址，请刷新后重试。')


class IPVoteConflict(Problem):
    def __init__(self, city):
        super().__init__(409, '该IP已投票，请勿重复提交；如需改选，请使用最初投票的浏览器。')
        self.vote = {'city': city}


def password_hash(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return 'pbkdf2_sha256$%s$%s$%s' % (PBKDF2_ROUNDS, salt, hashed.hex())


def password_matches(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def valid_admin_username(username):
    """Accept existing short usernames or an unquoted ASCII email login ID.

    This checks only the identifier's shape; it does not verify email ownership.
    Values retain their original spelling and the database's existing NOCASE rules.
    """
    if not isinstance(username, str) or len(username) > 254:
        return False
    if USERNAME.fullmatch(username):
        return True
    if username.count('@') != 1:
        return False
    local, domain = username.split('@')
    labels = domain.split('.')
    return bool(1 <= len(local) <= 64 and EMAIL_LOCAL.fullmatch(local)
                and len(labels) >= 2 and all(EMAIL_LABEL.fullmatch(label) for label in labels))


def validate_credentials(username, password):
    if not valid_admin_username(username):
        raise Problem(400, '请输入3–40位用户名（字母或数字开头，可含 . _ -），或有效的英文邮箱（最长254位）。')
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise Problem(400, '密码长度须为12–128位。')
    if password.isspace():
        raise Problem(400, '密码不能只包含空格。')


class CampaignStore:
    def __init__(self, path, media_dir=None, clock=time.time):
        self.path = str(path)
        self.media_dir = Path(media_dir or Path(path).resolve().parent / 'media').resolve()
        web_root = Path(__file__).resolve().parents[1] / 'web'
        if any(target == web_root or web_root in target.parents for target in (Path(path).resolve(), self.media_dir)):
            raise ValueError('CLUB_DB and CLUB_MEDIA_DIR must be outside the web directory.')
        self.clock = clock
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        # Constant-cost failed lookups avoid disclosing usernames through hashing time.
        self._dummy_password = password_hash(secrets.token_urlsafe(32))
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS campaign_visitors (
                    token_hash TEXT PRIMARY KEY, created INTEGER NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_votes (
                    visitor TEXT PRIMARY KEY REFERENCES campaign_visitors(token_hash),
                    city TEXT NOT NULL CHECK(city IN ('shenzhen','zhuhai','guangzhou')),
                    created INTEGER NOT NULL, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_page_events (
                    event_id TEXT PRIMARY KEY, visitor TEXT NOT NULL REFERENCES campaign_visitors(token_hash),
                    page TEXT NOT NULL CHECK(page IN ('home','vote')), stamp INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS campaign_page_by_stamp ON campaign_page_events(stamp,page,visitor);
                CREATE INDEX IF NOT EXISTS campaign_votes_by_created ON campaign_votes(created);
                CREATE TABLE IF NOT EXISTS campaign_admins (
                    username TEXT PRIMARY KEY COLLATE NOCASE, password_hash TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_sessions (
                    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES campaign_admins(username),
                    csrf TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_rate_events (
                    key_hash TEXT NOT NULL, kind TEXT NOT NULL, stamp INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS campaign_rate_lookup ON campaign_rate_events(key_hash,kind,stamp);
                CREATE TABLE IF NOT EXISTS campaign_posters (
                    city TEXT PRIMARY KEY, desktop TEXT NOT NULL, mobile TEXT NOT NULL,
                    x REAL NOT NULL, y REAL NOT NULL, updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_poster_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL,
                    desktop TEXT NOT NULL, mobile TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
                    changed INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            # Preserve existing votes whose source IP was never recorded. The
            # migration and per-database secret are serialized across processes.
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(campaign_votes)')}
            if 'ip_hash' not in columns:
                db.execute('ALTER TABLE campaign_votes ADD COLUMN ip_hash TEXT')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS campaign_votes_by_ip ON campaign_votes(ip_hash) WHERE ip_hash IS NOT NULL')
            db.execute('''CREATE TABLE IF NOT EXISTS campaign_vote_ips (
                ip_hash TEXT PRIMARY KEY,
                visitor TEXT NOT NULL REFERENCES campaign_votes(visitor))''')
            db.execute('INSERT OR IGNORE INTO campaign_vote_ips(ip_hash,visitor) SELECT ip_hash,visitor FROM campaign_votes WHERE ip_hash IS NOT NULL')
            db.execute('INSERT OR IGNORE INTO campaign_settings(key,value) VALUES(?,?)',
                       ('vote_ip_secret', secrets.token_hex(32)))
            self._vote_ip_secret = bytes.fromhex(db.execute(
                'SELECT value FROM campaign_settings WHERE key=?', ('vote_ip_secret',)).fetchone()['value'])
            if len(self._vote_ip_secret) != 32:
                raise ValueError('The campaign vote IP secret is invalid.')
        self.notifications = FeishuNotifications(self)
        self.feedback = CampaignFeedback(self)
        self.comments = CampaignComments(self)
        self.events = CampaignEvents(self)
        self.groups = CampaignGroups(self)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def limit(self, key, kind, maximum, seconds):
        now = int(self.clock())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM campaign_rate_events WHERE stamp<?', (now - 86400,))
            count = db.execute('SELECT COUNT(*) FROM campaign_rate_events WHERE key_hash=? AND kind=? AND stamp>?',
                               (digest(key), kind, now - seconds)).fetchone()[0]
            if count >= maximum:
                raise Problem(429, '操作过于频繁，请稍后再试。')
            db.execute('INSERT INTO campaign_rate_events VALUES(?,?,?)', (digest(key), kind, now))

    def visitor(self, token, db=None):
        """Return (digest, fresh cookie token or None); never accept a chosen ID."""
        now = int(self.clock())
        with self.connect() if db is None else nullcontext(db) as db:
            if isinstance(token, str) and TOKEN.fullmatch(token):
                row = db.execute('SELECT token_hash FROM campaign_visitors WHERE token_hash=? AND expires>?',
                                 (digest(token), now)).fetchone()
                if row:
                    return row['token_hash'], None
            token = secrets.token_urlsafe(32)
            hashed = digest(token)
            db.execute('INSERT INTO campaign_visitors VALUES(?,?,?)', (hashed, now, now + VISITOR_AGE))
        return hashed, token

    def ip_hash(self, ip):
        return hmac.new(self._vote_ip_secret, normalized_ip(ip).encode('ascii'), hashlib.sha256).hexdigest()

    @staticmethod
    def _ip_vote(db, ip_hash):
        return db.execute('''SELECT vote.visitor,vote.city FROM campaign_vote_ips AS claim
            JOIN campaign_votes AS vote ON vote.visitor=claim.visitor WHERE claim.ip_hash=?''',
                          (ip_hash,)).fetchone() if ip_hash else None

    def vote_state(self, visitor, ip=None):
        hashed = self.ip_hash(ip) if ip is not None else None
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT city FROM campaign_votes WHERE visitor=?', (visitor,)).fetchone()
            if row:
                return {'vote': {'city': row['city']}, 'canChange': True}
            row = self._ip_vote(db, hashed)
        return {'vote': {'city': row['city']} if row else None, 'canChange': not bool(row)}

    def vote(self, visitor, ip=None):
        return self.vote_state(visitor, ip)['vote']

    def cast_vote(self, visitor, city, ip=None):
        if not isinstance(city, str) or city not in CITY_IDS:
            raise Problem(400, '请选择深圳、珠海或广州。')
        hashed = self.ip_hash(ip) if ip is not None else None
        now = int(self.clock())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            own = db.execute('SELECT city,ip_hash FROM campaign_votes WHERE visitor=?', (visitor,)).fetchone()
            ip_vote = self._ip_vote(db, hashed)
            if own:
                # A browser remains one vote when its network changes. Retain
                # previous IP claims so another cookie cannot reuse that IP.
                if hashed and ip_vote is None:
                    if own['ip_hash'] is None:
                        db.execute('UPDATE campaign_votes SET ip_hash=? WHERE visitor=?', (hashed, visitor))
                    db.execute('INSERT INTO campaign_vote_ips(ip_hash,visitor) VALUES(?,?)', (hashed, visitor))
                if own['city'] != city:
                    db.execute('UPDATE campaign_votes SET city=?,updated=? WHERE visitor=?', (city, now, visitor))
            elif ip_vote:
                if ip_vote['city'] != city:
                    raise IPVoteConflict(ip_vote['city'])
                # Repeated delivery or another browser on the same IP never
                # creates a second row and never grants ownership of the first.
                return {'city': ip_vote['city']}
            else:
                db.execute('INSERT INTO campaign_votes(visitor,city,created,updated,ip_hash) VALUES(?,?,?,?,?)',
                           (visitor, city, now, now, hashed))
                if hashed:
                    db.execute('INSERT INTO campaign_vote_ips(ip_hash,visitor) VALUES(?,?)', (hashed, visitor))
                self.notifications.enqueue(db, 'new', city, None, now)
        return {'city': city}

    def track(self, visitor, page, event_id):
        if not isinstance(page, str) or page not in ('home', 'vote'):
            raise Problem(400, '页面标识无效。')
        try:
            if not isinstance(event_id, str) or str(uuid.UUID(event_id)) != event_id.lower():
                raise ValueError()
        except (ValueError, AttributeError):
            raise Problem(400, '访问事件标识无效。')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO campaign_page_events VALUES(?,?,?,?)',
                       (event_id.lower(), visitor, page, int(self.clock())))

    def has_admin(self):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM campaign_admins LIMIT 1').fetchone() is not None

    def create_admin(self, username, password, first_only=False):
        validate_credentials(username, password)
        encoded = password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if first_only and db.execute('SELECT 1 FROM campaign_admins LIMIT 1').fetchone():
                raise Problem(409, '管理员已设置，请直接登录。')
            try:
                db.execute('INSERT INTO campaign_admins VALUES(?,?,?)', (username, encoded, int(self.clock())))
            except sqlite3.IntegrityError:
                raise Problem(409, '用户名已存在。')

    def reset_password(self, username, password):
        validate_credentials(username, password)
        encoded = password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT username FROM campaign_admins WHERE username=?', (username,)).fetchone()
            if row is None:
                raise Problem(404, '管理员不存在。')
            db.execute('UPDATE campaign_admins SET password_hash=? WHERE username=?', (encoded, row['username']))
            db.execute('DELETE FROM campaign_sessions WHERE username=?', (row['username'],))

    def login(self, username, password, ip):
        self.limit(ip, 'login', 10, 900)
        if not valid_admin_username(username) or not isinstance(password, str) or len(password) > 128:
            raise Problem(401, '用户名或密码不正确。')
        self.limit(username.casefold(), 'login-user', 30, 900)
        with self.connect() as db:
            row = db.execute('SELECT * FROM campaign_admins WHERE username=?', (username,)).fetchone()
        valid = password_matches(password, row['password_hash'] if row else self._dummy_password)
        if not valid or row is None:
            raise Problem(401, '用户名或密码不正确。')
        return self.new_session(row['username'], password_version=row['password_hash'])

    def new_session(self, username, password_version=None):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(self.clock())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if password_version is not None:
                current = db.execute('SELECT password_hash FROM campaign_admins WHERE username=?', (username,)).fetchone()
                if current is None or not hmac.compare_digest(current['password_hash'], password_version):
                    raise Problem(401, '用户名或密码不正确。')
            db.execute('DELETE FROM campaign_sessions WHERE expires<=?', (now,))
            db.execute('INSERT INTO campaign_sessions VALUES(?,?,?,?)', (digest(token), username, csrf, now + SESSION_AGE))
        return token, {'authenticated': True, 'username': username, 'csrfToken': csrf}

    def session(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        with self.connect() as db:
            row = db.execute('SELECT username,csrf FROM campaign_sessions WHERE token_hash=? AND expires>?',
                             (digest(token), int(self.clock()))).fetchone()
        return {'authenticated': True, 'username': row['username'], 'csrfToken': row['csrf']} if row else None

    def logout(self, token):
        if isinstance(token, str):
            with self.connect() as db:
                db.execute('DELETE FROM campaign_sessions WHERE token_hash=?', (digest(token),))

    def cities(self):
        with self.connect() as db:
            rows = {r['city']: r for r in db.execute('SELECT * FROM campaign_posters')}
        result = []
        for city, name in CITIES:
            row = rows.get(city)
            result.append({
                'id': city, 'name': name,
                'posterUrl': '/api/media/' + row['desktop'] if row and row['desktop'] else '/city-vote/assets/alan-v1/%s.webp' % city,
                'posterMobileUrl': '/api/media/' + row['mobile'] if row and row['mobile'] else '/city-vote/assets/alan-v1/%s-640.webp' % city,
                'positionX': row['x'] if row else (65 if city == 'guangzhou' else 50),
                'positionY': row['y'] if row else (0 if city == 'zhuhai' else 45),
                'isDefault': not row or not row['desktop'], 'updatedAt': iso(row['updated']) if row else None,
            })
        return result

    def _city(self, city):
        if city not in CITY_IDS:
            raise Problem(404, '城市不存在。')
        return next(c for c in self.cities() if c['id'] == city)

    def _save_poster(self, city, desktop, mobile, x, y):
        now = int(self.clock())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM campaign_posters WHERE city=?', (city,)).fetchone()
            if desktop is None:
                # Position edits must not resurrect the previous upload if another
                # administrator replaced the image between GET and POST.
                desktop, mobile = (old['desktop'], old['mobile']) if old else ('', '')
            if old:
                db.execute('INSERT INTO campaign_poster_history(city,desktop,mobile,x,y,changed) VALUES(?,?,?,?,?,?)',
                           (city, old['desktop'], old['mobile'], old['x'], old['y'], now))
            db.execute('INSERT INTO campaign_posters VALUES(?,?,?,?,?,?) ON CONFLICT(city) DO UPDATE SET desktop=excluded.desktop,mobile=excluded.mobile,x=excluded.x,y=excluded.y,updated=excluded.updated',
                       (city, desktop, mobile, x, y, now))
        return self._city(city)

    def replace_poster(self, city, raw, content_type):
        self._city(city)
        if content_type not in ('image/png', 'image/jpeg', 'image/webp'):
            raise Problem(415, '请上传 PNG、JPG 或 WebP 静态图片。')
        if not raw or len(raw) > MAX_POSTER_BYTES:
            raise Problem(413, '图片请控制在8MB以内。')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as probe:
                    expected = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}[content_type]
                    if probe.format != expected or getattr(probe, 'n_frames', 1) != 1:
                        raise Problem(400, '请上传格式匹配的静态图片。')
                    width, height = probe.size
                    if min(width, height) < 64 or max(width, height) > 10000 or width * height > MAX_PIXELS:
                        raise Problem(400, '图片尺寸需至少64像素，最长边不超过10000像素且总像素不超过2400万。')
                    probe.verify()
                with Image.open(io.BytesIO(raw)) as source:
                    source.load()
                    corrected = ImageOps.exif_transpose(source)
                    if 'A' in corrected.getbands() or 'transparency' in corrected.info:
                        rgba = corrected.convert('RGBA')
                        normalized = Image.new('RGB', rgba.size, '#101717')
                        normalized.paste(rgba, mask=rgba.getchannel('A'))
                    else:
                        normalized = corrected.convert('RGB')
                    # Copy pixels only: no EXIF, ICC, comments, filenames or active data.
                    clean = Image.new('RGB', normalized.size)
                    clean.paste(normalized)
        except Problem:
            raise
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise Problem(400, '图片无法解码，请重新导出后上传。')
        stem = secrets.token_hex(16)
        names = (stem + '.webp', stem + '-640.webp')
        for target_width, name in zip((941, 640), names):
            width = min(clean.width, target_width)
            height = max(1, round(clean.height * width / clean.width))
            resized = clean.resize((width, height), Image.Resampling.LANCZOS)
            temporary = self.media_dir / (name + '.tmp')
            with temporary.open('xb') as output:
                resized.save(output, format='WEBP', quality=90, method=6)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.media_dir / name)
        # Both immutable files exist before committing the single DB pointer update.
        return self._save_poster(city, names[0], names[1], 65 if city == 'guangzhou' else 50, 45)

    def position(self, city, x, y):
        self._city(city)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100 for v in (x, y)):
            raise Problem(400, '图片位置须为0–100之间的数值。')
        return self._save_poster(city, None, None, x, y)

    def reset_poster(self, city):
        self._city(city)
        return self._save_poster(city, '', '', 65 if city == 'guangzhou' else 50, 0 if city == 'zhuhai' else 45)

    def media(self, name):
        if not MEDIA_NAME.fullmatch(name):
            raise Problem(404, '图片不存在。')
        target = self.media_dir / name
        if not target.is_file() or target.is_symlink():
            raise Problem(404, '图片不存在。')
        return target.read_bytes()

    def stats(self):
        now = int(self.clock())
        today = datetime.fromtimestamp(now, SHANGHAI).date()
        start_today = int(datetime.combine(today, datetime.min.time(), SHANGHAI).timestamp())
        days = [(today - timedelta(days=index)).isoformat() for index in range(13, -1, -1)]
        first = start_today - 13 * 86400
        with self.connect() as db:
            # A read transaction keeps counts, percentages and trends consistent.
            db.execute('BEGIN')
            counts = {r['city']: r['n'] for r in db.execute('SELECT city,COUNT(*) n FROM campaign_votes GROUP BY city')}
            total = sum(counts.values())
            metrics = {}
            for page in ('home', 'vote'):
                all_time = db.execute('SELECT COUNT(*),COUNT(DISTINCT visitor) FROM campaign_page_events WHERE page=?', (page,)).fetchone()
                current = db.execute('SELECT COUNT(*),COUNT(DISTINCT visitor) FROM campaign_page_events WHERE page=? AND stamp>=?', (page, start_today)).fetchone()
                metrics[page] = {'pv': all_time[0], 'uv': all_time[1], 'todayPv': current[0], 'todayUv': current[1]}
            pages = {(r['day'], r['page']): r['n'] for r in db.execute("SELECT date(stamp,'unixepoch','+8 hours') day,page,COUNT(*) n FROM campaign_page_events WHERE stamp>=? GROUP BY day,page", (first,))}
            votes = {r['day']: r['n'] for r in db.execute("SELECT date(created,'unixepoch','+8 hours') day,COUNT(*) n FROM campaign_votes WHERE created>=? GROUP BY day", (first,))}
        return {'totalVotes': total,
                'votes': [{'city': city, 'name': name, 'count': counts.get(city, 0), 'share': round(counts.get(city, 0) * 100 / total, 2) if total else 0} for city, name in CITIES],
                'metrics': metrics,
                'daily': [{'date': day, 'homePv': pages.get((day, 'home'), 0), 'votePv': pages.get((day, 'vote'), 0), 'newVotes': votes.get(day, 0)} for day in days],
                'updatedAt': iso(now)}
