"""Isolated, synthetic campaign acceptance/security regression tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore, SESSION_AGE, VISITOR_AGE, digest
from campaign_http import CampaignAPI
from store import Store, Problem


def png(width=800, height=1200, metadata=False):
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text('private-test-marker', 'synthetic-metadata-must-be-stripped')
    Image.new('RGB', (width, height), '#274b3c').save(output, format='PNG', pnginfo=info)
    return output.getvalue()


class CampaignStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1792674000
        self.path = Path(self.tmp.name) / 'campaign.sqlite3'
        self.store = CampaignStore(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def visitor(self):
        return self.store.visitor(None)[0]

    def test_database_and_uploaded_media_cannot_be_under_public_web_root(self):
        public = Path(__file__).resolve().parents[1] / 'web' / 'unsafe-test-path'
        for database, media in ((public / 'db.sqlite3', None), (self.path, public)):
            with self.assertRaises(ValueError):
                CampaignStore(database, media)
        self.assertFalse(public.exists())

    def test_origin_must_be_exact_browser_origin_without_trailing_slash(self):
        for origin in ('http://127.0.0.1:4180/', 'https://example.test/path',
                       'https://example.test?query=1', 'https://example.test#fragment',
                       'https://user@example.test', 'file:///tmp/site'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                CampaignAPI(self.store, origin)
        origin = 'http://127.0.0.1:4180'
        self.assertEqual(CampaignAPI(self.store, origin).origin, origin)

    def test_persistent_unique_vote_change_and_idempotency(self):
        visitor, token = self.store.visitor(None)
        self.assertEqual(self.store.visitor(token), (visitor, None))
        self.store.cast_vote(visitor, 'shenzhen')
        self.store.cast_vote(visitor, 'shenzhen')
        self.store.cast_vote(visitor, 'guangzhou')
        reloaded = CampaignStore(self.path, clock=lambda: self.now)
        self.assertEqual(reloaded.vote(visitor), {'city': 'guangzhou'})
        stats = reloaded.stats()
        self.assertEqual(stats['totalVotes'], 1)
        self.assertEqual([v['count'] for v in stats['votes']], [0, 0, 1])
        self.assertEqual([v['share'] for v in stats['votes']], [0, 0, 100])

    def test_concurrent_votes_cannot_double_count(self):
        visitor = self.visitor()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda city: self.store.cast_vote(visitor, city), ['shenzhen', 'zhuhai', 'guangzhou'] * 8))
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        self.assertIn(self.store.vote(visitor)['city'], ('shenzhen', 'zhuhai', 'guangzhou'))

    def test_forged_and_expired_visitor_tokens_are_replaced(self):
        visitor, token = self.store.visitor(None)
        forged, replacement = self.store.visitor('x' * 43)
        self.assertNotEqual(forged, visitor)
        self.assertNotEqual(replacement, 'x' * 43)
        self.now += VISITOR_AGE + 1
        renewed, fresh = self.store.visitor(token)
        self.assertNotEqual(renewed, visitor)
        self.assertIsNotNone(fresh)
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM campaign_visitors WHERE token_hash=?', (token,)).fetchone())

    def test_invalid_city_uuid_and_page_do_not_count(self):
        visitor = self.visitor()
        for city in ('beijing', None, 12, {'city': 'shenzhen'}):
            with self.assertRaises(Problem):
                self.store.cast_vote(visitor, city)
        for page, event in [('admin', str(uuid.uuid4())), ('home', 'bad'), ('home', None)]:
            with self.assertRaises(Problem):
                self.store.track(visitor, page, event)
        self.assertEqual(self.store.stats()['totalVotes'], 0)
        self.assertEqual(self.store.stats()['metrics']['home']['pv'], 0)

    def test_page_dedup_browser_uv_and_shanghai_day_boundary(self):
        # 15:59:59 UTC is 23:59:59 in China; UTC itself has not changed date.
        self.now = int(datetime.fromisoformat('2026-10-21T15:59:59+00:00').timestamp())
        first, second = self.visitor(), self.visitor()
        event = str(uuid.uuid4())
        self.store.track(first, 'home', event)
        self.store.track(first, 'home', event)
        self.store.cast_vote(first, 'shenzhen')
        self.now += 2
        self.store.track(first, 'home', str(uuid.uuid4()))
        self.store.track(second, 'home', str(uuid.uuid4()))
        self.store.track(second, 'vote', str(uuid.uuid4()))
        self.store.cast_vote(first, 'zhuhai')
        self.store.cast_vote(second, 'guangzhou')
        stats = self.store.stats()
        self.assertEqual(stats['metrics']['home'], {'pv': 3, 'uv': 2, 'todayPv': 2, 'todayUv': 2})
        self.assertEqual(len(stats['daily']), 14)
        self.assertEqual(stats['daily'][-2], {'date': '2026-10-21', 'homePv': 1, 'votePv': 0, 'newVotes': 1})
        self.assertEqual(stats['daily'][-1], {'date': '2026-10-22', 'homePv': 2, 'votePv': 1, 'newVotes': 1})
        self.assertEqual(stats['daily'][0]['newVotes'], 0)

    def test_password_hash_session_expiry_reset_and_case_insensitive_username(self):
        password = 'Synthetic-test-only-481'
        self.store.create_admin('Tester', password)
        token, session = self.store.login('tester', password, 'synthetic-ip')
        self.assertEqual(session['username'], 'Tester')
        self.assertEqual(self.store.session(token), session)
        with self.store.connect() as db:
            self.assertNotIn(password, db.execute('SELECT password_hash FROM campaign_admins').fetchone()[0])
            self.assertEqual(db.execute('SELECT token_hash FROM campaign_sessions').fetchone()[0], digest(token))
        self.now += SESSION_AGE + 1
        self.assertIsNone(self.store.session(token))
        token, _ = self.store.login('Tester', password, 'new-ip')
        self.store.reset_password('tester', 'Synthetic-replacement-661')
        self.assertIsNone(self.store.session(token))
        with self.assertRaises(Problem):
            self.store.login('Tester', password, 'wrong-password-ip')

    def test_first_admin_setup_serializes_concurrent_attempts(self):
        def create(number):
            try:
                self.store.create_admin('test%d' % number, 'Synthetic-password-719', first_only=True)
                return 201
            except Problem as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(create, range(3)))
        self.assertEqual(results.count(201), 1)
        self.assertEqual(results.count(409), 2)

    def test_password_reset_wins_against_inflight_old_login(self):
        self.store.create_admin('tester', 'Synthetic-password-719')
        with self.store.connect() as db:
            old_hash = db.execute('SELECT password_hash FROM campaign_admins').fetchone()[0]
        self.store.reset_password('tester', 'Synthetic-new-password-719')
        with self.assertRaises(Problem):
            self.store.new_session('tester', password_version=old_hash)

    def test_image_reencode_metadata_stripping_position_reset_and_history(self):
        result = self.store.replace_poster('shenzhen', png(metadata=True), 'image/png')
        self.assertFalse(result['isDefault'])
        name = result['posterUrl'].rsplit('/', 1)[1]
        raw = self.store.media(name)
        self.assertNotIn(b'synthetic-metadata', raw)
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, (800, 1200))
            self.assertNotIn('exif', image.info)
        with Image.open(io.BytesIO(self.store.media(result['posterMobileUrl'].rsplit('/', 1)[1]))) as image:
            self.assertEqual(image.size, (640, 960))
        moved = self.store.position('shenzhen', 33.5, 70)
        self.assertEqual(moved['posterUrl'], result['posterUrl'])
        self.assertEqual((moved['positionX'], moved['positionY']), (33.5, 70))
        reset = self.store.reset_poster('shenzhen')
        self.assertTrue(reset['isDefault'])
        self.assertEqual(reset['posterUrl'], '/city-vote/assets/alan-v1/shenzhen.webp')
        self.assertEqual(reset['posterMobileUrl'], '/city-vote/assets/alan-v1/shenzhen-640.webp')
        self.assertEqual(reset['positionX'], 50)
        self.assertEqual(self.store.media(name), raw)  # Previous upload retained.
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_poster_history').fetchone()[0], 2)

    def test_fresh_database_default_posters_are_packaged_for_both_viewports(self):
        web_root = Path(__file__).resolve().parents[1] / 'web' / 'creator'
        for city in self.store.cities():
            with self.subTest(city=city['id']):
                self.assertTrue(city['isDefault'])
                for field, suffix, expected_width in (
                    ('posterUrl', '.webp', 941),
                    ('posterMobileUrl', '-640.webp', 640),
                ):
                    url = '/city-vote/assets/alan-v1/' + city['id'] + suffix
                    self.assertEqual(city[field], url)
                    with Image.open(web_root / url.lstrip('/')) as image:
                        self.assertEqual(image.format, 'WEBP')
                        self.assertEqual(image.width, expected_width)
                        self.assertGreater(image.height, image.width)
                        image.verify()

    def test_zhuhai_default_and_reset_focus_preserve_saved_custom_position(self):
        cities = {city['id']: city for city in self.store.cities()}
        self.assertEqual(cities['zhuhai']['positionY'], 0)
        self.assertEqual(cities['shenzhen']['positionY'], 45)
        self.assertEqual(cities['guangzhou']['positionY'], 45)
        self.store.position('zhuhai', 61, 72)
        reloaded = CampaignStore(self.path, clock=lambda: self.now)
        self.assertEqual(reloaded._city('zhuhai')['positionY'], 72)
        reset = reloaded.reset_poster('zhuhai')
        self.assertEqual((reset['positionX'], reset['positionY']), (50, 0))
        self.assertEqual(reset['posterUrl'], '/city-vote/assets/alan-v1/zhuhai.webp')
        self.assertTrue(reset['isDefault'])

    def test_poster_rejects_active_fake_truncated_animated_and_invalid_position(self):
        invalid = [(b'<svg onload="alert(1)"/>', 'image/svg+xml'), (b'\x89PNG\r\n\x1a\ninvalid', 'image/png'),
                   (png(), 'image/jpeg'), (png(32, 64), 'image/png'), (png(64, 10001), 'image/png'),
                   (png()[:40], 'image/png'), (b'x' * (8 * 1024 * 1024 + 1), 'image/png')]
        animation = io.BytesIO()
        Image.new('RGB', (100, 100), 'red').save(animation, format='WEBP', save_all=True, append_images=[Image.new('RGB', (100, 100), 'blue')], duration=100, loop=0)
        invalid.append((animation.getvalue(), 'image/webp'))
        for raw, mime in invalid:
            with self.subTest(mime=mime, size=len(raw)), self.assertRaises(Problem):
                self.store.replace_poster('zhuhai', raw, mime)
        for x, y in [(True, 50), (float('nan'), 50), (50, 101), (-1, 30)]:
            with self.assertRaises(Problem):
                self.store.position('zhuhai', x, y)
        self.assertTrue(self.store._city('zhuhai')['isDefault'])
        self.assertEqual(list(self.store.media_dir.iterdir()), [])

    def test_media_path_traversal_is_rejected(self):
        for name in ('../campaign.sqlite3', '%2e%2e%2fsecret', 'x.webp', 'a' * 32 + '.svg'):
            with self.assertRaises(Problem) as failure:
                self.store.media(name)
            self.assertEqual(failure.exception.status, 404)

    def test_school_tables_remain_compatible(self):
        schools = Store(str(self.path), [{'id': 'sample-school', 'name': '合成测试大学'}])
        result = schools.save({'school': '合成测试大学', 'contact': '合成测试负责人', 'count': 3, 'qr': '', 'consent': True}, 'synthetic-ip')
        self.store.cast_vote(self.visitor(), 'zhuhai')
        self.assertEqual(schools.edit_profile(result['editKey'])['count'], 3)
        self.assertEqual(self.store.stats()['totalVotes'], 1)


class CampaignHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'CLUB_DB': str(Path(cls.bootstrap.name) / 'bootstrap.sqlite3'), 'CLUB_ORIGIN': 'http://127.0.0.1:4180'}):
            cls.server_module = importlib.import_module('server')
        class QuietHandler(cls.server_module.Handler):
            def log_message(self, *args):
                pass
        cls.httpd = cls.server_module.Server(('127.0.0.1', 0), QuietHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.bootstrap.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CampaignStore(Path(self.tmp.name) / 'data.sqlite3')
        self.origin = 'http://127.0.0.1:4180'
        self.api = CampaignAPI(self.store, self.origin, allow_local_setup=True)
        self.patch = patch.object(self.server_module, 'CAMPAIGN', self.api)
        self.patch.start()
        self.cookies = {}
        self.csrf = None

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def request(self, method, path, data=None, headers=None, raw=None):
        outgoing = {'Cookie': '; '.join('%s=%s' % pair for pair in self.cookies.items())}
        body = raw
        if method == 'POST':
            outgoing.update({'Origin': self.origin, 'Content-Type': 'application/json'})
            if body is None:
                body = json.dumps(data if data is not None else {}).encode()
        if headers:
            outgoing.update(headers)
        connection = HTTPConnection('127.0.0.1', self.port, timeout=20)
        connection.request(method, path, body=body, headers=outgoing)
        response = connection.getresponse()
        raw_result = response.read()
        result_headers = response.getheaders()
        for key, value in result_headers:
            if key.lower() == 'set-cookie':
                parsed = SimpleCookie()
                parsed.load(value)
                for name, value in parsed.items():
                    self.cookies[name] = value.value
        result = json.loads(raw_result) if response.getheader('Content-Type', '').startswith('application/json') else raw_result
        connection.close()
        return response.status, result, result_headers

    def setup_admin(self):
        status, result, headers = self.request('POST', '/api/admin/setup', {'username': 'tester', 'password': 'Synthetic-only-password-611'})
        self.assertEqual(status, 201)
        self.csrf = result['csrfToken']
        return headers

    def test_public_vote_roundtrip_and_cookie_flags(self):
        status, result, headers = self.request('GET', '/api/campaign')
        self.assertEqual(status, 200)
        self.assertIsNone(result['vote'])

        self.assertEqual(len(result['cities']), 3)
        self.assertEqual(result['cities'][2]['positionX'], 65)
        cookie = next(value for key, value in headers if key == 'Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=31536000', cookie)
        for city in ('shenzhen', 'shenzhen', 'zhuhai'):
            self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': city})[0], 200)
        self.assertEqual(self.request('GET', '/api/campaign')[1]['vote'], {'city': 'zhuhai'})
        self.assertEqual(self.store.stats()['totalVotes'], 1)


    def test_school_admin_requires_session_csrf_and_current_revision(self):
        directory = Store(str(self.store.path), [])
        saved = directory.save({'school': '测试大学', 'contact': '测试联系', 'count': 15, 'consent': True}, '192.0.2.1')
        path = '/api/admin/schools/' + saved['id']
        for endpoint in ['/api/admin/schools', path, '/api/admin/schools/history']:
            self.assertEqual(self.request('GET', endpoint)[0], 401)
        self.setup_admin()
        status, detail, _ = self.request('GET', path)
        self.assertEqual(status, 200)
        self.assertNotIn('edit_hash', detail['club'])
        body = {k: detail['club'][k] for k in ('name', 'contact', 'count', 'en', 'region', 'alias', 'revision', 'schoolId')}
        body.update(count=25, visible=False, removeQr=False)
        self.assertEqual(self.request('POST', path, body)[0], 403)
        self.assertEqual(self.request('POST', path, body, {'X-CSRF-Token': self.csrf, 'Origin': 'https://foreign.test'})[0], 403)
        self.assertEqual(self.request('POST', path, body, {'X-CSRF-Token': self.csrf})[0], 200)
        self.assertEqual(directory.summary(), [])
        self.assertEqual(self.request('POST', path, body, {'X-CSRF-Token': self.csrf})[0], 409)
        headers = {'X-CSRF-Token': self.csrf, 'Content-Type': 'image/png', 'X-Club-Revision': '2'}
        self.assertEqual(self.request('POST', path + '/qr', headers=headers, raw=png(64,64))[0], 200)

    def test_anonymous_admin_access_origin_and_csrf_protection(self):
        for path in ('/api/admin/stats', '/api/admin/posters'):
            self.assertEqual(self.request('GET', path)[0], 401)
        self.assertEqual(self.request('POST', '/api/admin/setup', {'username': 'tester', 'password': 'Synthetic-password-451'}, {'Origin': 'https://foreign.test'})[0], 403)
        self.assertFalse(self.store.has_admin())
        self.setup_admin()
        self.assertEqual(self.request('POST', '/api/admin/posters/shenzhen/reset')[0], 403)
        self.assertEqual(self.request('POST', '/api/admin/posters/shenzhen/reset', headers={'X-CSRF-Token': self.csrf, 'Origin': 'https://foreign.test'})[0], 403)
        self.assertEqual(self.request('POST', '/api/admin/posters/shenzhen/reset', headers={'X-CSRF-Token': self.csrf})[0], 200)

    def test_setup_availability_once_and_production_disabled(self):
        session = self.request('GET', '/api/admin/session')[1]
        self.assertTrue(session['setupRequired'])
        self.assertTrue(session['setupAvailable'])
        self.setup_admin()
        session = self.request('GET', '/api/admin/session')[1]
        self.assertTrue(session['authenticated'])
        self.assertFalse(session['setupAvailable'])
        self.assertFalse(session['setupRequired'])
        self.assertEqual(self.request('POST', '/api/admin/setup', {'username': 'another', 'password': 'Synthetic-password-631'})[0], 409)
        production = CampaignAPI(self.store, 'https://www.leanschool.net', allow_local_setup=True)
        self.assertFalse(production.local_setup)
        self.assertFalse(CampaignAPI(self.store, self.origin, allow_local_setup=False).local_setup)
        self.api.local_setup = False
        self.assertEqual(self.request('POST', '/api/admin/setup', {'username': 'another', 'password': 'Synthetic-password-631'})[0], 403)

    def test_login_success_rotates_session_and_keeps_visitor_vote(self):
        self.request('GET', '/api/campaign')
        visitor = self.cookies['lean_visitor']
        self.request('POST', '/api/campaign/vote', {'city': 'shenzhen'})
        self.setup_admin()
        previous_session = self.cookies['lean_admin']
        status, result, _ = self.request('POST', '/api/admin/login', {'username': 'tester', 'password': 'Synthetic-only-password-611'})
        self.assertEqual(status, 200)
        self.assertTrue(result['authenticated'])
        self.assertNotEqual(self.cookies['lean_admin'], previous_session)
        self.assertIsNone(self.store.session(previous_session))
        self.assertEqual(self.cookies['lean_visitor'], visitor)
        self.assertEqual(self.request('GET', '/api/campaign')[1]['vote'], {'city': 'shenzhen'})

    def test_authenticated_stats_and_logout_revokes_server_session(self):
        self.setup_admin()
        session_cookie = self.cookies['lean_admin']
        self.assertEqual(self.request('GET', '/api/admin/stats')[0], 200)
        self.assertEqual(self.request('POST', '/api/admin/logout', headers={'X-CSRF-Token': self.csrf})[0], 200)
        self.assertFalse(self.request('GET', '/api/admin/session')[1]['authenticated'])
        self.cookies['lean_admin'] = session_cookie
        self.assertEqual(self.request('GET', '/api/admin/stats')[0], 401)

    def test_login_rate_limit_cannot_be_bypassed_with_untrusted_header(self):
        self.store.create_admin('tester', 'Synthetic-only-password-611')
        for index in range(10):
            status = self.request('POST', '/api/admin/login', {'username': 'tester', 'password': 'incorrect'}, {'X-Real-IP': '192.0.2.%d' % (index + 1)})[0]
            self.assertEqual(status, 401)
        self.assertEqual(self.request('POST', '/api/admin/login', {'username': 'tester', 'password': 'Synthetic-only-password-611'})[0], 429)

    def test_upload_download_reposition_reset_and_reject_bad_image(self):
        self.setup_admin()
        headers = {'X-CSRF-Token': self.csrf, 'Content-Type': 'image/png'}
        status, result, _ = self.request('POST', '/api/admin/posters/guangzhou', headers=headers, raw=png())
        self.assertEqual(status, 200)
        media_url = result['city']['posterUrl']
        status, raw, media_headers = self.request('GET', media_url)
        self.assertEqual(status, 200)
        self.assertEqual(dict(media_headers)['Content-Type'], 'image/webp')
        self.assertIn('immutable', dict(media_headers)['Cache-Control'])
        self.assertEqual(dict(media_headers)['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(raw.startswith(b'RIFF'))
        self.assertEqual(self.request('GET', '/api/campaign')[1]['cities'][2]['posterUrl'], media_url)
        status, result, _ = self.request('POST', '/api/admin/posters/guangzhou/position', {'x': 77, 'y': 60}, {'X-CSRF-Token': self.csrf})
        self.assertEqual(status, 200)
        self.assertEqual(result['city']['positionX'], 77)
        self.assertEqual(self.request('POST', '/api/admin/posters/guangzhou', headers=headers, raw=b'<html>bad</html>')[0], 400)
        self.assertEqual(self.request('POST', '/api/admin/posters/guangzhou/reset', headers={'X-CSRF-Token': self.csrf})[0], 200)
        self.assertEqual(self.request('GET', media_url)[0], 200)
        self.assertTrue(self.request('GET', '/api/campaign')[1]['cities'][2]['isDefault'])

    def test_json_errors_oversized_request_unknown_route_and_analytics_dedup(self):
        self.request('GET', '/api/campaign')
        event = {'page': 'home', 'eventId': str(uuid.uuid4())}
        for _ in range(2):
            self.assertEqual(self.request('POST', '/api/analytics/page', event)[0], 200)
        self.assertEqual(self.store.stats()['metrics']['home']['pv'], 1)
        for body in (b'[]', b'NaN', b'{"city":"shenzhen","city":"zhuhai"}', b'{bad'):
            self.assertEqual(self.request('POST', '/api/campaign/vote', raw=body)[0], 400)
        self.assertEqual(self.request('POST', '/api/campaign/vote', raw=b' ' * 33000)[0], 413)
        self.assertEqual(self.request('GET', '/api/admin/unknown')[0], 404)
        self.assertEqual(self.request('POST', '/api/admin/unknown')[0], 404)
        self.assertEqual(self.request('GET', '/api/media/../data.sqlite3')[0], 404)

    def test_https_session_cookie_secure_flag(self):
        self.api.secure = True
        headers = self.setup_admin()
        cookie = next(value for key, value in headers if key == 'Set-Cookie')
        self.assertIn('Secure', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=28800', cookie)


if __name__ == '__main__':
    unittest.main()
