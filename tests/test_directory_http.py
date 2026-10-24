"""Exercise the directory through the existing real HTTP/auth boundary."""
import secrets
import unittest
import test_campaign as campaign_tests
from store import Store


class DirectoryHTTPTests(unittest.TestCase):
    setUpClass = classmethod(campaign_tests.CampaignHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(campaign_tests.CampaignHTTPTests.tearDownClass.__func__)
    tearDown = campaign_tests.CampaignHTTPTests.tearDown
    request = campaign_tests.CampaignHTTPTests.request
    setup_admin = campaign_tests.CampaignHTTPTests.setup_admin

    def setUp(self):
        campaign_tests.CampaignHTTPTests.setUp(self)
        self.directory = Store(str(self.store.path), []).directory
        self.body = dict(editKey=secrets.token_urlsafe(32), revision=0, profile=dict(
            kind='group', name='HTTP Test Group', country='加拿大', city='Toronto',
            contact='test@example.invalid', count=5, contactPublic=False, profilePublic=True))

    def test_guest_submit_review_and_shared_public_read(self):
        status, draft, _ = self.request('POST', '/api/directory/submit', self.body)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('GET', '/api/directory')[1]['items'], [])
        path = '/api/admin/directory/' + draft['id']
        for endpoint in ['/api/admin/directory', path]:
            self.assertEqual(self.request('GET', endpoint)[0], 401)
        self.setup_admin()
        review = dict(action='publish', revision=draft['revision'])
        self.assertEqual(self.request('POST', path, review)[0], 403)
        self.assertEqual(self.request('POST', path, review, {'X-CSRF-Token': self.csrf})[0], 200)
        public = self.request('GET', '/api/directory/' + draft['id'])[1]
        self.assertEqual(public['profile']['contact'], '')
        self.assertEqual(self.request('GET', '/api/directory')[1]['items'][0]['id'], draft['id'])

    def test_foreign_origin_and_opaque_origin_rejected(self):
        for origin in ('https://foreign.invalid', 'null'):
            status, _, _ = self.request('POST', '/api/directory/submit', self.body, {'Origin': origin})
            self.assertEqual(status, 403)
        self.assertEqual(self.directory.listing(private=True), [])

    def test_bad_key_does_not_return_private_contact(self):
        self.request('POST', '/api/directory/submit', self.body)
        status, data, _ = self.request('POST', '/api/directory/manage', {'editKey': secrets.token_urlsafe(32)})
        self.assertEqual(status, 404)
        self.assertNotIn('profile', data)
        status, data, _ = self.request('POST', '/api/directory/manage', {'editKey': self.body['editKey']})
        self.assertEqual(status, 200)
        self.assertEqual(data['profile']['contact'], 'test@example.invalid')

    def test_private_person_is_not_exposed_by_legacy_public_endpoint(self):
        self.body['profile'].update(kind='person', profilePublic=False)
        _, draft, _ = self.request('POST', '/api/directory/submit', self.body)
        self.setup_admin()
        self.request('POST', '/api/admin/directory/' + draft['id'], dict(action='publish', revision=1), {'X-CSRF-Token': self.csrf})
        self.assertEqual(self.request('GET', '/api/directory')[1]['items'], [])
        self.assertEqual(self.request('GET', '/api/directory/' + draft['id'])[0], 404)


if __name__ == '__main__':
    unittest.main()
