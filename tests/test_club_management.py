"""Synthetic migration, concurrency and public/private boundary checks."""
import importlib.util
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from store import Store, Problem
from club_management import ClubManagement
spec = importlib.util.spec_from_file_location('import_legacy_clubs', ROOT / 'scripts/import_legacy_clubs.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root / 'old.sqlite3'
        self.dst = self.root / 'new.sqlite3'
        with closing(sqlite3.connect(self.src)) as db, db:
            db.execute('CREATE TABLE clubs(id TEXT PRIMARY KEY,name TEXT,name_key TEXT,contact TEXT,count INTEGER,qr TEXT,edit_hash TEXT,updated INTEGER,revision INTEGER)')
            db.execute('CREATE TABLE events(ip TEXT,kind TEXT,school TEXT,stamp INTEGER)')
            db.execute('INSERT INTO clubs VALUES(?,?,?,?,?,?,?,?,?)', ('school-a', '测试大学', '测试大学', '仅测试联系', 123, 'original-qr', 'synthetic-digest', 1, 3))
            db.execute('INSERT INTO events VALUES(?,?,?,?)', ('192.0.2.1', 'read', 'school-a', 2))
        self.meta = {'school-a': {'name': '测试大学', 'en': 'Test Campus', 'region': '中国大陆', 'alias': 'test'}}

    def tearDown(self):
        self.temp.cleanup()

    def migrate(self):
        return migration.migrate(self.src, self.dst, self.meta, [{'id': 42, 'name': '测试大学'}])

    def test_complete_import_idempotent_and_isolated_from_live_events(self):
        result = self.migrate()
        self.assertEqual((result['clubs'], result['events'], result['totalCount'], result['directoryMatches']), (1, 1, 123, 1))
        store = Store(str(self.dst), [])
        with store.connect() as db, closing(sqlite3.connect(self.src)) as old:
            self.assertEqual(tuple(db.execute('SELECT * FROM clubs').fetchone()), old.execute('SELECT * FROM clubs').fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM club_history_events').fetchone()[0], 1)
            original = json.loads(db.execute('SELECT payload FROM club_import_records').fetchone()[0])
            self.assertEqual(original['edit_hash'], 'synthetic-digest')
        self.assertTrue(self.migrate()['alreadyImported'])
        summary = store.summary()[0]
        self.assertEqual(summary['schoolId'], 42)
        for key in ('contact', 'qr', 'edit_hash'):
            self.assertNotIn(key, summary)

    def test_existing_destination_is_never_overwritten(self):
        target = Store(str(self.dst), [])
        with target.connect() as db:
            db.execute("INSERT INTO clubs VALUES('other-id','测试大学','测试大学','new contact',999,'','hash',3,1)")
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertEqual(target.summary()[0]['count'], 999)
        with target.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM club_history_events').fetchone()[0], 0)

    def test_management_visibility_revision_and_import_do_not_revert_edits(self):
        self.migrate()
        store = Store(str(self.dst), [])
        manager = store.management
        club = manager.detail('school-a')['club']
        body = {key: club[key] for key in ('name', 'contact', 'count', 'en', 'region', 'alias', 'revision', 'schoolId')}
        body.update(visible=False, removeQr=False, count=150)
        saved = manager.save('school-a', body, 'synthetic-admin')
        self.assertEqual(saved['club']['count'], 150)
        self.assertEqual(store.summary(), [])
        self.assertIsNone(store.profile('school-a', '192.0.2.2'))
        with self.assertRaises(Problem) as ctx:
            manager.save('school-a', body, 'synthetic-admin')
        self.assertEqual(ctx.exception.status, 409)
        self.assertTrue(self.migrate()['alreadyImported'])
        self.assertEqual(manager.detail('school-a')['club']['count'], 150)
        self.assertNotIn('edit_hash', saved['club'])
        self.assertEqual(manager.history(1)['items'][0]['network'], '192.0.*.*')
        self.assertNotIn('ip', manager.history(1)['items'][0])

    def test_upload_rejects_svg_fake_and_oversized_images(self):
        for raw, content_type in [(b'<svg/>', 'image/svg+xml'), (b'not a PNG', 'image/png'), (b'x' * (2 * 1024**2 + 1), 'image/png')]:
            with self.assertRaises(Problem):
                ClubManagement.image(raw, content_type)
        output = io.BytesIO(); Image.new('RGB', (64, 64)).save(output, 'PNG')
        self.assertTrue(ClubManagement.image(output.getvalue(), 'image/png').startswith('data:image/png;base64,'))


if __name__ == '__main__':
    unittest.main()
