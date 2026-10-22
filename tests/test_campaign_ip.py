"""IP-deduplicated voting: isolated databases and real HTTP boundaries only."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore, IPVoteConflict, normalized_ip
from campaign_http import CampaignAPI
from store import Problem


class CampaignIPStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'site.sqlite3'
        self.store = CampaignStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def visitor(self):
        return self.store.visitor(None)[0]

    def test_same_ip_new_cookie_is_idempotent_and_cannot_change_owner_vote(self):
        owner, other = self.visitor(), self.visitor()
        self.store.cast_vote(owner, 'shenzhen', '192.0.2.21')
        self.assertEqual(self.store.cast_vote(other, 'shenzhen', '192.0.2.21'), {'city': 'shenzhen'})
        self.assertEqual(self.store.vote_state(other, '192.0.2.21'),
                         {'vote': {'city': 'shenzhen'}, 'canChange': False})
        with self.assertRaises(IPVoteConflict) as caught:
            self.store.cast_vote(other, 'guangzhou', '192.0.2.21')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.vote, {'city': 'shenzhen'})
        self.store.cast_vote(owner, 'zhuhai', '192.0.2.21')
        self.assertEqual(self.store.vote_state(owner, '192.0.2.21'),
                         {'vote': {'city': 'zhuhai'}, 'canChange': True})
        self.assertEqual(self.store.vote(other, '192.0.2.21'), {'city': 'zhuhai'})
        stats = self.store.stats()
        self.assertEqual(stats['totalVotes'], 1)
        self.assertEqual([row['count'] for row in stats['votes']], [0, 1, 0])

    def test_distinct_ips_are_independent_but_browser_network_changes_never_add_votes(self):
        owner, second, third = self.visitor(), self.visitor(), self.visitor()
        self.store.cast_vote(owner, 'shenzhen', '192.0.2.21')
        self.store.cast_vote(second, 'zhuhai', '192.0.2.22')
        self.store.cast_vote(owner, 'guangzhou', '192.0.2.23')
        self.assertEqual(self.store.stats()['totalVotes'], 2)
        for address in ('192.0.2.21', '192.0.2.23'):
            self.assertFalse(self.store.vote_state(third, address)['canChange'])
            with self.assertRaises(IPVoteConflict):
                self.store.cast_vote(third, 'shenzhen', address)
        self.assertTrue(self.store.vote_state(third, '192.0.2.24')['canChange'])
        self.assertIsNone(self.store.vote(third, '192.0.2.24'))

    def test_ipv6_equivalent_forms_and_ipv4_mapped_addresses_share_identity(self):
        owner, other = self.visitor(), self.visitor()
        self.store.cast_vote(owner, 'shenzhen', '2001:0db8:0000:0000:0000:0000:0000:0001')
        self.assertFalse(self.store.vote_state(other, '2001:db8::1')['canChange'])
        self.assertEqual(self.store.ip_hash('192.0.2.1'), self.store.ip_hash('::ffff:c000:201'))
        self.assertEqual(normalized_ip('::ffff:192.0.2.1'), '192.0.2.1')
        for invalid in (None, 'malformed-ip', '192.0.2.01', '192.0.2.1, 192.0.2.2', 'fe80::1%eth0'):
            with self.subTest(invalid=invalid), self.assertRaises(Problem):
                normalized_ip(invalid)

    def test_parallel_first_votes_same_ip_use_one_row(self):
        visitors = [self.visitor() for _ in range(24)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda visitor: self.store.cast_vote(visitor, 'zhuhai', '198.51.100.9'), visitors))
        self.assertEqual(results, [{'city': 'zhuhai'}] * 24)
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        states = [self.store.vote_state(visitor, '198.51.100.9') for visitor in visitors]
        self.assertEqual(sum(state['canChange'] for state in states), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_vote_ips').fetchone()[0], 1)

    def test_parallel_conflicting_cities_same_ip_have_one_winner(self):
        visitors = [self.visitor() for _ in range(18)]
        cities = ['shenzhen', 'zhuhai', 'guangzhou'] * 6
        def submit(pair):
            visitor, city = pair
            try:
                return self.store.cast_vote(visitor, city, '203.0.113.7')['city']
            except IPVoteConflict as error:
                return error.vote['city']
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(submit, zip(visitors, cities)))
        self.assertEqual(len(set(outcomes)), 1)
        self.assertEqual(self.store.stats()['totalVotes'], 1)

    def test_salted_ip_identity_survives_restart_and_does_not_store_plaintext_ip(self):
        owner = self.visitor()
        ip = '203.0.113.73'
        self.store.cast_vote(owner, 'zhuhai', ip)
        restarted = CampaignStore(self.path)
        self.assertEqual(self.store.ip_hash(ip), restarted.ip_hash(ip))
        self.assertFalse(restarted.vote_state(self.visitor(), ip)['canChange'])
        with restarted.connect() as db:
            persisted = '\n'.join(db.iterdump())
            self.assertNotIn(ip, persisted)
            self.assertEqual(db.execute('SELECT length(ip_hash) FROM campaign_votes').fetchone()[0], 64)
        separate = CampaignStore(Path(self.tmp.name) / 'another.sqlite3')
        self.assertNotEqual(self.store.ip_hash(ip), separate.ip_hash(ip))

    def test_legacy_schema_migration_preserves_votes_and_claims_on_next_submission(self):
        legacy_path = Path(self.tmp.name) / 'legacy.sqlite3'
        with sqlite3.connect(legacy_path) as db:
            db.executescript('''
                CREATE TABLE campaign_visitors(token_hash TEXT PRIMARY KEY,created INTEGER NOT NULL,expires INTEGER NOT NULL);
                CREATE TABLE campaign_votes(visitor TEXT PRIMARY KEY REFERENCES campaign_visitors(token_hash),
                    city TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL);
                INSERT INTO campaign_visitors VALUES('legacy-owner',1,9999999999);
                INSERT INTO campaign_visitors VALUES('legacy-other',2,9999999999);
                INSERT INTO campaign_votes VALUES('legacy-owner','shenzhen',1,1);
                INSERT INTO campaign_votes VALUES('legacy-other','zhuhai',2,2);
            ''')
        db.close()
        migrated = CampaignStore(legacy_path)
        self.assertEqual(migrated.stats()['totalVotes'], 2)
        self.assertEqual(migrated.vote('legacy-owner'), {'city': 'shenzhen'})
        with migrated.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_votes WHERE ip_hash IS NULL').fetchone()[0], 2)
        migrated.cast_vote('legacy-owner', 'guangzhou', '192.0.2.33')
        self.assertEqual(migrated.stats()['totalVotes'], 2)
        self.assertFalse(migrated.vote_state(migrated.visitor(None)[0], '192.0.2.33')['canChange'])
        restarted = CampaignStore(legacy_path)
        self.assertEqual(restarted.vote('legacy-other'), {'city': 'zhuhai'})
        self.assertEqual(restarted.stats()['totalVotes'], 2)


class CampaignIPHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'CLUB_DB': str(Path(cls.bootstrap.name) / 'bootstrap.sqlite3'),
                                    'CLUB_ORIGIN': 'http://127.0.0.1:4180'}):
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
        self.api = CampaignAPI(self.store, 'http://127.0.0.1:4180', allow_local_setup=True)
        self.api_patch = patch.object(self.server_module, 'CAMPAIGN', self.api)
        self.proxy_patch = patch.object(self.server_module, 'TRUSTED_PROXIES', set())
        self.api_patch.start()
        self.proxy_patch.start()

    def tearDown(self):
        self.proxy_patch.stop()
        self.api_patch.stop()
        self.tmp.cleanup()

    def request(self, method, path, data=None, cookie='', headers=None):
        outgoing = {'Origin': 'http://127.0.0.1:4180', 'Content-Type': 'application/json', 'Cookie': cookie}
        outgoing.update(headers or {})
        connection = HTTPConnection('127.0.0.1', self.port, timeout=20)
        connection.request(method, path, body=json.dumps(data or {}) if method == 'POST' else None, headers=outgoing)
        response = connection.getresponse()
        result = json.loads(response.read())
        cookies = SimpleCookie()
        for name, value in response.getheaders():
            if name.lower() == 'set-cookie':
                cookies.load(value)
        connection.close()
        return response.status, result, '; '.join('%s=%s' % (name, morsel.value) for name, morsel in cookies.items())

    def test_first_post_and_authenticated_stats_roundtrip_change_same_count(self):
        status, result, cookie = self.request('POST', '/api/campaign/vote', {'city': 'shenzhen'})
        self.assertEqual(status, 200)
        self.assertEqual(result, {'vote': {'city': 'shenzhen'}, 'canChange': True})
        self.store.create_admin('synthetic-admin', 'Synthetic-password-only-781')
        token, _ = self.store.new_session('synthetic-admin')
        admin_cookie = 'lean_admin=' + token
        status, stats, _ = self.request('GET', '/api/admin/stats', cookie=admin_cookie)
        self.assertEqual((status, stats['totalVotes']), (200, 1))
        self.assertEqual(stats['votes'][0]['count'], 1)
        self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': 'zhuhai'}, cookie=cookie)[0], 200)
        stats = self.request('GET', '/api/admin/stats', cookie=admin_cookie)[1]
        self.assertEqual(stats['totalVotes'], 1)
        self.assertEqual([row['count'] for row in stats['votes']], [0, 1, 0])

    def test_other_cookie_same_ip_get_and_conflict_return_authoritative_state(self):
        self.request('POST', '/api/campaign/vote', {'city': 'shenzhen'})
        status, state, other_cookie = self.request('GET', '/api/campaign')
        self.assertEqual(status, 200)
        self.assertEqual(state['vote'], {'city': 'shenzhen'})
        self.assertFalse(state['canChange'])
        status, same, _ = self.request('POST', '/api/campaign/vote', {'city': 'shenzhen'}, cookie=other_cookie)
        self.assertEqual((status, same['canChange']), (200, False))
        status, conflict, _ = self.request('POST', '/api/campaign/vote', {'city': 'zhuhai'}, cookie=other_cookie)
        self.assertEqual(status, 409)
        self.assertEqual(conflict['code'], 'IP_ALREADY_VOTED')
        self.assertEqual(conflict['vote'], {'city': 'shenzhen'})
        self.assertFalse(conflict['canChange'])
        self.assertEqual(self.store.stats()['totalVotes'], 1)

    def test_untrusted_client_ip_headers_cannot_bypass_one_ip_vote(self):
        status, _, _ = self.request('POST', '/api/campaign/vote', {'city': 'zhuhai'},
                                    headers={'X-Real-IP': '192.0.2.1', 'X-Forwarded-For': '192.0.2.1'})
        self.assertEqual(status, 200)
        status, result, _ = self.request('POST', '/api/campaign/vote', {'city': 'guangzhou'},
                                         headers={'X-Real-IP': '192.0.2.2', 'X-Forwarded-For': '192.0.2.2'})
        self.assertEqual(status, 409)
        self.assertEqual(result['code'], 'IP_ALREADY_VOTED')
        self.assertEqual(self.store.stats()['totalVotes'], 1)

    def test_only_explicit_trusted_proxy_supplies_distinct_client_addresses(self):
        with patch.object(self.server_module, 'TRUSTED_PROXIES', {'127.0.0.1'}):
            self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': 'shenzhen'},
                                          headers={'X-Real-IP': '192.0.2.1'})[0], 200)
            self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': 'zhuhai'},
                                          headers={'X-Real-IP': '192.0.2.2'})[0], 200)
            self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': 'guangzhou'},
                                          headers={'X-Real-IP': '::ffff:192.0.2.1'})[0], 409)
            self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': 'guangzhou'},
                                          headers={'X-Real-IP': 'not-an-ip'})[0], 200)
            self.assertEqual(self.request('POST', '/api/campaign/vote', {'city': 'shenzhen'},
                                          headers={'X-Real-IP': 'still-not-an-ip'})[0], 409)
        self.assertEqual(self.store.stats()['totalVotes'], 3)

    def test_parallel_cookie_less_http_submissions_are_atomic(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda _: self.request('POST', '/api/campaign/vote', {'city': 'zhuhai'}), range(12)))
        self.assertEqual([response[0] for response in responses], [200] * 12)
        self.assertEqual(self.store.stats()['totalVotes'], 1)
        self.assertEqual(sum(response[1]['canChange'] for response in responses), 1)
        self.assertEqual(len({response[2] for response in responses}), 12)


if __name__ == '__main__':
    unittest.main()
