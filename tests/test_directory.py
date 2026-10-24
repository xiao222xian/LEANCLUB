"""Directory ownership, publication, privacy and overseas/homonym regressions."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from store import Store, Problem


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / 'synthetic.sqlite3'), [])
        self.directory = self.store.directory

    def tearDown(self):
        self.temp.cleanup()

    def profile(self, **changes):
        return dict(kind='group', name='Test Running Group', country='英国', city='London',
                    contact='test@example.invalid', contactPublic=False, profilePublic=True, count=12, **changes)

    def submit(self, profile=None, key=None, revision=0):
        key = key or secrets.token_urlsafe(32)
        return key, self.directory.submit(dict(editKey=key, revision=revision, profile=profile or self.profile()))

    def publish(self, draft):
        return self.directory.review(draft['id'], dict(action='publish', revision=draft['revision']), 'test-admin')

    def test_unknown_overseas_group_pending_before_review_and_contact_private(self):
        key, draft = self.submit()
        self.assertEqual(self.directory.listing(), [])
        self.assertEqual(self.directory.manage(key)['id'], draft['id'])
        approved = self.publish(draft)
        self.assertEqual(approved['status'], 'published')
        self.assertEqual(len(self.directory.listing()), 1)
        self.assertEqual(self.directory.detail(draft['id'])['profile']['contact'], '')
        self.assertEqual(self.store.profile(draft['id'], '192.0.2.1')['contact'], '')
        self.assertNotIn('test@example.invalid', json.dumps(self.directory.listing()))
        self.assertNotIn(key, json.dumps(self.directory.detail(draft['id'], private=True)))

    def test_person_opt_in_and_no_ranking_even_if_public(self):
        p = dict(self.profile(), kind='person', profilePublic=False, count=999)
        key, d = self.submit(p); self.publish(d)
        self.assertEqual(self.directory.listing(), [])
        self.assertEqual(self.store.summary(), [])
        current = self.directory.manage(key)
        with self.assertRaises(Problem):
            self.directory.review(d['id'], dict(action='publish', revision=current['revision'], profile=dict(current['profile'], profilePublic=True)), 'admin')
        _, update = self.submit(dict(current['profile'], profilePublic=True), key, current['revision'])
        self.publish(update)
        self.assertEqual(self.directory.listing()[0]['count'], 0)
        self.assertEqual(self.store.summary(), [])

    def test_same_name_different_countries_and_keys_never_overwrite(self):
        key, a = self.submit(); self.publish(a)
        _, b = self.submit(dict(self.profile(), country='加拿大')); self.publish(b)
        _, c = self.submit(); self.publish(c)
        self.assertEqual(len({x['id'] for x in self.directory.listing()}), 3)
        self.assertEqual(self.directory.manage(key)['id'], a['id'])
        with self.assertRaises(Problem):
            self.directory.manage(secrets.token_urlsafe(32))

    def test_idempotency_and_concurrent_submission(self):
        key = secrets.token_urlsafe(32)
        body = dict(editKey=key, revision=0, profile=self.profile())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.directory.submit(body), range(4)))
        self.assertEqual(len({r['id'] for r in results}), 1)
        self.assertEqual(len(self.directory.listing(private=True)), 1)
        with self.assertRaises(Problem) as ctx:
            self.directory.submit(dict(body, profile=dict(self.profile(), name='Different')))
        self.assertEqual(ctx.exception.status, 409)

    def test_owner_edit_waits_for_review_and_consent_withdrawal_is_immediate(self):
        key, draft = self.submit(dict(self.profile(), contactPublic=True)); self.publish(draft)
        current = self.directory.manage(key)
        _, edit = self.submit(dict(current['profile'], name='New Name', contactPublic=False), key, current['revision'])
        self.assertEqual(self.directory.listing()[0]['name'], 'Test Running Group')
        self.assertEqual(self.store.profile(draft['id'], '192.0.2.1')['contact'], '')
        with self.assertRaises(Problem):
            self.publish(draft)
        self.publish(edit)
        self.assertEqual(self.directory.listing()[0]['name'], 'New Name')
        current = self.directory.manage(key)
        self.submit(dict(current['profile'], profilePublic=False), key, current['revision'])
        self.assertEqual(self.directory.listing(), [])
        self.assertIsNone(self.store.profile(draft['id'], '192.0.2.1'))

    def test_return_note_resubmit_and_hide(self):
        key, draft = self.submit()
        with self.assertRaises(Problem):
            self.directory.review(draft['id'], dict(action='changes', revision=1), 'admin')
        returned = self.directory.review(draft['id'], dict(action='changes', revision=1, note='请补充所在城市'), 'admin')
        self.assertEqual(self.directory.manage(key)['note'], '请补充所在城市')
        _, updated = self.submit(returned['profile'], key, returned['revision'])
        self.assertEqual(updated['note'], '')
        published = self.publish(updated)
        self.directory.review(draft['id'], dict(action='hide', revision=published['revision']), 'admin')
        self.assertEqual(self.directory.listing(), [])

    def test_legacy_management_key_and_count_history_preserved(self):
        old = self.store.save(dict(school='历史测试学校', contact='test-only', count=123, consent=True), '192.0.2.1')
        current = self.directory.manage(old['editKey'])
        with self.store.connect() as db:
            db.execute('INSERT INTO club_directory_meta(club_id,school_id) VALUES(?,42)', (old['id'],))
        current = self.directory.manage(old['editKey'])
        _, edit = self.submit(dict(current['profile'], country='中国'), old['editKey'], current['revision'])
        self.publish(edit)
        self.assertEqual(self.store.summary()[0]['id'], old['id'])
        self.assertEqual(self.store.summary()[0]['count'], 123)
        self.assertEqual(self.store.summary()[0]['schoolId'], 42)
        self.assertEqual(self.store.edit_profile(old['editKey'])['id'], old['id'])

    def test_two_groups_can_share_one_new_school(self):
        p = dict(self.profile(), schoolName='Example International College')
        _, a = self.submit(p); self.publish(a)
        _, b = self.submit(dict(p, name='Second Group')); self.publish(b)
        rows = self.directory.listing()
        schools = [x for x in rows if x['kind'] == 'school']
        groups = [x for x in rows if x['kind'] == 'group']
        self.assertEqual(len(schools), 1)
        self.assertEqual({x['schoolRef'] for x in groups}, {schools[0]['id']})
        self.assertEqual(len(self.store.summary()), 2)

    def test_private_person_does_not_create_public_school(self):
        _, draft = self.submit(dict(self.profile(), kind='person', profilePublic=False, schoolName='Private affiliation'))
        self.publish(draft)
        self.assertEqual(self.directory.listing(), [])

    def test_same_name_schools_in_different_cities_are_separate(self):
        p = dict(self.profile(), schoolName='Same-name College')
        _, a = self.submit(p); self.publish(a)
        _, b = self.submit(dict(p, city='Oxford')); self.publish(b)
        schools = [x for x in self.directory.listing() if x['kind'] == 'school']
        self.assertEqual(len(schools), 2)

    def test_stale_admin_changes_cannot_be_overwritten_by_pending_review(self):
        key, d = self.submit(); self.publish(d)
        current = self.directory.manage(key)
        _, update = self.submit(dict(current['profile'], name='Pending change'), key, current['revision'])
        with self.store.connect() as db:
            db.execute('UPDATE clubs SET revision=revision+1 WHERE id=?', (d['id'],))
        with self.assertRaises(Problem) as error:
            self.publish(update)
        self.assertEqual(error.exception.status, 409)

    def test_validation_and_unsafe_qr(self):
        for changes in (dict(country=''), dict(count=True), dict(count=-1), dict(kind='bad'), dict(qr='data:image/svg+xml;base64,PHN2Zz4='), dict(contactPublic='yes')):
            with self.subTest(changes=changes), self.assertRaises(Problem):
                self.submit(dict(self.profile(), **changes))

    def test_old_public_table_remains_private_during_code_rollback(self):
        key, d = self.submit(); self.publish(d)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT contact FROM clubs WHERE id=?', (d['id'],)).fetchone()[0], '')
        current = self.directory.manage(key)
        self.assertEqual(current['profile']['contact'], 'test@example.invalid')
        self.submit(dict(current['profile'], profilePublic=False), key, current['revision'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT visible FROM club_directory_meta WHERE club_id=?', (d['id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
