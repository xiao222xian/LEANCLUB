import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy/edge' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('edge_release', 'release.py')
reset = load('edge_reset', 'reset-public-data.py')
origin = load('edge_origin', 'configure-origin.py')


class EdgeTests(unittest.TestCase):
    def test_origin_preserves_existing_values(self):
        result = origin.add_origin('CLUB_ORIGIN=https://www.leanschool.net\nCLUB_COMPAT_ORIGINS=https://boji.tensorbot.xyz\nTEST=value\n')
        self.assertIn('CLUB_COMPAT_ORIGINS=https://boji.tensorbot.xyz,https://leanmuscleclub.com\n', result)
        self.assertIn('TEST=value\n', result)
        with self.assertRaises(ValueError):
            origin.add_origin(result)

    def test_reset_preserves_accounts_orders_feedback_and_other_notifications(self):
        db = sqlite3.connect(':memory:')
        for name in (*reset.TABLES, 'campaign_admins', 'campaign_registrations', 'campaign_feedback'):
            db.execute('CREATE TABLE ' + name + '(id INTEGER PRIMARY KEY, content TEXT)')
            db.execute('INSERT INTO ' + name + ' VALUES(1,?)', ('synthetic',))
        for name in reset.PARTIAL:
            db.execute('CREATE TABLE ' + name + '(id INTEGER PRIMARY KEY, kind TEXT)')
            db.execute('INSERT INTO ' + name + ' VALUES(1,?)', (reset.PARTIAL[name][0],))
            db.execute('INSERT INTO ' + name + ' VALUES(2,?)', ('feedback',))
        result = reset.reset(db)
        self.assertEqual(set(result['after'].values()), {0})
        self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_registrations').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT kind FROM campaign_feishu_outbox').fetchall(), [('feedback',)])

    def test_static_integrity_domain_and_filing(self):
        import hashlib
        commit = 'a' * 40
        raw = {'index.html': b'https://www.leanschool.net/', 'site-filing.js': '辽ICP备123'.encode(),
               'city-vote/comments.js': b'hello', 'events/index.html': b'https://www.leanschool.net/events'}
        record = {'commit': commit, 'origin': release.ORIGIN,
                  'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for name, data in raw.items()}}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            package = base / 'source.tar.gz'
            with tarfile.open(package, 'w:gz') as archive:
                for name, data in raw.items():
                    info = tarfile.TarInfo('LEANCLUB-' + commit + '/web/creator/' + name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            self.assertEqual(release.write_static(package, record, base / 'good'), 4)
            self.assertEqual((base / 'good/index.html').read_text(), release.ORIGIN + '/')
            self.assertNotIn('ICP备', (base / 'good/site-filing.js').read_text())
            record['files']['index.html']['sha256'] = '0' * 64
            with self.assertRaises(ValueError):
                release.write_static(package, record, base / 'bad')


if __name__ == '__main__':
    unittest.main()
