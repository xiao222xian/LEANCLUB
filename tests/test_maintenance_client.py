import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('maintenance_client', ROOT / 'deploy/ci/install-release-tools-via-ssh.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class MaintenanceClientTests(unittest.TestCase):
    def test_archive_mismatch_stops_before_any_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            def fake_execute(args, *unused, **kwargs):
                Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--output='))).write_bytes(b'wrong archive')
                return SimpleNamespace(returncode=0)
            with patch.object(client, 'execute', side_effect=fake_execute) as execute:
                with self.assertRaisesRegex(client.ClientError, '^ARCHIVE_MISMATCH$'):
                    client.archive(ROOT, Path(temporary) / 'tools.tar.gz')
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(execute.call_args.args[0][0], 'git')

    def test_password_is_child_environment_only_with_strict_host_checking(self):
        secret = 'synthetic-password-for-test-only'
        success = {'ok': True, 'toolsRoot': client.TOOLS_ROOT, 'tests': 88,
                   'nginx': 'valid', 'services': 'active', 'applicationPublished': False,
                   'productionUnchanged': True}
        captured = []
        def fake_execute(args, *unused, **kwargs):
            self.assertNotIn(secret, repr(args))
            self.assertNotIn(secret, Path(kwargs['env']['SSH_ASKPASS']).read_text())
            self.assertEqual(kwargs['env']['LS_ONE_SHOT_SSH_PASSWORD'], secret)
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('NumberOfPasswordPrompts=1', args)
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            captured.append(args[0])
            return SimpleNamespace(returncode=0, stdout=json.dumps(success).encode())
        with patch.object(sys, 'argv', ['maintenance']), patch.object(sys, 'platform', 'linux'), \
                patch.dict(client.os.environ, {'LEANSCHOOL_MAINTENANCE_SSH_PASSWORD': secret,
                    'LEANSCHOOL_DEPLOY_KNOWN_HOSTS': '152.136.108.87 ssh-ed25519 synthetic'}), \
                patch.object(client, 'archive'), patch.object(client, 'execute', side_effect=fake_execute):
            self.assertEqual(client.main(), success)
        self.assertEqual(captured, ['/usr/bin/scp', '/usr/bin/ssh'])

    def test_child_output_is_never_forwarded(self):
        result = SimpleNamespace(returncode=1, stdout=json.dumps({'ok': False,
            'errorCode': 'REMOTE_TESTS_FAILED', 'error': 'synthetic private content'}).encode())
        with self.assertRaisesRegex(client.ClientError, '^REMOTE_TESTS_FAILED$'):
            client.safe_response(result)
        result.stdout = b'synthetic private contents, not JSON'
        with self.assertRaisesRegex(client.ClientError, '^SSH_FAILED$'):
            client.safe_response(result)
        result.stdout = b'[]'
        with self.assertRaisesRegex(client.ClientError, '^SSH_FAILED$'):
            client.safe_response(result)

    def test_remote_archive_refuses_links_and_traversal(self):
        # Compile the fixed program without invoking main; stub Linux-only import on Windows.
        namespace = {'__name__': 'maintenance_test'}
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}):
            exec(compile(client.REMOTE_PROGRAM, '<maintenance>', 'exec'), namespace)
        for name, kind in (('../outside', tarfile.REGTYPE), ('deploy/link', tarfile.SYMTYPE),
                           ('deploy/hard', tarfile.LNKTYPE), ('/absolute', tarfile.REGTYPE)):
            with self.subTest(name=name):
                output = io.BytesIO()
                with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.PAX_FORMAT,
                                  pax_headers={'comment': client.COMMIT}) as package:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = '/etc/shadow' if kind != tarfile.REGTYPE else ''
                    package.addfile(member)
                data = output.getvalue()
                namespace['ARCHIVE_SHA256'] = hashlib.sha256(data).hexdigest()
                with self.assertRaisesRegex(namespace['MaintenanceError'], '^REMOTE_ARCHIVE_INVALID$'):
                    namespace['inspect_archive'](data)

    def test_changed_production_snapshot_prevents_success_receipt(self):
        namespace = {'__name__': 'maintenance_test'}
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}):
            exec(compile(client.REMOTE_PROGRAM, '<maintenance>', 'exec'), namespace)
        counts = iter((30, 33, 3, 22))
        def command(args, *unused, **kwargs):
            if 'unittest' in args:
                return SimpleNamespace(stderr='Ran %s tests in 0.1s\n\nOK\n' % next(counts))
            return SimpleNamespace(stdout=json.dumps({'ok': True, 'toolsRoot': str(namespace['DESTINATION']),
                'passwordLocked': True, 'productionServicesRestarted': False}))
        namespace.update({'preflight': Mock(), 'service_check': Mock(), 'install_tree': Mock(),
            'verify_tree': Mock(), 'read_archive': Mock(return_value=({}, set())),
            'production_snapshot': Mock(side_effect=[('old',), ('changed',)]), 'command': command})
        with self.assertRaisesRegex(namespace['MaintenanceError'], '^POSTCHECK_FAILED$'):
            namespace['maintain']()


if __name__ == '__main__':
    unittest.main()
