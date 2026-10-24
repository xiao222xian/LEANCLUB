"""Isolated certificate, rollback and routing checks; no production credentials or network writes."""
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import warnings
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / 'deploy' / 'ubuntu'
spec = importlib.util.spec_from_file_location('leanschool_https', SOURCE / 'enable-leanschool-https.py')
https = importlib.util.module_from_spec(spec)
spec.loader.exec_module(https)

CERT = b'-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----\n'
CHAIN = b'-----BEGIN CERTIFICATE-----\nCHAIN\n-----END CERTIFICATE-----\n'
KEY = b'-----BEGIN PRIVATE KEY-----\nSYNTHETIC-PRIVATE-KEY\n-----END PRIVATE KEY-----\n'


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def bundle(self, entries=None):
        target = self.folder / 'bundle.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(target, 'w') as archive:
                for name, data in entries or [('fullchain.pem', CERT + CHAIN), ('privkey.key', KEY)]:
                    if isinstance(name, str) and '\\' in name:
                        entry = zipfile.ZipInfo('placeholder')
                        entry.filename = entry.orig_filename = name
                        name = entry
                    archive.writestr(name, data)
        return target

    def test_normal_bundle_is_normalized_without_extracting_paths(self):
        path = self.bundle([('folder/fullchain.pem', CERT + CHAIN), ('folder/key.key', b'comment\n' + KEY)])
        certs, key = https.read_bundle(path)
        self.assertEqual(len(certs), 2)
        self.assertEqual(key, KEY)
        self.assertEqual(list(self.folder.iterdir()), [path])

    def test_unsafe_paths_are_rejected(self):
        for name in ('../key', '/key', 'C:/key', 'a\\key', './key', 'a//key', 'a/../key', 'a\x01key'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                https.read_bundle(self.bundle([(name, KEY), ('fullchain.pem', CERT + CHAIN)]))

    def test_symlink_and_duplicate_names_are_rejected(self):
        linked = zipfile.ZipInfo('key')
        linked.create_system = 3
        linked.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            https.read_bundle(self.bundle([(linked, KEY), ('fullchain.pem', CERT + CHAIN)]))
        with self.assertRaises(ValueError):
            https.read_bundle(self.bundle([('key', KEY), ('key', b'other'), ('fullchain.pem', CERT + CHAIN)]))

    def test_duplicate_keys_and_ambiguous_chains_are_rejected(self):
        for entries in ([('fullchain.pem', CERT + CHAIN), ('key', KEY + KEY)],
                        [('a.pem', CERT + CHAIN), ('b.pem', CHAIN + CERT), ('key', KEY)],
                        [('a.pem', CERT + CERT), ('key', KEY)],
                        [('leaf.pem', CERT), ('key', KEY)]):
            with self.subTest(entries=len(entries)), self.assertRaises(ValueError):
                https.read_bundle(self.bundle(entries))

    def test_encrypted_keys_and_archive_limits_are_rejected(self):
        encrypted = KEY.replace(b'PRIVATE KEY', b'ENCRYPTED PRIVATE KEY')
        for entries in ([('fullchain.pem', CERT + CHAIN), ('key', encrypted)],
                        [('padding', b'x' * (1024 * 1024 + 1))],
                        [(str(index), b'x') for index in range(21)]):
            with self.subTest(entries=len(entries)), self.assertRaises(ValueError):
                https.read_bundle(self.bundle(entries))

    def test_validation_uses_host_trust_purpose_and_key_match(self):
        calls = []
        def command(*args, data=None):
            calls.append(args)
            if args[1] == 'pkey':
                return b'public-key'
            if '-pubkey' in args:
                return b'public-key-pem'
            return b'public certificate metadata'
        output = io.StringIO()
        with patch.object(https, 'run', side_effect=command), redirect_stdout(output):
            with https.validated_bundle(self.bundle()) as (cert, key):
                self.assertEqual(cert, CERT + CHAIN)
                self.assertEqual(key, KEY)
        verify = next(call for call in calls if call[1] == 'verify')
        self.assertIn('-purpose', verify)
        self.assertIn('sslserver', verify)
        self.assertIn('-verify_hostname', verify)
        self.assertIn(https.DOMAIN, verify)
        self.assertNotIn('SYNTHETIC-PRIVATE-KEY', output.getvalue())

    def test_key_mismatch_is_rejected_before_yield(self):
        def command(*args, data=None):
            if args[1] == 'pkey':
                return b'certificate-public-key' if '-pubin' in args else b'other-key'
            return b'certificate-public-key'
        with patch.object(https, 'run', side_effect=command), self.assertRaisesRegex(ValueError, 'does not match'):
            with https.validated_bundle(self.bundle()):
                self.fail('A mismatched key must not be usable')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, relative in {'CONF': 'available/leanschool.conf', 'ENABLED': 'enabled/leanschool.conf',
                               'SNIPPET': 'snippets/proxy.conf', 'TLS': 'ssl/www.leanschool.net',
                               'BACKUP_ROOT': 'backups', 'UI_INDEX': 'ui/console/index.html'}.items():
            self.stack.enter_context(patch.object(https, name, self.root / relative))
        https.ENABLED.parent.mkdir(parents=True)
        https.UI_INDEX.parent.mkdir(parents=True)
        https.UI_INDEX.write_text('console', encoding='utf-8')
        self.old = self.root / 'available/bojizhuye.conf'
        self.old.parent.mkdir(parents=True)
        self.old.write_bytes(b'old-vhost-and-certificate-paths-must-stay-identical')
        self.old_bytes = self.old.read_bytes()
        # Windows tests model the one managed symlink; file writes/backups/rollback remain real.
        self.real_is_symlink = Path.is_symlink
        self.stack.enter_context(patch.object(Path, 'is_symlink', lambda p:
            p.exists() if p == https.ENABLED else self.real_is_symlink(p)))
        self.stack.enter_context(patch.object(Path, 'symlink_to', lambda p, target:
            p.write_text(str(target), encoding='utf-8')))
        self.calls = []

    def command(self, *args, data=None, timeout=60):
        self.calls.append(args)
        if args[0] == 'curl':
            self.assertIn('--noproxy', args)
            self.assertNotIn('-k', args)
            self.assertNotIn('--insecure', args)
            self.assertIn('--resolve', args)
            url = urlsplit(args[-1])
            if args[args.index('-w') + 1] == '%{http_code}\n%{redirect_url}':
                target = '/console/login?redirect=/leanclub/content' if url.path == '/admin' else '/console/leanclub/registrations'
                return ('302\nhttps://' + https.DOMAIN + target).encode()
            denied = ('__leanschool_missing__' in url.path or url.path.startswith('/internal/')
                      or url.path in {'/app-api/system/auth/login', '/doc.html', '/v3/api-docs',
                                      '/swagger-ui/index.html', '/actuator/env', '/druid/'})
            return b'404' if denied else b'200'
        return b''

    def seed_previous(self):
        previous = {https.CONF: https.MARKER + b'\nold config', https.SNIPPET: https.MARKER + b'\nold snippet',
                    https.TLS / 'fullchain.pem': b'old certificate', https.TLS / 'privkey.pem': b'old key'}
        for path, data in previous.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        https.ENABLED.symlink_to(https.CONF)
        return previous

    def apply(self, command=None):
        with patch.object(https, 'preflight'), patch.object(https, 'run', side_effect=command or self.command), redirect_stdout(io.StringIO()):
            https.apply_certificate(CERT + CHAIN, KEY)

    def readiness_clock(self):
        elapsed = [0.0]
        self.stack.enter_context(patch.object(https.time, 'monotonic', side_effect=lambda: elapsed[0]))
        def sleep(seconds):
            self.assertGreater(seconds, 0)
            elapsed[0] += seconds
        self.stack.enter_context(patch.object(https.time, 'sleep', side_effect=sleep))
        return elapsed

    def test_first_old_worker_certificate_failure_retries_then_verifies_all_routes(self):
        elapsed = self.readiness_clock()
        attempts = 0
        def command(*args, data=None, timeout=60):
            nonlocal attempts
            result = self.command(*args, data=data, timeout=timeout)
            if args[0] == 'curl' and args[-1] == 'https://' + https.DOMAIN + '/':
                attempts += 1
                if attempts == 1:
                    self.assertEqual(timeout, 3.0)
                    self.assertEqual(args[args.index('--max-time') + 1], '3.0')
                    raise https.CommandError('curl', 60)
            return result
        self.apply(command)
        self.assertEqual(elapsed[0], 0.5)
        self.assertEqual(attempts, 3)  # Two readiness probes and the normal route verification.
        self.assertEqual(self.calls.count(('systemctl', 'reload', 'nginx')), 1)
        self.assertEqual(self.old.read_bytes(), self.old_bytes)
        self.assertTrue(https.ENABLED.exists())
        self.assertTrue(any(call[-1].endswith('/doc.html') for call in self.calls if call[0] == 'curl'))
        self.assertTrue(any(call[-1] == 'https://' + https.OLD_DOMAIN + '/admin'
                            for call in self.calls if call[0] == 'curl'))

    def test_persistent_certificate_failure_reaches_deadline_and_restores_previous_site(self):
        previous = self.seed_previous()
        elapsed = self.readiness_clock()
        attempts = []
        def command(*args, data=None, timeout=60):
            result = self.command(*args, data=data, timeout=timeout)
            if args[0] == 'curl':
                self.assertEqual(args[-1], 'https://' + https.DOMAIN + '/')
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, min(3.0, 20.0 - elapsed[0]))
                attempts.append(timeout)
                raise https.CommandError('curl', 60)
            return result
        with self.assertRaisesRegex(RuntimeError, 'timed out after 20 seconds; last curl exit 60'):
            self.apply(command)
        self.assertEqual(elapsed[0], 20.0)
        self.assertGreater(len(attempts), 1)
        self.assertEqual(self.calls.count(('systemctl', 'reload', 'nginx')), 2)
        for path, data in previous.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertTrue(https.ENABLED.exists())
        self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_hanging_requests_cannot_extend_readiness_deadline(self):
        elapsed = self.readiness_clock()
        def command(*args, data=None, timeout=60):
            result = self.command(*args, data=data, timeout=timeout)
            if args[0] == 'curl':
                self.assertEqual(float(args[args.index('--max-time') + 1]), timeout)
                self.assertLessEqual(timeout, min(3.0, 20.0 - elapsed[0]))
                elapsed[0] += timeout
                raise subprocess.TimeoutExpired('curl', timeout)
            return result
        with self.assertRaisesRegex(RuntimeError, 'timed out after 20 seconds; last request timeout'):
            self.apply(command)
        self.assertEqual(elapsed[0], 20.0)
        self.assertFalse(https.CONF.exists())
        self.assertFalse(https.ENABLED.exists())
        self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_success_preserves_old_vhost_and_keeps_backup(self):
        previous = self.seed_previous()
        self.apply()
        self.assertEqual(self.old.read_bytes(), self.old_bytes)
        self.assertEqual((https.TLS / 'privkey.pem').read_bytes(), KEY)
        self.assertTrue(https.ENABLED.exists())
        backup = next(https.BACKUP_ROOT.iterdir())
        manifest = json.loads((backup / 'manifest.json').read_text())
        self.assertEqual(manifest['existed'], [True] * 4)
        self.assertEqual((backup / '0').read_bytes(), previous[https.CONF])
        self.assertTrue(any(call[-1].endswith('/console/login') for call in self.calls if call[0] == 'curl'))

    def test_nginx_validation_failure_removes_new_site(self):
        first = True
        def command(*args, data=None, timeout=60):
            nonlocal first
            if args == ('nginx', '-t') and first:
                first = False
                raise RuntimeError('synthetic invalid configuration')
            return self.command(*args, data=data)
        with self.assertRaisesRegex(RuntimeError, 'synthetic invalid'):
            self.apply(command)
        self.assertFalse(https.CONF.exists())
        self.assertFalse(https.SNIPPET.exists())
        self.assertFalse(https.TLS.exists())
        self.assertFalse(https.ENABLED.exists())
        self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_reload_or_https_failure_restores_previous_site(self):
        previous = self.seed_previous()
        first = True
        def command(*args, data=None, timeout=60):
            nonlocal first
            if args == ('systemctl', 'reload', 'nginx') and first:
                first = False
                raise RuntimeError('synthetic reload failure')
            return self.command(*args, data=data)
        with self.assertRaisesRegex(RuntimeError, 'synthetic reload'):
            self.apply(command)
        for path, data in previous.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertTrue(https.ENABLED.exists())
        self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_missing_static_returning_html_triggers_rollback(self):
        def command(*args, data=None, timeout=60):
            if args[0] == 'curl' and '__leanschool_missing__' in args[-1]:
                return b'200'
            return self.command(*args, data=data)
        with self.assertRaisesRegex(ValueError, 'isolation'):
            self.apply(command)
        self.assertFalse(https.CONF.exists())
        self.assertFalse(https.ENABLED.exists())
        self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_rollback_failure_is_reported_and_other_files_restored(self):
        previous = self.seed_previous()
        original_write = https.atomic_write
        def write(path, data, mode):
            if path == https.CONF and data == previous[https.CONF]:
                raise OSError('synthetic rollback write failure')
            return original_write(path, data, mode)
        def command(*args, data=None, timeout=60):
            if args == ('nginx', '-t'):
                raise RuntimeError('synthetic invalid configuration')
            return self.command(*args, data=data)
        with patch.object(https, 'atomic_write', side_effect=write), self.assertRaisesRegex(RuntimeError, 'ROLLBACK_FAILED'):
            self.apply(command)
        for path in (https.SNIPPET, https.TLS / 'fullchain.pem', https.TLS / 'privkey.pem'):
            self.assertEqual(path.read_bytes(), previous[path])
        self.assertEqual(self.old.read_bytes(), self.old_bytes)

    def test_preflight_rejects_unowned_config_and_duplicate_hostname(self):
        healthy = lambda port, path, host: {'status': 'ok'} if port == 5381 else {'code': 0}
        https.CONF.write_bytes(b'unrelated configuration')
        with patch.object(https, 'local_json', side_effect=healthy), self.assertRaisesRegex(ValueError, 'not owned'):
            https.preflight()
        https.CONF.unlink()
        config = b'# configuration file /etc/nginx/sites-enabled/unrelated.conf:\nserver { server_name www.leanschool.net; }\n'
        with patch.object(https, 'local_json', side_effect=healthy), patch.object(https, 'run', return_value=config), self.assertRaisesRegex(ValueError, 'already owns'):
            https.preflight()


class RoutingTests(unittest.TestCase):
    def test_console_static_fallback_and_gateway_boundaries(self):
        text = (SOURCE / 'leanschool.nginx.conf.in').read_text()
        snippet = (SOURCE / 'leanschool-proxy.conf.in').read_text()
        self.assertIn('location /console/assets/ { try_files $uri =404; }', text)
        self.assertIn('location /console/brand/ { try_files $uri =404; }', text)
        self.assertIn('try_files $uri $uri/ /console/index.html;', text)
        self.assertIn('return 302 /console/login?redirect=/leanclub/content;', text)
        self.assertIn('location ^~ /internal/ { return 404; }', text)
        self.assertIn('proxy_pass http://127.0.0.1:5382;', text)
        self.assertNotIn('proxy_set_header Origin', snippet)
        self.assertNotIn('proxy_read_timeout', snippet)  # Locations choose 25s or 120s; no duplicate directives.
        log = next(line for line in text.splitlines() if line.startswith('log_format'))
        self.assertNotIn('$request_uri', log)
        self.assertNotIn('$args', log)
        self.assertNotIn('$request ', log)
        self.assertNotIn(https.OLD_DOMAIN, text)


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL is required for real certificate verification')
class RealOpenSSLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.config = cls.folder / 'openssl.cnf'
        cls.config.write_text('[req]\ndistinguished_name = dn\n[dn]\n')
        def run(*args):
            subprocess.run(('openssl', *args), capture_output=True, check=True, timeout=30)
        cls.root_key, cls.ca = cls.folder / 'ca.key', cls.folder / 'ca.pem'
        run('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-keyout', str(cls.root_key),
            '-out', str(cls.ca), '-subj', '/CN=LeanSchool isolated test CA', '-config', str(cls.config),
            '-addext', 'basicConstraints=critical,CA:TRUE')
        for label, host in (('valid', https.DOMAIN), ('wrong-host', 'example.invalid')):
            key, csr, cert, ext = [cls.folder / (label + suffix) for suffix in ('.key', '.csr', '.pem', '.ext')]
            ext.write_text('basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:' + host + '\n')
            run('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(csr),
                '-subj', '/CN=' + host, '-config', str(cls.config))
            run('x509', '-req', '-in', str(csr), '-CA', str(cls.ca), '-CAkey', str(cls.root_key),
                '-CAcreateserial', '-days', '2', '-out', str(cert), '-extfile', str(ext))
            with zipfile.ZipFile(cls.folder / (label + '.zip'), 'w') as archive:
                archive.writestr('fullchain.pem', cert.read_bytes() + cls.ca.read_bytes())
                archive.writestr('private.key', key.read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_trusted_host_and_matching_key(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.ca)}), redirect_stdout(io.StringIO()):
            with https.validated_bundle(self.folder / 'valid.zip') as (cert, key):
                self.assertIn(b'BEGIN CERTIFICATE', cert)
                self.assertIn(b'PRIVATE KEY', key)

    def test_real_wrong_hostname_is_rejected(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.ca)}), self.assertRaises(RuntimeError):
            with https.validated_bundle(self.folder / 'wrong-host.zip'):
                self.fail('Wrong host must not validate')

    def test_real_untrusted_chain_is_rejected(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.folder / 'missing-ca.pem')}), self.assertRaises(RuntimeError):
            with https.validated_bundle(self.folder / 'valid.zip'):
                self.fail('Untrusted chain must not validate')


if __name__ == '__main__':
    unittest.main()
