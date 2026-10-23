"""Offline, backed-up migration of existing registrations to explicit ownership.

Usage: python scripts/migrate_member_orders.py --db <private.sqlite3>
       --backup-dir <private-backups> --apply --confirm-service-stopped
No application imports, service start/stop, credential reads or payment calls.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from itertools import zip_longest
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from member_orders_schema import (LEGACY_COLUMNS, VERSION,
                                 create_member_metadata, member_schema_ready, registration_sql)


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def inspect(db):
    if member_schema_ready(db):
        return 'current'
    columns = {row[1] for row in db.execute('PRAGMA table_info(campaign_registrations)')}
    if columns != set(LEGACY_COLUMNS):
        raise ValueError('Unexpected registration schema; review it before migration.')
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if table != 'campaign_registrations' and any(row[2] == 'campaign_registrations'
                for row in db.execute('PRAGMA foreign_key_list(' + quoted(table) + ')')):
            raise ValueError('A dependent foreign key requires a reviewed migration.')
    for _, sql in db.execute("SELECT type,sql FROM sqlite_master WHERE type IN ('view','trigger') AND sql IS NOT NULL"):
        if re.search(r'\bcampaign_registrations\b', sql, re.I):
            raise ValueError('A dependent view or trigger requires a reviewed migration.')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_registrations_member_v1'").fetchone():
        raise ValueError('A migration staging table already exists; review it first.')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Existing foreign-key errors must be resolved before migration.')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Existing database integrity check failed.')
    return 'legacy'


def migrate(db_path, backup_dir, apply=False, confirmed_stopped=False):
    target = Path(db_path).resolve(strict=True)
    backup_root = Path(backup_dir).resolve()
    web_root = (ROOT / 'web').resolve()
    if target == web_root or web_root in target.parents or backup_root == web_root or web_root in backup_root.parents:
        raise ValueError('Database and backups must stay outside the public web directory.')
    if not target.is_file():
        raise ValueError('An existing database file is required.')
    db = sqlite3.connect(target, timeout=10)
    backup = None
    try:
        db.execute('PRAGMA foreign_keys=ON')
        state = inspect(db)
        count = db.execute('SELECT COUNT(*) FROM campaign_registrations').fetchone()[0]
        if state == 'current':
            return {'version': VERSION, 'changed': False, 'state': 'current', 'registrations': count}
        if not apply:
            return {'version': VERSION, 'changed': False, 'state': 'migration-required', 'registrations': count}
        if not confirmed_stopped:
            raise ValueError('Stop the event writers and explicitly confirm before applying.')
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / ('before-member-orders-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex + '.sqlite3')
        # Pin a read snapshot for SQLite's online backup API, including committed WAL.
        db.execute('BEGIN')
        data_version = db.execute('PRAGMA data_version').fetchone()[0]
        db.execute('SELECT COUNT(*) FROM campaign_registrations').fetchone()
        with closing(sqlite3.connect(backup)) as copy:
            db.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup verification failed; source remains unchanged.')
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        if db.execute('PRAGMA data_version').fetchone()[0] != data_version:
            raise ValueError('Database changed during backup; stop writers and retry.')
        if inspect(db) != 'legacy':
            raise ValueError('Schema changed during backup; inspect it before retrying.')
        indexes = [row[0] for row in db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='campaign_registrations' AND sql IS NOT NULL")]
        db.execute(registration_sql('campaign_registrations_member_v1'))
        columns = ','.join(LEGACY_COLUMNS)
        db.execute('INSERT INTO campaign_registrations_member_v1(' + columns + ') SELECT ' + columns + ' FROM campaign_registrations')
        left = db.execute('SELECT ' + columns + ' FROM campaign_registrations ORDER BY id')
        right = db.execute('SELECT ' + columns + ' FROM campaign_registrations_member_v1 ORDER BY id')
        if any(old != new for old, new in zip_longest(left, right)):
            raise ValueError('Copied registration fields differ; migration rolled back.')
        db.execute('DROP TABLE campaign_registrations')
        db.execute('ALTER TABLE campaign_registrations_member_v1 RENAME TO campaign_registrations')
        for sql in indexes:
            db.execute(sql)
        create_member_metadata(db, int(time.time()))
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Migration validation failed; source changes rolled back.')
        db.commit()
        return {'version': VERSION, 'changed': True, 'state': 'current', 'registrations': count, 'backup': str(backup)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-service-stopped', action='store_true')
    args = parser.parse_args()
    try:
        result = migrate(args.db, args.backup_dir, args.apply, args.confirm_service_stopped)
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Only our fixed validation text is safe; SQLite errors can embed data.
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else 'Migration failed; inspect the private backup and source locally.'}))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
