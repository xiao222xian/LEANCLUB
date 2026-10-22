#!/usr/bin/env python3
"""Operator-approved, one-time maintenance; secrets arrive in a private capsule.

Keeps analytics, posters and accounts. Never prints credentials or sends messages.
"""
import json
from contextlib import closing
import os
from pathlib import Path
import pwd
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

assert os.geteuid() == 0, 'Run via sudo.'
os.umask(0o077)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://metadata.tencentyun.com/latest/meta-data/public-ipv4', timeout=5) as r:
    assert r.read().decode().strip() == '152.136.108.87', 'Wrong server.'
capsule = Path('/home/ubuntu/boji-private-maintenance.json')
assert capsule.is_file() and not capsule.is_symlink()
capsule.chmod(0o600)
payload = json.loads(capsule.read_text())
assert set(payload) == {'adminHash', 'feishu'}
assert re.fullmatch(r'pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}', payload['adminHash'])
sys.path.insert(0, '/opt/bojizhuye/current/backend')
from campaign_feishu import validate_webhook
validate_webhook(payload['feishu']['webhook'])
assert payload['feishu']['enabled'] is True

state = Path('/var/lib/bojizhuye/clubs.sqlite3')
backup = Path('/var/backups/bojizhuye-maintenance') / time.strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True, mode=0o700)
backup.parent.chmod(0o700)

operation = r'''
import json, sys
sys.path.insert(0, '/opt/bojizhuye/current/backend')
from campaign import CampaignStore, digest
p = json.load(sys.stdin)
store = CampaignStore('/var/lib/bojizhuye/clubs.sqlite3', '/var/lib/bojizhuye/media')
tables = ('clubs','campaign_votes','campaign_vote_ips','campaign_feishu_outbox','campaign_page_events')
with store.connect() as db:
    db.execute('BEGIN IMMEDIATE')
    before = {t: db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables}
    account = db.execute('SELECT username FROM campaign_admins WHERE username=?', ('admin',)).fetchone()
    assert account is not None
    username = account['username']
    for table in ('campaign_feishu_outbox','campaign_vote_ips','campaign_votes','events','clubs'):
        db.execute('DELETE FROM '+table)
    db.execute('UPDATE campaign_admins SET password_hash=? WHERE username=?', (p['adminHash'],'admin'))
    db.execute('DELETE FROM campaign_sessions WHERE username=?', (username,))
    assert db.execute('SELECT COUNT(*) FROM campaign_sessions WHERE username=?', (username,)).fetchone()[0] == 0
    db.execute("DELETE FROM campaign_rate_events WHERE kind='login-user' AND key_hash=?", (digest('admin'),))
    db.execute("DELETE FROM campaign_rate_events WHERE kind='login'")
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
store.notifications.save(p['feishu'])
with store.connect() as db:
    after = {t: db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables}
    assert all(after[t] == 0 for t in tables if t != 'campaign_page_events')
    assert after['campaign_page_events'] == before['campaign_page_events']
    assert db.execute('SELECT password_hash FROM campaign_admins WHERE username=?', ('admin',)).fetchone()[0] == p['adminHash']
settings = store.notifications.settings()
assert settings['enabled'] and settings['configured']
print(json.dumps({'before':before,'after':after,'adminUpdated':True,'oldAdminSessionsRevoked':True,
                 'feishuEnabled':settings['enabled'],'feishuConfigured':settings['configured'],
                 'notificationQueued':False}))
'''
backup_ready = False
def fix_ownership():
    account = pwd.getpwnam('bojizhuye')
    for path in (state, Path(str(state)+'-wal'), Path(str(state)+'-shm')):
        if path.exists():
            os.chown(path, account.pw_uid, account.pw_gid)
try:
    subprocess.run(['systemctl', 'stop', 'bojizhuye'], check=True)
    assert subprocess.check_output(['systemctl', 'show', 'bojizhuye', '--property=MainPID', '--value'], text=True).strip() == '0'
    with closing(sqlite3.connect(state)) as source, closing(sqlite3.connect(backup / 'before.sqlite3')) as target:
        source.backup(target)
    backup_ready = True
    fix_ownership()
    result = subprocess.run(['runuser','-u','bojizhuye','--','/opt/bojizhuye/current/.venv/bin/python','-c',operation],
                            input=json.dumps(payload), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Maintenance failed; private data was not logged.')
    report = json.loads(result.stdout)
    (backup / 'result.json').write_text(json.dumps(report, indent=2))
    capsule.unlink()
except BaseException:
    if backup_ready:
        with closing(sqlite3.connect(backup / 'before.sqlite3')) as source, closing(sqlite3.connect(state)) as target:
            source.backup(target)
        fix_ownership()
        print('Database restored. Backup:', backup)
    raise
finally:
    subprocess.run(['systemctl', 'start', 'bojizhuye'], check=True)
for attempt in range(10):
    try:
        with opener.open('http://127.0.0.1:5381/api/club/health', timeout=3) as r:
            assert json.load(r)['status'] == 'ok'
        break
    except OSError:
        if attempt == 9:
            raise
        time.sleep(.5)
print(json.dumps(report))
print('PRIVATE BACKUP', backup)
print('MAINTENANCE VERIFIED; service healthy; credentials not printed.')
