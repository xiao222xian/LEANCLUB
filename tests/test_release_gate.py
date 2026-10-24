"""Release identity and credential isolation; synthetic inputs, no external actions."""
import copy
from contextlib import contextmanager, nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_gate', ROOT / 'deploy/ubuntu/release-gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
spec = importlib.util.spec_from_file_location('publish_client', ROOT / 'deploy/ci/publish-via-ssh.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
spec = importlib.util.spec_from_file_location('fetch_fixtures', ROOT / 'tests/test_fetch_github_artifact.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.config = {'repositoryId': 123, 'workflowId': 456}
        repo = {'id': 123, 'full_name': gate.REPO, 'private': True}
        self.run = {'id': 12, 'workflow_id': 456, 'path': gate.WORKFLOW,
                    'status': 'completed', 'conclusion': 'success', 'event': 'push',
                    'head_branch': 'main', 'head_sha': 'a' * 40, 'run_attempt': 1,
                    'repository': repo, 'head_repository': dict(repo)}
        self.request = {'runId': 12, 'mode': 'plan', 'token': 'synthetic-private-token-1234567890'}

    def test_request_is_explicit_and_bounded(self):
        self.assertEqual(gate.read_request(io.StringIO(json.dumps(self.request))), self.request)
        for change in ({'runId': True}, {'runId': '12'}, {'mode': 'shell'},
                       {'command': 'id'}, {'token': 'short'}, {'token': 'a' * 20000}):
            with self.subTest(change=list(change)), self.assertRaises(gate.GateError) as caught:
                gate.read_request(io.StringIO(json.dumps(self.request | change)))
            self.assertNotIn(self.request['token'], str(caught.exception))

    def test_approved_build_identity(self):
        self.assertEqual(gate.validate_run(self.run, self.config, 12), ('a' * 40, 1))

    def test_unapproved_build_sources_rejected(self):
        changes = [{'id': 13}, {'workflow_id': 999}, {'path': '.github/workflows/other.yml'},
                   {'status': 'in_progress'}, {'conclusion': 'failure'}, {'event': 'pull_request'},
                   {'head_branch': 'feature/unsafe'}, {'head_sha': 'main'}, {'run_attempt': 0},
                   {'repository': {'id': 124, 'full_name': gate.REPO, 'private': True}},
                   {'head_repository': {'id': 123, 'full_name': 'outsider/fork', 'private': True}},
                   {'repository': {'id': 123, 'full_name': gate.REPO, 'private': False}}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(gate.GateError):
                gate.validate_run(self.run | change, self.config, 12)

    def test_allowed_push_branches_and_main_only_dispatch(self):
        for branch in gate.BRANCHES:
            gate.validate_run(self.run | {'head_branch': branch, 'event': 'push'}, self.config, 12)
            if branch == 'main':
                gate.validate_run(self.run | {'head_branch': branch, 'event': 'workflow_dispatch'}, self.config, 12)
            else:
                with self.assertRaises(gate.GateError):
                    gate.validate_run(self.run | {'head_branch': branch, 'event': 'workflow_dispatch'}, self.config, 12)

    def test_request_deadline_is_cancelled_and_reported(self):
        handlers = []
        def stalled_read(_limit):
            handlers[0](14, None)
        def install(_signum, handler):
            handlers.append(handler)
            return 'previous-handler'
        with patch.object(gate.signal, 'SIGALRM', 14, create=True), \
                patch.object(gate.signal, 'signal', side_effect=install) as install_mock, \
                patch.object(gate.signal, 'alarm', create=True) as alarm:
            with self.assertRaises(gate.GateError) as caught:
                gate.bounded_request(SimpleNamespace(read=stalled_read))
            self.assertEqual(caught.exception.code, 'REQUEST_TIMEOUT')
            self.assertEqual(alarm.call_args_list[0].args, (20,))
            self.assertEqual(alarm.call_args_list[-1].args, (0,))
            self.assertEqual(install_mock.call_args_list[-1].args, (14, 'previous-handler'))

    def test_request_read_happens_before_publication_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'gate.json'
            config.write_text(json.dumps(self.config | {'format': 1, 'toolsRoot': directory}))
            with patch.dict('sys.modules', {'fcntl': SimpleNamespace(),
                    'resource': SimpleNamespace(RLIMIT_CORE=0, setrlimit=Mock())}), \
                    patch.object(gate, 'CONFIG', config), patch.object(gate, 'protected'), \
                    patch.object(gate.os, 'geteuid', return_value=0, create=True), \
                    patch.dict(gate.os.environ, {'SUDO_USER': 'leanschool-deploy'}), \
                    patch.object(gate, 'bounded_request', side_effect=gate.GateError('timeout', 'REQUEST_TIMEOUT')), \
                    patch.object(gate.os, 'open') as open_lock:
                with self.assertRaises(gate.GateError):
                    gate.main()
                open_lock.assert_not_called()

    def test_error_contract_does_not_include_exception_text(self):
        self.assertEqual(client.SAFE_ERRORS, gate.SAFE_ERRORS)
        for code in gate.SAFE_ERRORS:
            report = gate.error_report(code)
            self.assertEqual(client.safe_failure(json.dumps(report)), report)
        valid = gate.error_report('PREFLIGHT_FAILED')
        for value in (valid | {'error': self.request['token']}, valid | {'extra': self.request['token']},
                      valid | {'errorCode': self.request['token']}, valid | {'errorCode': []},
                      valid | {'phase': 'unexpected'}, {'ok': False}, [valid]):
            self.assertIsNone(client.safe_failure(json.dumps(value)))
        self.assertIsNone(client.safe_failure('ssh: ' + self.request['token']))

    def test_child_error_uses_only_code_and_no_killing_timeout_or_token(self):
        manifest = {'commits': {'website': 'a' * 40}}
        fetcher = SimpleNamespace(FetchError=RuntimeError)
        for code in gate.COORDINATOR_CODES:
            with self.subTest(code=code):
                request = dict(self.request)
                def child(argv, **kwargs):
                    self.assertNotIn('timeout', kwargs)
                    self.assertNotIn('token', request)
                    self.assertNotIn(self.request['token'], json.dumps(kwargs))
                    self.assertEqual(kwargs['env'], {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
                    return SimpleNamespace(returncode=1, stdout='', stderr=json.dumps({
                        'ok': False, 'databaseRestored': False, 'errorCode': code, 'error': self.request['token']}))
                with patch.object(gate, 'deadline', return_value=nullcontext()), \
                        patch.object(gate, 'fetch_release', return_value=(Path('/private/release'), manifest, 1, {})), \
                        patch.object(gate, 'protected', side_effect=lambda p, **_: p), \
                        patch.object(gate.subprocess, 'run', side_effect=child):
                    with self.assertRaises(gate.GateError) as caught:
                        gate.execute(request, self.config | {'toolsRoot': '/fixed/tools'}, fetcher)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn(self.request['token'], str(caught.exception))

    def test_download_gets_sixty_minutes_and_timer_ends_before_release(self):
        active = {'timer': False}
        events = []
        @contextmanager
        def download_deadline(seconds, code):
            self.assertEqual((seconds, code), (3600, 'BUILD_FETCH_TIMEOUT'))
            active['timer'] = True
            events.append('download-timer-started')
            try:
                yield
            finally:
                active['timer'] = False
                events.append('download-timer-cleared')
        def retrieve(*_args):
            self.assertTrue(active['timer'])
            return Path('/private/release'), {'commits': {'website': 'a' * 40}}, 1, {}
        def release(*_args, **kwargs):
            self.assertFalse(active['timer'])
            self.assertNotIn('timeout', kwargs)
            events.append('release-started')
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'ok': True, 'mode': 'plan', 'deployed': False, 'schemaCompatible': True}))
        with patch.object(gate, 'deadline', side_effect=download_deadline), \
                patch.object(gate, 'fetch_release', side_effect=retrieve), \
                patch.object(gate, 'protected', side_effect=lambda p, **_: p), \
                patch.object(gate, 'save_plan'), \
                patch.object(gate.subprocess, 'run', side_effect=release):
            result = gate.execute(dict(self.request), self.config | {'toolsRoot': '/fixed/tools'},
                                  SimpleNamespace(FetchError=RuntimeError))
        self.assertTrue(result['ok'])
        self.assertEqual(events, ['download-timer-started', 'download-timer-cleared', 'release-started'])

    def test_publish_cannot_download_uncached_artifact(self):
        fetcher = fixtures.fetcher
        run = self.run | {'id': fixtures.RUN, 'head_sha': fixtures.SHA, 'run_attempt': fixtures.ATTEMPT}
        artifact = fixtures.artifact_metadata() | {'workflow_run': {
            'repository_id': 123, 'head_repository_id': 123, 'head_branch': 'main'}}
        request = self.request | {'runId': fixtures.RUN, 'mode': 'publish'}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(fetcher, 'GitHub', return_value=SimpleNamespace(get=Mock(return_value=run))), \
                patch.object(fetcher, 'artifact_for_run', return_value=(artifact, fixtures.ATTEMPT)), \
                patch.object(fetcher, 'fetch') as download:
            with self.assertRaises(gate.GateError) as caught:
                gate.fetch_release(request, self.config, fetcher, Path(directory))
            self.assertEqual(caught.exception.code, 'PLAN_REQUIRED')
            download.assert_not_called()
            folder = Path(directory) / ('%d-%d-%s' % (fixtures.RUN, fixtures.ATTEMPT, fixtures.SHA[:12]))
            folder.mkdir()  # A leftover directory without a verified receipt is not a prepared release.
            with self.assertRaises(gate.GateError) as caught:
                gate.fetch_release(request, self.config, fetcher, Path(directory))
            self.assertEqual(caught.exception.code, 'PLAN_REQUIRED')
            download.assert_not_called()

    def test_only_successful_preflight_creates_plan_and_publish_checks_its_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / '12-1-aaaaaaaaaaaa'
            folder.mkdir()
            path = root / (folder.name + '.planned.json')
            config = self.config | {'toolsRoot': str(root / 'fixed-tools-v1')}
            identity = {'artifactId': 789, 'digest': 'sha256:' + 'b' * 64, 'workflowSha': 'a' * 40}
            expected = {'format': 1, 'runId': 12, 'attempt': 1, **identity, 'toolsRoot': config['toolsRoot']}
            manifest = {'commits': {'website': 'a' * 40}}
            fetcher = SimpleNamespace(FetchError=RuntimeError)
            fetched = (folder, manifest, 1, identity)
            failure = SimpleNamespace(returncode=1, stdout='', stderr=json.dumps({
                'ok': False, 'databaseRestored': False, 'errorCode': 'PREFLIGHT_FAILED'}))
            planned = SimpleNamespace(returncode=0, stdout=json.dumps({
                'ok': True, 'mode': 'plan', 'deployed': False, 'schemaCompatible': True}))
            published = SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'mode': 'publish'}))
            with patch.object(gate, 'deadline', return_value=nullcontext()), \
                    patch.object(gate, 'fetch_release', return_value=fetched), \
                    patch.object(gate, 'protected', side_effect=lambda p, **_: p), \
                    patch.object(gate.subprocess, 'run', return_value=failure) as child:
                with self.assertRaises(gate.GateError) as caught:
                    gate.execute(dict(self.request), config, fetcher, root)
                self.assertEqual(caught.exception.code, 'PREFLIGHT_FAILED')
                self.assertFalse(path.exists())
                child.reset_mock()
                with self.assertRaises(gate.GateError) as caught:
                    gate.execute(self.request | {'mode': 'publish'}, config, fetcher, root)
                self.assertEqual(caught.exception.code, 'PLAN_REQUIRED')
                child.assert_not_called()
                child.return_value = planned
                gate.execute(dict(self.request), config, fetcher, root)
                self.assertEqual(json.loads(path.read_text()), expected)
                self.assertNotIn(self.request['token'], path.read_text())
                if os.name == 'posix':
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                child.return_value = published
                result = gate.execute(self.request | {'mode': 'publish'}, config, fetcher, root)
                self.assertTrue(result['ok'])
                for change in ({'runId': 13}, {'attempt': 2}, {'artifactId': 999},
                               {'digest': 'sha256:' + 'c' * 64}, {'workflowSha': 'd' * 40},
                               {'toolsRoot': str(root / 'previous-tools')}, {'extra': True}):
                    with self.subTest(change=change):
                        path.write_text(json.dumps(expected | change))
                        child.reset_mock()
                        with self.assertRaises(gate.GateError) as caught:
                            gate.execute(self.request | {'mode': 'publish'}, config, fetcher, root)
                        self.assertEqual(caught.exception.code, 'PLAN_REQUIRED')
                        child.assert_not_called()
                path.write_text(json.dumps(expected))
                with self.assertRaises(gate.GateError) as caught:
                    gate.execute(self.request | {'mode': 'publish'}, config | {'toolsRoot': str(root / 'fixed-tools-v2')}, fetcher, root)
                self.assertEqual(caught.exception.code, 'PLAN_REQUIRED')

    def test_incomplete_success_receipt_does_not_authorize_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fetcher = SimpleNamespace(FetchError=RuntimeError)
            with patch.object(gate, 'deadline', return_value=nullcontext()), \
                    patch.object(gate, 'fetch_release', return_value=(root / 'release', {'commits': {}}, 1, {})), \
                    patch.object(gate, 'protected', side_effect=lambda p, **_: p), \
                    patch.object(gate, 'save_plan') as save, \
                    patch.object(gate.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=0, stdout=json.dumps({'ok': True, 'mode': 'plan'}))):
                with self.assertRaises(gate.GateError) as caught:
                    gate.execute(dict(self.request), self.config | {'toolsRoot': str(root)}, fetcher, root)
                self.assertEqual(caught.exception.code, 'COORDINATOR_FAILED')
                save.assert_not_called()

    def test_cached_bundle_is_rehashed_and_needs_live_matching_identity(self):
        fetcher = fixtures.fetcher
        run = self.run | {'id': fixtures.RUN, 'head_sha': fixtures.SHA, 'run_attempt': fixtures.ATTEMPT}
        artifact = fixtures.artifact_metadata() | {'workflow_run': {
            'repository_id': 123, 'head_repository_id': 123, 'head_branch': 'main'}}
        request = self.request | {'runId': fixtures.RUN}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / ('%d-%d-%s' % (fixtures.RUN, fixtures.ATTEMPT, fixtures.SHA[:12]))
            folder.mkdir()
            for name, contents in fixtures.bundle().items():
                (folder / name).write_bytes(contents)
            receipt = root / (folder.name + '.verified.json')
            identity = {'artifactId': artifact['id'], 'digest': artifact['digest'], 'workflowSha': fixtures.SHA}
            receipt.write_text(json.dumps(identity))
            api = SimpleNamespace(get=Mock(return_value=run))
            with patch.object(fetcher, 'GitHub', return_value=api), \
                    patch.object(fetcher, 'artifact_for_run', return_value=(artifact, fixtures.ATTEMPT)) as check_run, \
                    patch.object(fetcher, 'fetch') as download, \
                    patch.object(gate, 'protected', side_effect=lambda p, **_: p):
                self.assertEqual(gate.fetch_release(request, self.config, fetcher, root)[0], folder)
                check_run.assert_called_once()
                download.assert_not_called()
                (folder / 'yudao-server.jar').write_bytes(b'tampered')
                with self.assertRaises(fetcher.FetchError):
                    gate.fetch_release(request, self.config, fetcher, root)
                receipt.write_text(json.dumps(identity | {'artifactId': 999}))
                with self.assertRaises(gate.GateError):
                    gate.fetch_release(request, self.config, fetcher, root)

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: 1)() == 0,
                         'Root-owned POSIX filesystem permissions need Linux/root validation')
    def test_trusted_paths_reject_writable_ancestor_and_symlink(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            parent = Path(directory)
            item = parent / 'tool.py'
            item.write_text('# code')
            item.chmod(0o600)
            self.assertEqual(gate.protected(item, private=True), item)
            parent.chmod(0o777)
            with self.assertRaises(gate.GateError):
                gate.protected(item)
            parent.chmod(0o700)
            link = parent / 'link.py'
            link.symlink_to(item)
            with self.assertRaises(gate.GateError):
                gate.protected(link)

    def test_release_client_uses_stdin_and_pinned_host(self):
        environment = {'BUILD_RUN_ID': '12', 'RELEASE_MODE': 'plan', 'GH_TOKEN': self.request['token'],
                       'DEPLOY_SSH_KEY': 'synthetic private key', 'DEPLOY_KNOWN_HOSTS': 'synthetic pinned host'}
        def run(argv, **kwargs):
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertNotIn(self.request['token'], ' '.join(argv))
            self.assertNotIn('synthetic private key', ' '.join(argv))
            self.assertEqual(json.loads(kwargs['input']), self.request)
            self.assertEqual(kwargs['timeout'], 4800)
            self.assertNotIn('GH_TOKEN', client.os.environ)
            self.assertNotIn('DEPLOY_SSH_KEY', client.os.environ)
            return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'buildRunId': 12, 'mode': 'plan'}))
        with patch.dict(client.os.environ, environment, clear=True), patch.object(client.subprocess, 'run', side_effect=run), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(client.main(), 0)
            self.assertNotIn(self.request['token'], output.getvalue())

    def test_client_distinguishes_safe_failure_without_printing_raw_output(self):
        environment = {'BUILD_RUN_ID': '12', 'RELEASE_MODE': 'plan', 'GH_TOKEN': self.request['token'],
                       'DEPLOY_SSH_KEY': 'synthetic private key', 'DEPLOY_KNOWN_HOSTS': 'synthetic pinned host'}
        for stderr, expected in ((json.dumps(gate.error_report('PREFLIGHT_FAILED')), 'PREFLIGHT_FAILED'),
                                 ('ssh: failed ' + self.request['token'], 'SSH_CONNECTION_OR_PROTOCOL_FAILED')):
            with patch.dict(client.os.environ, environment, clear=True), \
                    patch.object(client.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr=stderr)), \
                    patch('sys.stderr', new_callable=io.StringIO) as output:
                self.assertEqual(client.main(), 1)
                self.assertIn(expected, output.getvalue())
                self.assertNotIn(self.request['token'], output.getvalue())


if __name__ == '__main__':
    unittest.main()
