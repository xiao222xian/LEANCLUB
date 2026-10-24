"""Idempotent, transactional import; private records never enter Git or stdout."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from store import Store

SOURCE = 'leanmuscleclub.com:2026-10-24'
COLUMNS = ('id', 'name', 'name_key', 'contact', 'count', 'qr', 'edit_hash', 'updated', 'revision')


def migrate(source, destination, metadata, directory=()):
    src = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    src.execute('PRAGMA query_only=ON')
    if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Source integrity check failed')
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables != {'clubs', 'events'}:
        raise ValueError('Unexpected source tables; review all data before import')
    clubs = [dict(r) for r in src.execute('SELECT * FROM clubs ORDER BY id')]
    events = [dict(r) for r in src.execute('SELECT rowid source_rowid,* FROM events ORDER BY rowid')]
    src.close()
    payload = json.dumps({'clubs': clubs, 'events': events, 'metadata': metadata}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    catalog = {}
    for item in directory:
        catalog.setdefault(Store.name_key(item['name']), []).append(item['id'])
    target = Store(str(destination), [])
    with target.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT digest FROM club_imports WHERE source=?', (SOURCE,)).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise ValueError('Source changed since first import; manual reconciliation required')
            return {'alreadyImported': True, 'clubs': len(clubs), 'events': len(events)}
        # Abort the whole transaction on a collision, preserving all target rows.
        for club in clubs:
            if set(club) != set(COLUMNS) or not isinstance(club['count'], int) or club['count'] < 0:
                raise ValueError('Unexpected club shape')
            if db.execute('SELECT 1 FROM clubs WHERE id=? OR name_key=?', (club['id'], club['name_key'])).fetchone():
                raise ValueError('Existing destination school conflict; nothing imported')
        mapped = 0
        for club in clubs:
            db.execute('INSERT INTO clubs(' + ','.join(COLUMNS) + ') VALUES(' + ','.join('?' for _ in COLUMNS) + ')', tuple(club[k] for k in COLUMNS))
            info = metadata.get(club['id'], {})
            if info.get('name') != club['name']:
                info = {}
            matches = catalog.get(Store.name_key(club['name']), [])
            school_id = matches[0] if len(matches) == 1 else None
            mapped += school_id is not None
            db.execute('INSERT INTO club_directory_meta(club_id,en,region,alias,school_id,source) VALUES(?,?,?,?,?,?)',
                (club['id'], info.get('en', ''), info.get('region', ''), info.get('alias', ''), school_id, SOURCE))
            db.execute('INSERT INTO club_import_records VALUES(?,?,?)',
                (SOURCE, club['id'], json.dumps(club, ensure_ascii=False, sort_keys=True)))
            actual = dict(db.execute('SELECT * FROM clubs WHERE id=?', (club['id'],)).fetchone())
            if actual != club:
                raise ValueError('Imported record differs from source')
        db.executemany('INSERT INTO club_history_events VALUES(?,?,?,?,?,?)',
            [(SOURCE, e['source_rowid'], e['ip'], e['kind'], e['school'], e['stamp']) for e in events])
        total = sum(c['count'] for c in clubs)
        db.execute('INSERT INTO club_imports VALUES(?,?,?,?,?,?)', (SOURCE, digest, int(time.time()), len(clubs), len(events), total))
    return {'alreadyImported': False, 'clubs': len(clubs), 'events': len(events), 'totalCount': total,
            'directoryMatches': mapped, 'allClubFieldsVerified': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--metadata', type=Path, required=True)
    p.add_argument('--directory', type=Path)
    a = p.parse_args()
    metadata = {x['id']: x for x in json.loads(a.catalog.read_text())}
    metadata.update(json.loads(a.metadata.read_text()))
    directory = json.loads(a.directory.read_text()) if a.directory else []
    print(json.dumps(migrate(a.source, a.destination, metadata, directory)))
