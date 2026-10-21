"""Small SQLite directory; edit keys are stored only as SHA-256 digests."""
from contextlib import contextmanager
import hashlib
import json
import os
import secrets
import sqlite3
import time
import unicodedata


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Store:
    def __init__(self, path, catalog):
        self.path, self.catalog = path, catalog
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS clubs (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL, contact TEXT NOT NULL, count INTEGER NOT NULL, qr TEXT NOT NULL, edit_hash TEXT NOT NULL, updated INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1)')
            db.execute('CREATE TABLE IF NOT EXISTS events (ip TEXT, kind TEXT, school TEXT, stamp INTEGER)')
            db.execute('CREATE INDEX IF NOT EXISTS event_lookup ON events(ip,kind,stamp)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def name_key(name):
        return ''.join(unicodedata.normalize('NFKC', name).casefold().split())

    @staticmethod
    def key_hash(key):
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    def canonical(self, name):
        key = self.name_key(name)
        for school in self.catalog:
            if self.name_key(school['name']) == key:
                return school['id'], school['name'], key
        return 'club-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:20], name, key

    def summary(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,name,count,updated FROM clubs ORDER BY name')]

    def profile(self, school_id, ip):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,name,contact,count,qr,updated,revision FROM clubs WHERE id=?', (school_id,)).fetchone()
            if row is None:
                return None
            since = int(time.time()) - 3600
            seen = {r[0] for r in db.execute('SELECT DISTINCT school FROM events WHERE ip=? AND kind=? AND stamp>?', (ip, 'read', since))}
            if school_id not in seen and len(seen) >= 10:
                raise Problem(429, '查看学校过于频繁，请稍后再试。')
            db.execute('INSERT INTO events VALUES(?,?,?,?)', (ip, 'read', school_id, int(time.time())))
            return dict(row)

    def edit_profile(self, key):
        if not isinstance(key, str) or not 30 <= len(key) <= 100:
            raise Problem(403, '修改链接无效或已失效。')
        with self.connect() as db:
            row = db.execute('SELECT id,name,contact,count,qr,updated,revision FROM clubs WHERE edit_hash=?', (self.key_hash(key),)).fetchone()
            if row is None:
                raise Problem(403, '修改链接无效或已失效。')
            return dict(row)

    def save(self, data, ip):
        name = data.get('school')
        contact = data.get('contact')
        count = data.get('count')
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
            raise Problem(400, '请填写学校全称。')
        if not isinstance(contact, str) or not 2 <= len(contact.strip()) <= 300:
            raise Problem(400, '请填写负责人联系方式（2–300字）。')
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100000:
            raise Problem(400, '群人数请填写1至100000之间的整数。')
        if data.get('consent') is not True:
            raise Problem(400, '请确认同意在学校页面展示填写的信息。')
        school_id, name, name_key = self.canonical(name.strip())
        supplied_key = data.get('editKey', '')
        if not isinstance(supplied_key, str) or len(supplied_key) > 100:
            raise Problem(403, '修改链接无效。')
        now = int(time.time())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM events WHERE stamp<?', (now - 86400,))
            if db.execute('SELECT COUNT(*) FROM events WHERE ip=? AND kind=? AND stamp>?', (ip, 'write', now - 3600)).fetchone()[0] >= 10:
                raise Problem(429, '提交过于频繁，请一小时后再试。')
            old = db.execute('SELECT * FROM clubs WHERE name_key=?', (name_key,)).fetchone()
            if old:
                if supplied_key:
                    if not secrets.compare_digest(old['edit_hash'], self.key_hash(supplied_key)):
                        raise Problem(403, '修改链接已失效，请使用公共填写页重新提交。')
                    if data.get('revision') != old['revision']:
                        raise Problem(409, '资料已在其他页面更新，请重新打开修改链接。')
                    key = supplied_key
                else:
                    key = secrets.token_urlsafe(32)
                qr = data.get('qr') if data.get('qr') is not None else old['qr']
                revision = old['revision'] + 1
                db.execute('UPDATE clubs SET contact=?,count=?,qr=?,updated=?,revision=?,edit_hash=? WHERE id=?', (contact.strip(), count, qr, now, revision, self.key_hash(key), old['id']))
                school_id = old['id']
            else:
                if supplied_key:
                    raise Problem(403, '修改链接与当前学校不匹配。')
                key = secrets.token_urlsafe(32)
                qr = data.get('qr') or ''
                revision = 1
                db.execute('INSERT INTO clubs VALUES(?,?,?,?,?,?,?,?,?)', (school_id, name, name_key, contact.strip(), count, qr, self.key_hash(key), now, revision))
            db.execute('INSERT INTO events VALUES(?,?,?,?)', (ip, 'write', school_id, now))
        return {'id': school_id, 'name': name, 'editKey': key, 'revision': revision}
