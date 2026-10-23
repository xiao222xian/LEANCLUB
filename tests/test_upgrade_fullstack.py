"""Isolated upgrade/rollback tests: no real service, DB, network or secrets."""
from contextlib import ExitStack, closing
import io
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('upgrade_fullstack', ROOT / 'deploy/ubuntu/upgrade-fullstack-site.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
from campaign import CampaignStore
from member_orders_schema import member_schema_ready
from payment_schema import payment_schema_ready


def old_environment():
    return ('# Managed by bojizhuye HTTPS configuration.\n'
            'CLUB_DB=' + str(upgrade.STATE / 'clubs.sqlite3') + '\n'
            'CLUB_MEDIA_DIR=' + str(upgrade.STATE / 'media') + '\n'
            'CLUB_PORT=5381\nCLUB_ORIGIN=https://boji.tensorbot.xyz\n'
            'CLUB_TRUSTED_PROXY_IPS=127.0.0.1\nCLUB_ALLOW_LOCAL_SETUP=0\n'
            '# keep this comment and unrelated runtime setting\nOTHER_SETTING="keep exactly"\n')


def bridge_environment():
    return ('LS_PUBLIC_TENANT_ID=1\nLS_OPERATIONS_SECRET=' + 'a' * 64 + '\n'
            'LS_MEMBER_ORDERS_SECRET=' + 'b' * 64 + '\n'
            'LS_DB_PASSWORD=synthetic-not-to-copy\n')


def legacy_nginx():
    return ('''# Managed by bojizhuye HTTPS configuration. Do not reuse.
server {
    listen 443 ssl;
    server_name boji.tensorbot.xyz;
    ssl_certificate /etc/nginx/ssl/boji.tensorbot.xyz/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/boji.tensorbot.xyz/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    root /opt/bojizhuye/current/web/creator;
    location ^~ /api/ { proxy_pass http://127.0.0.1:5381; }
    location = /admin { try_files /admin/index.html =404; }
    location = /admin/ { try_files /admin/index.html =404; }
    location / { try_files $uri =404; }
}
server {
    listen 80;
    server_name boji.tensorbot.xyz;
    return 301 https://boji.tensorbot.xyz$request_uri;
}
''')


class EnvironmentTests(unittest.TestCase):
    def test_merge_preserves_old_values_and_only_maps_two_secrets(self):
        old = old_environment()
        result = upgrade.site_environment(old, bridge_environment())
        values = upgrade.environment(result)
        self.assertIn('# keep this comment', result)
        self.assertIn('OTHER_SETTING="keep exactly"', result)
        self.assertEqual(values['CLUB_ORIGIN'], upgrade.ORIGIN)
        self.assertEqual(values['CLUB_COMPAT_ORIGINS'], upgrade.COMPAT_ORIGIN)
        self.assertEqual(values['LEANCLUB_OPERATIONS_SECRET'], 'a' * 64)
        self.assertEqual(values['LEANCLUB_MEMBER_ORDERS_SECRET'], 'b' * 64)
        self.assertNotIn('synthetic-not-to-copy', result)
        self.assertEqual(upgrade.site_environment(result, bridge_environment()), result)

    def test_ambiguous_or_missing_credentials_and_unsafe_environment_fail(self):
        for bridge in (bridge_environment() + 'LS_OPERATIONS_SECRET=' + 'c' * 64,
                       bridge_environment().replace('b' * 64, 'a' * 64),
                       bridge_environment().replace('a' * 64, ''),
                       bridge_environment().replace('a' * 64, 'A' * 64),
                       bridge_environment().replace('LS_PUBLIC_TENANT_ID=1', 'LS_PUBLIC_TENANT_ID=2')):
            with self.assertRaises(ValueError) as error:
                upgrade.site_environment(old_environment(), bridge)
            self.assertNotIn('a' * 64, str(error.exception))
        for old in (old_environment() + 'CLUB_PORT=5381\n',
                    old_environment().replace('CLUB_ALLOW_LOCAL_SETUP=0', 'CLUB_ALLOW_LOCAL_SETUP=1'),
                    old_environment().replace('https://boji.tensorbot.xyz', 'http://boji.tensorbot.xyz')):
            with self.assertRaises(ValueError):
                upgrade.site_environment(old, bridge_environment())

    def test_environment_parser_never_executes_or_interpolates(self):
        value = upgrade.environment('A="$(test)"\nB=literal$dollar\n')
        self.assertEqual(value, {'A': '$(test)', 'B': 'literal$dollar'})
        for text in ('export SECRET=value', 'A=hello\\\n', 'A="unterminated', 'A="escaped\\x"'):
            with self.assertRaises(ValueError):
                upgrade.environment(text)


class NginxTests(unittest.TestCase):
    def test_only_old_tls_block_changes_preserving_certificates_admin_and_redirect(self):
        old = legacy_nginx()
        result = upgrade.patch_legacy_nginx(old)
        self.assertEqual(result.split('server {')[2], old.split('server {')[2])
        for line in old.splitlines():
            if 'ssl_' in line or 'location = /admin' in line:
                self.assertIn(line, result)
        self.assertIn('proxy_pass http://127.0.0.1:5382;', result)
        self.assertIn('proxy_pass http://127.0.0.1:48080;', result)
        self.assertIn('root /opt/leanschool/ui/current;', result)
        self.assertIn('try_files $uri $uri/ /console/index.html;', result)
        self.assertIn('location /console/assets/ { try_files $uri =404; }', result)
        self.assertIn('location ~ /\\. { return 404; }', result)
        self.assertIn('location = /check-in { return 302 /console/leanclub/registrations; }', result)
        self.assertIn('access_log off;', result)
        self.assertNotIn('proxy_set_header Origin', result)
        self.assertEqual(upgrade.patch_legacy_nginx(result), result)

    def test_conflicts_duplicate_hosts_and_non_tls_fail_closed(self):
        cases = (legacy_nginx().replace('listen 443 ssl;', 'listen 80;'),
                 legacy_nginx() + legacy_nginx(),
                 legacy_nginx().replace('    location /', '    location ^~ /app-api/ { return 403; }\n    location /'),
                 legacy_nginx().replace('    location /', '    location = /console { return 404; }\n    location /'),
                 legacy_nginx() + '{')
        for text in cases:
            with self.assertRaises(ValueError):
                upgrade.patch_legacy_nginx(text)

    def test_parser_ignores_braces_in_comments_and_quotes(self):
        old = legacy_nginx().replace('    root ', '    # fake server {\n    set $sample "}";\n    root ')
        self.assertIn('set $sample "}";', upgrade.patch_legacy_nginx(old))


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'console'
        (self.root / 'assets').mkdir(parents=True)
        (self.root / 'assets/app.js').write_text('console.log("synthetic");')
        (self.root / 'assets/app.css').write_text('body{}')

    def tearDown(self):
        self.temp.cleanup()

    def index(self, script='/console/assets/app.js'):
        (self.root / 'index.html').write_text('<!doctype html><html><head><script type="module" src="' + script + '"></script><link rel="stylesheet" href="/console/assets/app.css"></head><body>fixture</body></html>')

    def test_real_console_base_and_assets_are_required(self):
        self.index()
        upgrade.console_ready(self.root)
        (self.root / 'assets/app.css').unlink()
        with self.assertRaises(ValueError):
            upgrade.console_ready(self.root)

    def test_wrong_base_external_encoded_and_traversal_assets_rejected(self):
        for script in ('/assets/app.js', 'https://example.test/app.js', '/console/../outside.js', '/console/%61ssets/app.js'):
            self.index(script)
            with self.assertRaises(ValueError):
                upgrade.console_ready(self.root)


class ReadinessTests(unittest.TestCase):
    def test_only_the_systemd_main_process_may_own_the_python_listener(self):
        with patch.object(upgrade, 'active', return_value=True):
            for listener in ('LISTEN 127.0.0.1:5381 users:((python,pid=42,fd=3))',
                             'LISTEN 127.0.0.1:5381 users:((python,pid=99,fd=3))',
                             'LISTEN 0.0.0.0:5381 users:((python,pid=42,fd=3))'):
                with patch.object(upgrade, 'command', side_effect=[SimpleNamespace(stdout='42\n'), SimpleNamespace(stdout=listener)]):
                    if '127.0.0.1' in listener and 'pid=42' in listener:
                        upgrade.assert_single_service()
                    else:
                        with self.assertRaises(ValueError):
                            upgrade.assert_single_service()

    def test_stopped_gate_rejects_live_service_and_other_sqlite_file_holders(self):
        with patch.object(upgrade, 'active', return_value=True), patch.object(upgrade.socket, 'socket') as socket:
            with self.assertRaises(ValueError):
                upgrade.assert_stopped(Path('/synthetic/clubs.sqlite3'))
            socket.assert_not_called()
        with patch.object(upgrade, 'active', return_value=False), patch.object(upgrade.socket, 'socket'), \
                patch.object(Path, 'glob', side_effect=[[Path('/proc/42')], [Path('/proc/42/fd/3')]]), \
                patch.object(upgrade.os, 'readlink', return_value=str(Path('/synthetic/clubs.sqlite3')) + '-wal'):
            with self.assertRaises(ValueError):
                upgrade.assert_stopped(Path('/synthetic/clubs.sqlite3'))

    def test_java_dependency_must_have_real_school_rows(self):
        valid = {'code': 0, 'data': {'list': [{'id': 1, 'name': '合成大学'}], 'total': 1}}
        with patch.object(upgrade, 'json_get', side_effect=[{'code': 0, 'data': None}, valid]) as get:
            upgrade.java_ready()
            self.assertTrue(all(call.args[2] is True for call in get.call_args_list))
        for data in ([], {'code': 500}, {'code': 0, 'data': {'list': [], 'total': 0}},
                     {'code': 0, 'data': {'list': [{'id': 1}], 'total': 1}}):
            with patch.object(upgrade, 'json_get', side_effect=[{'code': 0}, data]):
                with self.assertRaises(ValueError):
                    upgrade.java_ready()

    def test_health_uses_both_exact_hosts_and_checks_admin_setup(self):
        def response(url, host=None, tenant=False):
            if url.endswith('/api/admin/session'):
                return {'setupAvailable': False, 'setupRequired': False}
            return {'status': 'ok'}
        with patch.object(upgrade, 'json_get', side_effect=response) as get, patch.object(upgrade, 'java_ready') as java:
            upgrade.service_health()
            self.assertEqual({call.args[1] for call in get.call_args_list}, set(upgrade.HOSTS))
            self.assertEqual({call.args[1] for call in java.call_args_list}, set(upgrade.HOSTS))
        with patch.object(upgrade, 'json_get', return_value={'status': 'ok', 'setupAvailable': True}), patch.object(upgrade, 'java_ready'):
            with self.assertRaises(ValueError):
                upgrade.service_health()


class ArchiveTests(unittest.TestCase):
    def test_shared_archive_validation_and_extraction_pin_commit_digest_and_file_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'source.tar.gz'
            revision = 'a' * 40
            with tarfile.open(archive, 'w:gz', format=tarfile.PAX_FORMAT, pax_headers={'comment': revision}) as tar:
                for name in sorted(upgrade.install.REQUIRED):
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = 7, 0o777
                    tar.addfile(member, io.BytesIO(b'fixture'))
            digest = upgrade.install.archive_digest(archive)
            upgrade.install.validate_archive(archive, revision)
            with self.assertRaises(ValueError):
                upgrade.install.validate_archive(archive, 'b' * 40)
            with patch.object(upgrade.install, 'BASE', root / 'app'), patch.object(upgrade.install, 'run') as run:
                release = upgrade.install.prepare_release(archive, revision, digest)
                self.assertEqual((release / 'backend/server.py').read_bytes(), b'fixture')
                self.assertEqual((release / '.deployment-sha256').read_text().strip(), digest)
                self.assertEqual(upgrade.install.prepare_release(archive, revision, digest), release)
                self.assertEqual(run.call_count, 2)
                with self.assertRaises(ValueError):
                    upgrade.install.prepare_release(archive, revision, '0' * 64)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / 'clubs.sqlite3'
        self.backup = self.root / 'backup'
        self.backup.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def subprocess(self, *args, **kwargs):
        # Use the current test interpreter instead of requiring an Ubuntu venv.
        return self.real_command(sys.executable, *args[1:], **kwargs)

    def migrate(self):
        self.real_command = upgrade.command
        with patch.object(upgrade, 'STATE', self.root), patch.object(upgrade, 'command', side_effect=self.subprocess), \
                patch.object(upgrade.os, 'chown', create=True):
            upgrade.migrate_database(ROOT, self.database, self.backup, 123, 123)

    def test_baseline_without_events_gets_explicit_current_schemas_and_keeps_old_rows(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript('''CREATE TABLE campaign_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE campaign_admins(username TEXT PRIMARY KEY COLLATE NOCASE,password_hash TEXT NOT NULL,created INTEGER NOT NULL);
                INSERT INTO campaign_admins VALUES('synthetic-admin','synthetic-hash',123);
                INSERT INTO campaign_settings VALUES('vote_ip_secret','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
                INSERT INTO campaign_settings VALUES('synthetic-existing-setting','retain');''')
            db.executescript('''CREATE TABLE campaign_visitors(token_hash TEXT PRIMARY KEY,created INTEGER NOT NULL,expires INTEGER NOT NULL);
                CREATE TABLE campaign_votes(visitor TEXT PRIMARY KEY REFERENCES campaign_visitors(token_hash),city TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,ip_hash TEXT);
                CREATE TABLE campaign_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL REFERENCES campaign_admins(username),csrf TEXT NOT NULL,expires INTEGER NOT NULL);
                INSERT INTO campaign_visitors VALUES('synthetic-visitor',1,4102444800);
                INSERT INTO campaign_votes VALUES('synthetic-visitor','shenzhen',1,2,'synthetic-old-ip-hash');
                INSERT INTO campaign_sessions VALUES('synthetic-session','synthetic-admin','synthetic-csrf',4102444800);''')
            db.commit()
        upgrade.backup_database(self.database, self.backup / 'before.sqlite3')
        self.migrate()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertTrue(member_schema_ready(db))
            self.assertTrue(payment_schema_ready(db))
            self.assertEqual(db.execute('SELECT * FROM campaign_admins').fetchall(), [('synthetic-admin', 'synthetic-hash', 123)])
            self.assertEqual(db.execute('SELECT * FROM campaign_votes').fetchall(), [('synthetic-visitor', 'shenzhen', 1, 2, 'synthetic-old-ip-hash')])
            self.assertEqual(db.execute('SELECT * FROM campaign_sessions').fetchall(), [('synthetic-session', 'synthetic-admin', 'synthetic-csrf', 4102444800)])
            self.assertEqual(db.execute("SELECT value FROM campaign_settings WHERE key='vote_ip_secret'").fetchone()[0], 'a' * 64)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM campaign_comments').fetchone()[0], 0)
        with closing(sqlite3.connect(self.backup / 'before.sqlite3')) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='campaign_registrations'").fetchone())

    def test_existing_legacy_orders_migrate_explicitly_and_keep_all_fields(self):
        # Existing migration fixture has real legacy NOT NULL ownership semantics.
        from test_member_orders import legacy_fixture
        from test_campaign_events import event_body, registration_body, synthetic_paid
        from payment_fake import FakePaymentAdapter
        from member_orders_schema import LEGACY_COLUMNS
        store = CampaignStore(self.database, self.root / 'media')
        store.events.payment_adapter = FakePaymentAdapter(store.clock)
        event = store.events.save(event_body(int(store.clock())))['event']
        visitor = store.visitor(None)[0]
        receipt = store.events.register(visitor, event['id'], registration_body(), upgrade.COMPAT_ORIGIN)
        synthetic_paid(store, receipt['order']['id'])
        legacy_fixture(store)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertFalse(member_schema_ready(db))
            before = db.execute('SELECT ' + ','.join(LEGACY_COLUMNS) + ' FROM campaign_registrations').fetchall()
        self.migrate()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertTrue(member_schema_ready(db))
            self.assertTrue(payment_schema_ready(db))
            self.assertEqual(db.execute('SELECT ' + ','.join(LEGACY_COLUMNS) + ' FROM campaign_registrations').fetchall(), before)
        self.assertEqual(len(list(self.backup.glob('before-member-orders-*.sqlite3'))), 1)
        self.assertEqual(len(list(self.backup.glob('before-payments-*.sqlite3'))), 1)

    def test_backup_includes_committed_wal_without_overwriting_source_or_existing_target(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE test(value TEXT)')
            db.execute("INSERT INTO test VALUES('synthetic committed WAL')")
            db.commit()
            destination = self.backup / 'before.sqlite3'
            upgrade.backup_database(self.database, destination)
            with closing(sqlite3.connect(destination)) as copied:
                self.assertEqual(copied.execute('SELECT value FROM test').fetchone()[0], 'synthetic committed WAL')
            with self.assertRaises(ValueError):
                upgrade.backup_database(self.database, destination)

    def test_failed_initializer_still_repairs_database_ownership_for_old_service(self):
        self.database.write_bytes(b'synthetic ownership fixture')
        with patch.object(upgrade, 'command', side_effect=RuntimeError('synthetic failure')), \
                patch.object(upgrade.os, 'chown', create=True) as chown:
            with self.assertRaises(RuntimeError):
                upgrade.migrate_database(ROOT, self.database, self.backup, 123, 456)
            chown.assert_any_call(self.database, 123, 456)


class UpgradeFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.paths = {}
        for name in ('ENV', 'UNIT', 'CONF', 'GATEWAY_ENV', 'GATEWAY_UNIT'):
            path = self.root / name
            if name in ('ENV', 'UNIT', 'CONF'):
                path.write_text('old ' + name)
            self.paths[name] = path
            self.stack.enter_context(patch.object(upgrade, name, path))
        self.stack.enter_context(patch.object(upgrade, 'BASE', self.root / 'app'))
        self.stack.enter_context(patch.object(upgrade, 'STATE', self.root))
        self.release = self.root / 'release'
        templates = self.release / 'deploy/ubuntu'
        templates.mkdir(parents=True)
        for name in ('bojizhuye-public-gateway.env.in', 'bojizhuye-public-gateway.service.in'):
            (templates / name).write_text('synthetic ' + name)
        self.backup = self.root / 'private-backup'
        self.backup.mkdir()
        self.database = self.root / 'clubs.sqlite3'
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE preservation(value TEXT)')
            db.execute("INSERT INTO preservation VALUES('before')")
            db.commit()
        self.events = []
        self.stack.enter_context(patch.object(upgrade.os, 'readlink', return_value='old-release'))
        self.stack.enter_context(patch.object(upgrade, 'active', return_value=False))
        self.stack.enter_context(patch.object(upgrade, 'enabled', return_value=False))
        self.stack.enter_context(patch.object(upgrade, 'command', side_effect=self.command))
        self.stack.enter_context(patch.object(upgrade.install, 'set_current', side_effect=lambda target: self.events.append(('current', str(target)))))
        for name in ('java_ready', 'console_ready', 'assert_single_service', 'assert_stopped', 'legacy_https_health'):
            self.stack.enter_context(patch.object(upgrade, name, side_effect=lambda *a, n=name: self.events.append((n,))))
        self.stack.enter_context(patch.object(upgrade, 'migrate_database', side_effect=self.migrate))
        self.stack.enter_context(patch.object(upgrade.time, 'sleep'))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def command(self, *args, **kwargs):
        self.events.append(tuple(str(arg) for arg in args))
        return SimpleNamespace(returncode=0, stdout='')

    def migrate(self, *args):
        self.events.append(('migration',))
        with closing(sqlite3.connect(self.backup / 'before.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT value FROM preservation').fetchall(), [('before',)])
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("INSERT INTO preservation VALUES('after migration - must survive rollback')")
            db.commit()

    def apply(self):
        upgrade.apply_upgrade(self.release, 'new env', 'new nginx', self.backup, SimpleNamespace(pw_uid=1, pw_gid=1))

    def test_dependency_failure_has_zero_service_stops_or_configuration_changes(self):
        with patch.object(upgrade, 'java_ready', side_effect=ValueError('school directory not ready')):
            with self.assertRaises(ValueError):
                self.apply()
        self.assertEqual(self.events, [])
        self.assertEqual(self.paths['ENV'].read_text(), 'old ENV')
        self.assertFalse((self.backup / 'before.sqlite3').exists())

    def test_success_order_stops_before_backup_migrates_before_start_and_keeps_old_unit(self):
        with patch.object(upgrade, 'service_health', side_effect=lambda: self.events.append(('health',))):
            self.apply()
        self.assertLess(self.events.index(('java_ready',)), self.events.index(('systemctl', 'stop', upgrade.SERVICE)))
        self.assertLess(self.events.index(('console_ready',)), self.events.index(('systemctl', 'stop', upgrade.SERVICE)))
        self.assertLess(self.events.index(('systemctl', 'stop', upgrade.SERVICE)), self.events.index(('migration',)))
        self.assertLess(self.events.index(('migration',)), self.events.index(('systemctl', 'start', upgrade.SERVICE)))
        self.assertLess(self.events.index(('health',)), self.events.index(('systemctl', 'reload', 'nginx')))
        self.assertEqual(self.paths['UNIT'].read_text(), 'old UNIT')
        self.assertEqual(self.paths['ENV'].read_text(), 'new env')

    def test_failed_new_health_restores_code_env_unit_but_never_business_database(self):
        with patch.object(upgrade, 'service_health', side_effect=ValueError('synthetic health failure')), patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(ValueError):
                self.apply()
        for name in ('ENV', 'UNIT', 'CONF'):
            self.assertEqual(self.paths[name].read_text(), 'old ' + name)
        self.assertFalse(self.paths['GATEWAY_ENV'].exists())
        self.assertFalse(self.paths['GATEWAY_UNIT'].exists())
        self.assertIn(('current', 'old-release'), self.events)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM preservation').fetchone()[0], 2)

    def test_incomplete_control_rollback_does_not_restart_with_mixed_configurations(self):
        snapshots = {path: (b'old', 0o600) for path in self.paths.values()}
        with patch.object(upgrade.install, 'set_current', side_effect=OSError('synthetic symlink failure')):
            errors = upgrade.restore_configuration(snapshots, 'old-release', False, False)
        self.assertIn('restore current symlink', errors)
        self.assertFalse(any(event[:2] == ('systemctl', 'restart') for event in self.events))
        self.assertNotIn(('systemctl', 'reload', 'nginx'), self.events)


if __name__ == '__main__':
    unittest.main()
