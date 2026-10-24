"""Explicit, backed-up reset of votes, analytics and public comments only."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

TABLES = ('campaign_vote_ips', 'campaign_votes', 'campaign_page_events', 'campaign_comments')
PARTIAL = {
    'campaign_feishu_outbox': ('new', 'summary', 'test', 'change'),
    'campaign_rate_events': ('vote', 'analytics', 'campaign-read', 'comments-visitor',
                             'comments-ip', 'comments-request', 'comments-read'),
}


def fingerprint(db, table, where='', values=()):
    # Hash in memory; no credentials or customer rows leave the process.
    hashes = sorted(hashlib.sha256(repr(row).encode()).digest()
                    for row in db.execute('SELECT * FROM "' + table + '"' + where, values))
    return len(hashes), hashlib.sha256(b''.join(hashes)).digest()


def reset(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    preserved = {table: fingerprint(db, table) for table in tables - set(TABLES) - PARTIAL.keys()}
    before = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in TABLES if table in tables}
    for table in TABLES:
        if table in tables:
            db.execute('DELETE FROM ' + table)
    for table, kinds in PARTIAL.items():
        if table in tables:
            placeholders = ','.join('?' for _ in kinds)
            condition = ' WHERE kind NOT IN (' + placeholders + ') OR kind IS NULL'
            kept = fingerprint(db, table, condition, kinds)
            db.execute('DELETE FROM ' + table + ' WHERE kind IN (' + placeholders + ')', kinds)
            if fingerprint(db, table, condition, kinds) != kept:
                raise ValueError('Preserved subset changed')
    if any(fingerprint(db, name) != value for name, value in preserved.items()):
        raise ValueError('Preserved table changed')
    after = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in before}
    if any(after.values()) or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Reset validation failed')
    return {'before': before, 'after': after, 'preservedTablesUnchanged': len(preserved)}


def main():
    if os.geteuid() != 0 or not Path('/etc/leanschool/daily-release-host.json').exists():
        raise ValueError('Run only on the registered origin host')
    backup = Path('/var/backups/lean-edge-reset') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    subprocess.run(['systemctl', 'stop', 'bojizhuye'], check=True)
    try:
        with sqlite3.connect('/var/lib/bojizhuye/clubs.sqlite3') as db:
            db.execute('PRAGMA foreign_keys=ON')
            with sqlite3.connect(backup / 'before.sqlite3') as saved:
                db.backup(saved)
                if saved.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                    raise ValueError('Backup integrity failed')
            db.execute('BEGIN IMMEDIATE')
            result = reset(db)
            db.commit()
            result.update({'ok': True, 'backup': str(backup), 'sharedDomains': ['boji.tensorbot.xyz', 'leanmuscleclub.com']})
            (backup / 'result.json').write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
    finally:
        subprocess.run(['systemctl', 'start', 'bojizhuye'], check=True)


if __name__ == '__main__':
    main()
