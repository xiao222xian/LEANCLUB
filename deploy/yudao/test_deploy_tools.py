"""Isolated safety/format checks; no network and no production/local business DB."""
import importlib.util
import json
import io
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import preflight
import product_seed

HERE = Path(__file__).resolve().parent
def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded
schema = module('prepare-schema')
bootstrap = module('prepare-bootstrap')
installer = module('platform-install')

class DeploymentToolsTest(unittest.TestCase):
    def test_sql_quoted_comments_and_semicolons(self):
        text = "-- ignore;\nCREATE TABLE x (v varchar(99)); INSERT INTO x VALUES ('a; -- b', 'x''y'); /* drop; */"
        self.assertEqual(len(schema.statements(text)), 2)
        self.assertIn("'a; -- b'", schema.statements(text)[1])

    def test_no_account_channel_or_business_rows(self):
        text = """DROP TABLE system_menu;
CREATE TABLE system_menu (id int, name text);
CREATE TABLE system_users (id int, password text);
INSERT INTO system_menu VALUES (1, 'menu');
INSERT INTO system_users VALUES (1, 'SECRET_CANARY');
INSERT INTO pay_channel VALUES (1, 'SECRET_CHANNEL');
DELETE FROM system_menu;"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.sql'; path.write_text(text, encoding='utf-8')
            tables, seeds, _, skipped = schema.collect([path])
        self.assertEqual(set(tables), {'system_menu', 'system_users'})
        self.assertEqual(len(seeds), 1)
        self.assertNotIn('SECRET', ''.join(seeds))
        self.assertEqual(skipped['DROP'], 1)
        self.assertEqual(skipped['DELETE'], 1)

    def test_conflicting_definitions_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.sql'
            path.write_text('CREATE TABLE x (id int); CREATE TABLE x (id text);', encoding='utf-8')
            with self.assertRaises(ValueError): schema.collect([path])

    def test_unclosed_sql_fails(self):
        with self.assertRaises(ValueError): schema.statements("INSERT INTO x VALUES ('truncated")

    def test_foreign_key_parent_is_created_before_child(self):
        tables = {'child': 'CREATE TABLE child (id int, FOREIGN KEY(id) REFERENCES parent(id))',
                  'parent': 'CREATE TABLE parent (id int PRIMARY KEY)'}
        self.assertEqual(schema.ordered_tables(tables), [tables['parent'], tables['child']])

    def test_foreign_key_missing_parent_and_cycle_fail(self):
        with self.assertRaisesRegex(ValueError, 'Missing referenced'):
            schema.ordered_tables({'a': 'CREATE TABLE a (id int REFERENCES absent(id))'})
        with self.assertRaisesRegex(ValueError, 'Circular'):
            schema.ordered_tables({'a': 'CREATE TABLE a (id int REFERENCES b(id))',
                                   'b': 'CREATE TABLE b (id int REFERENCES a(id))'})

    def valid_env(self):
        return {'LS_DB_NAME': 'leanschool', 'LS_DB_USER': 'leanschool_app',
                'LS_DB_PASSWORD': 'A7xC9vB2nM4qW6eR8tY1uI3oP5sD',
                'LS_REDIS_PASSWORD': 'G6hJ9kL2zX5cV8bN1mQ4wE7rT3yU',
                'LS_FIELD_ENCRYPTION_KEY': 'qW8eR4tY9uI3oP5aS7dF1gH6jK2lZ0xC',
                'LS_PUBLIC_ORIGIN': 'https://www.leanschool.net',
                'LS_ADMIN_URL': 'https://www.leanschool.net/console/'}

    def test_valid_environment_and_independent_secrets(self):
        env = self.valid_env(); preflight.validate_environment(env)
        env['LS_REDIS_PASSWORD'] = env['LS_DB_PASSWORD']
        with self.assertRaisesRegex(ValueError, 'must differ'): preflight.validate_environment(env)

    def test_dangerous_profile_override_rejected(self):
        env = self.valid_env(); env['SPRING_PROFILES_ACTIVE'] = 'local'
        with self.assertRaisesRegex(ValueError, 'override'): preflight.validate_environment(env)

    def test_bridge_keys_cannot_share_domain(self):
        env = self.valid_env()
        for prefix in ['LS_OPERATIONS', 'LS_MEMBER_ORDERS']:
            env[prefix + '_ENABLED'] = 'true'; env[prefix + '_SECRET'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'separate'): preflight.validate_environment(env)

    def test_brand_seed_is_draft_and_old_filing_not_reused(self):
        source = {'schemaVersion': 1, 'footer': {'brand': 'LEAN SCHOOL', 'icpText': 'OLD'},
                  'voices': [{'quote': 'Keep the existing homepage marquee'}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.json'; path.write_text(json.dumps(source), encoding='utf-8')
            sql, content, report = product_seed.brand_draft(path)
        self.assertEqual(source['footer']['icpText'], 'OLD')
        self.assertEqual(content['footer']['icpText'], '')
        self.assertEqual(content['voices'], source['voices'])
        self.assertIn('NULL,1', sql)
        self.assertIn('unpublished', report['mode'])

    def test_empty_sql_strings_have_valid_hex_syntax(self):
        self.assertEqual(product_seed.literal(''), "CONVERT(X'' USING utf8mb4)")
        self.assertEqual(bootstrap.sql_string(''), "CONVERT(X'' USING utf8mb4)")

    def test_bootstrap_uses_guard_and_hash_only(self):
        sql = bootstrap.render('opsadmin', '$2b$12$synthetic_hash', '2030-01-01', 'synthetic_oauth')
        self.assertIn('CHECK (ok=1)', sql)
        self.assertIn('START TRANSACTION', sql)
        self.assertNotIn('UPDATE system_users', sql)
        self.assertNotIn('ON DUPLICATE KEY', sql)
        self.assertNotIn('$2b$12$synthetic_hash', sql) # Hex encoding is SQL-safe, not encryption.

    def test_bootstrap_websites_match_upstream_comma_storage(self):
        sql = bootstrap.render('opsadmin', '$2b$12$synthetic_hash', '2030-01-01', 'synthetic_oauth')
        tenant = next(statement for statement in schema.statements(sql)
                      if statement.startswith('INSERT INTO system_tenant '))
        strings = [bytes.fromhex(value).decode('utf-8') for value in
                   re.findall(r"CONVERT\(X'([0-9a-f]*)' USING utf8mb4\)", tenant)]
        self.assertEqual(strings[0], '薄肌学院')
        # StringListTypeHandler splits commas and TenantMapper uses FIND_IN_SET.
        # JSON array brackets/quotes would stop either host from matching.
        self.assertEqual(strings[2].split(','), ['www.leanschool.net', 'boji.tensorbot.xyz'])
        self.assertNotIn('[', strings[2])
        self.assertNotIn('"', strings[2])

    def test_bootstrap_accepts_16_character_password(self):
        with tempfile.TemporaryDirectory() as temp:
            password = Path(temp) / 'password.private'
            output = Path(temp) / 'bootstrap.private.sql'
            password.write_text('QaOnly7x9B2m4N6p', encoding='utf-8')
            args = ['prepare-bootstrap.py', '--username', 'opsadmin',
                    '--tenant-expiry', '2099-01-01', '--password-file', str(password),
                    '--output', str(output)]
            with patch.object(sys, 'argv', args), patch('builtins.print'):
                bootstrap.main()
            self.assertTrue(output.is_file())
            self.assertNotIn(password.read_text(), output.read_text(encoding='utf-8'))

    def test_bootstrap_rejects_non_16_character_password_before_sql(self):
        for length in (15, 17, 32):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as temp:
                password = Path(temp) / 'password.private'
                output = Path(temp) / 'bootstrap.private.sql'
                password.write_text(('QaOnly7x9B2m4N6p' * 2)[:length], encoding='utf-8')
                args = ['prepare-bootstrap.py', '--username', 'opsadmin',
                        '--tenant-expiry', '2099-01-01', '--password-file', str(password),
                        '--output', str(output)]
                with patch.object(sys, 'argv', args):
                    with self.assertRaisesRegex(SystemExit, 'exactly 16'):
                        bootstrap.main()
                self.assertFalse(output.exists())

    def make_ui(self, directory, extra=None, html='<script src="/console/assets/main.js"></script>'):
        path = Path(directory) / 'ui.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, content in [('console/index.html', html), ('console/assets/main.js', 'export {};')]:
                data = content.encode(); info = tarfile.TarInfo(name); info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            if extra: archive.addfile(extra)
        return path

    def test_ui_console_prefix_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            archive, entries, size = installer.archive_entries(self.make_ui(temp), 'console')
            try:
                self.assertEqual([name for name, _, _ in entries], ['index.html', 'assets/main.js'])
                self.assertGreater(size, 0)
            finally: archive.close()

    def test_ui_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_ui(temp, tarfile.TarInfo('console/../../escape'))
            with self.assertRaisesRegex(ValueError, 'Unsafe'): installer.archive_entries(path, 'console')

    def test_ui_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            item = tarfile.TarInfo('console/secret'); item.type = tarfile.SYMTYPE; item.linkname = '/etc/passwd'
            path = self.make_ui(temp, item)
            with self.assertRaisesRegex(ValueError, 'symlinks'): installer.archive_entries(path, 'console')

    def test_ui_development_build_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_ui(temp, html='<script src="/console/@vite/client"></script>')
            with self.assertRaisesRegex(ValueError, 'production'): installer.archive_entries(path, 'console')

    def test_subprocess_failures_do_not_echo_secrets(self):
        fake = subprocess.CompletedProcess(['mysql'], 1, 'SECRET_SQL', 'SECRET_PASSWORD')
        with patch.object(installer.subprocess, 'run', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'output withheld') as error:
                installer.run(['/usr/bin/mysql'], 'SECRET_INPUT')
        self.assertNotIn('SECRET', str(error.exception))

if __name__ == '__main__': unittest.main()
