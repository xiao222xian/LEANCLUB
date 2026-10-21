import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from store import Store, Problem


class ClubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'clubs.sqlite3')
        self.store = Store(self.path, [{'id':'school-1', 'name':'测试大学'}])
        self.data = {'school':'测试大学', 'contact':'测试负责人（合成资料）', 'count':20, 'qr':'', 'consent':True}

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_persist_and_public_overwrite(self):
        result = self.store.save(self.data, 'test-ip')
        self.assertEqual(result['id'], 'school-1')
        self.assertEqual(Store(self.path, []).profile('school-1','reader')['contact'], self.data['contact'])
        result2 = self.store.save(dict(self.data, count=31, contact='新的负责人', qr=None), 'other-ip')
        self.assertEqual(result2['revision'], 2)
        self.assertEqual(len(self.store.summary()), 1)
        self.assertEqual(self.store.summary()[0]['count'], 31)
        self.assertEqual(self.store.profile('school-1','reader')['contact'], '新的负责人')
        with self.assertRaises(Problem):
            self.store.edit_profile(result['editKey'])
        edited = dict(self.data, editKey=result2['editKey'], revision=2, count=40)
        self.store.save(edited, 'test-ip')
        with self.assertRaises(Problem):
            self.store.save(edited, 'test-ip')
        public = self.store.profile('school-1','reader')
        self.assertNotIn('edit_hash', public)
        self.assertNotIn('contact', self.store.summary()[0])
        with self.store.connect() as db:
            self.assertNotEqual(db.execute('SELECT edit_hash FROM clubs').fetchone()[0], result['editKey'])

    def test_validation_and_consent(self):
        for change in ({'count':0},{'count':True},{'count':1.2},{'consent':False},{'contact':''},{'school':'x'}):
            with self.assertRaises(Problem):
                self.store.save(dict(self.data, **change),'ip')

    def test_canonical_name_and_cross_school_key(self):
        result=self.store.save(dict(self.data,school=' 测试大学 '),'ip')
        with self.assertRaises(Problem):
            self.store.save(dict(self.data,school='另一所测试大学',editKey=result['editKey'],revision=1),'ip')
        with self.assertRaises(Problem):
            self.store.edit_profile('a'*43)

    def test_distinct_contact_rate_limit(self):
        for index in range(11):
            self.store.save(dict(self.data,school='测试学校%d'%index),'writer-%d'%index)
        ids=[item['id'] for item in self.store.summary()]
        for school_id in ids[:10]:
            self.store.profile(school_id,'same-reader')
        self.store.profile(ids[0],'same-reader')
        with self.assertRaises(Problem) as ctx:
            self.store.profile(ids[10],'same-reader')
        self.assertEqual(ctx.exception.status,429)

    def test_qr_validation(self):
        os.environ['CLUB_DB']=str(Path(self.tmp.name)/'images.sqlite3')
        import server
        self.assertEqual(server.validate_qr(''),'')
        for value in ('data:image/svg+xml;base64,AAAA','data:image/png;base64,AAAA',123):
            with self.assertRaises(Problem):server.validate_qr(value)
        import base64
        original=Path(__file__).resolve().parents[1]/'web/creator/lean-default-douyin.jpg'
        valid='data:image/jpeg;base64,'+base64.b64encode(original.read_bytes()).decode()
        self.assertEqual(server.validate_qr(valid),valid)


if __name__=='__main__':unittest.main()
