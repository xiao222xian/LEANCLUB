"""Offline additive payment migration; startup does not migrate existing stores.

Rollback: stop all writers, restore the verified pre-migration SQLite backup.
After payment work starts, never discard coordination tables independently of
orders: restore/reconcile the entire snapshot under a reviewed recovery plan.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from member_orders_schema import member_schema_ready
from payment_schema import TABLES, VERSION, create_payment_schema, payment_schema_ready


def inspect(db):
    if payment_schema_ready(db):
        return 'current'
    if not member_schema_ready(db):
        raise ValueError('Complete the explicit member-order migration first.')
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables.intersection(TABLES):
        raise ValueError('Partial payment schema found; review it before migration.')
    if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Existing database integrity checks failed.')
    return 'migration-required'


def migrate(db_path, backup_dir, apply=False, confirmed_stopped=False):
    target, backups = Path(db_path).resolve(strict=True), Path(backup_dir).resolve()
    web = (ROOT / 'web').resolve()
    if not target.is_file() or web == target or web in target.parents or web == backups or web in backups.parents:
        raise ValueError('Use an existing private database and a private backup directory.')
    with closing(sqlite3.connect(target, timeout=10)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        state = inspect(db)
        result = {'version': VERSION, 'changed': False, 'state': state}
        if state == 'current' or not apply:
            return result
        if not confirmed_stopped:
            raise ValueError('Stop all writers and explicitly confirm before applying.')
        backups.mkdir(parents=True, exist_ok=True)
        backup = backups / ('before-payments-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex + '.sqlite3')
        db.execute('BEGIN')
        before_version = db.execute('PRAGMA data_version').fetchone()[0]
        db.execute('SELECT COUNT(*) FROM campaign_registrations').fetchone()
        with closing(sqlite3.connect(backup)) as copy:
            db.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed.')
        db.commit()
        try:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA data_version').fetchone()[0] != before_version or inspect(db) != 'migration-required':
                raise ValueError('Database changed during backup; stop writers and retry.')
            # No order field, signing key, ownership, token or existing index is changed.
            create_payment_schema(db, int(time.time()))
            if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Migration checks failed; source changes rolled back.')
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result | {'changed': True, 'state': 'current', 'backup': str(backup)}


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
        print(json.dumps({'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else 'Private database migration failed.'}))
        return 1
    print(json.dumps({'ok': True, **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
