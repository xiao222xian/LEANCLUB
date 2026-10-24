"""Focused HTTP checks for QR publishing, privacy and expiry; isolated databases."""
import base64
import io
import unittest
from PIL import Image
import test_campaign as campaign_tests


class GroupQRTests(unittest.TestCase):
    setUpClass = classmethod(campaign_tests.CampaignHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(campaign_tests.CampaignHTTPTests.tearDownClass.__func__)
    setUp = campaign_tests.CampaignHTTPTests.setUp
    tearDown = campaign_tests.CampaignHTTPTests.tearDown
    request = campaign_tests.CampaignHTTPTests.request
    setup_admin = campaign_tests.CampaignHTTPTests.setup_admin

    def write(self, **data):
        data.setdefault('revision', self.store.groups.listing()['revision'])
        return self.request('POST', '/api/admin/group-qrs', data, {'X-CSRF-Token': self.csrf or ''})

    def add(self, label='合成测试群', **extra):
        out = io.BytesIO(); Image.new('RGB', (128, 128), 'white').save(out, 'PNG')
        status, result, _ = self.write(action='save', label=label, image='data:image/png;base64,' + base64.b64encode(out.getvalue()).decode(), **extra)
        self.assertEqual(status, 200)
        return next(x['id'] for x in result['items'] if x['label'] == label)

    def test_auth_csrf_and_private_drafts(self):
        self.assertEqual(self.request('GET', '/api/admin/group-qrs')[0], 401)
        self.assertEqual(self.write(action='disable')[0], 401)
        self.setup_admin()
        self.assertEqual(self.request('POST', '/api/admin/group-qrs', dict(action='disable', revision=0))[0], 403)
        group_id = self.add()
        self.assertIsNone(self.request('GET', '/api/campaign/group-qr')[1]['group'])
        self.assertEqual(self.request('GET', '/api/campaign/group-qr/' + group_id + '/1')[0], 404)
        self.cookies = {}
        self.assertEqual(self.request('GET', '/api/admin/group-qrs/' + group_id + '/image')[0], 401)

    def test_switch_single_active_version_conflict_and_delete_guard(self):
        self.setup_admin(); a = self.add('A'); b = self.add('B')
        revision = self.store.groups.listing()['revision']
        self.assertEqual(self.write(action='activate', id=a, revision=revision)[0], 200)
        self.assertEqual(self.write(action='activate', id=b, revision=revision)[0], 409)
        self.assertEqual(self.write(action='delete', id=a)[0], 409)
        self.assertEqual(self.write(action='activate', id=b)[0], 200)
        public = self.request('GET', '/api/campaign/group-qr')[1]['group']
        self.assertEqual(public['id'], b); self.assertNotIn('label', public)
        self.assertEqual(self.request('GET', '/api/campaign/group-qr/' + a + '/1')[0], 404)
        status, raw, headers = self.request('GET', public['imageUrl'])
        self.assertEqual(status, 200); self.assertTrue(raw.startswith(b'\x89PNG'))
        self.assertIn(('Cache-Control', 'no-store'), headers)
        self.assertEqual(self.write(action='disable')[0], 200)
        self.assertIsNone(self.request('GET', '/api/campaign/group-qr')[1]['group'])

    def test_expiry_and_event_retry_do_not_count_as_group_members(self):
        self.setup_admin(); expires = int(self.store.clock()) + 100
        group_id = self.add(expiresAt=expires); self.write(action='activate', id=group_id)
        event = dict(id=group_id, kind='view', eventId='synthetic-event-1234567890')
        for _ in range(2): self.assertEqual(self.request('POST', '/api/campaign/group-qr/events', event)[0], 200)
        self.assertEqual(self.store.groups.listing()['items'][0]['views'], 1)
        self.store.clock = lambda: expires + 1
        self.assertIsNone(self.request('GET', '/api/campaign/group-qr')[1]['group'])
        self.assertEqual(self.request('GET', '/api/campaign/group-qr/' + group_id + '/1')[0], 404)
        self.assertEqual(self.write(action='activate', id=group_id)[0], 400)
        self.assertEqual(self.store.stats()['totalVotes'], 0)

    def test_invalid_upload_and_image_replacement(self):
        self.setup_admin()
        self.assertEqual(self.write(action='save', label='Bad', image='data:image/svg+xml;base64,aaaa')[0], 400)
        group_id = self.add(); self.write(action='activate', id=group_id)
        old_url = self.store.groups.active()['imageUrl']
        out = io.BytesIO(); Image.new('RGB', (160, 160), 'black').save(out, 'PNG')
        self.assertEqual(self.write(action='save', id=group_id, label='替换图', image='data:image/png;base64,' + base64.b64encode(out.getvalue()).decode())[0], 200)
        self.assertEqual(self.request('GET', old_url)[0], 404)
        self.assertEqual(self.store.groups.active()['imageVersion'], 2)


if __name__ == '__main__': unittest.main()
