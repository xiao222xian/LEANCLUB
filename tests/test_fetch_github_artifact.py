"""Synthetic artifacts and mocked HTTP only: no network, credentials or deployments."""
from argparse import Namespace
import contextlib
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('artifact_fetch', ROOT / 'deploy/ubuntu/fetch-github-artifact.py')
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)
RUN, ATTEMPT, ARTIFACT = 123, 2, 456
SHA = 'c' * 40
NAME = 'leanschool-release-123-2'
TOKEN = 'synthetic-test-token-do-not-print'
SIGNED = 'https://download.example.invalid/archive?signature=do-not-print'
COMMITS = {'website': 'a' * 40, 'backend': 'b' * 40, 'adminUi': 'd' * 40}


def metadata(data):
    return json.dumps(data).encode()


def hashes(files):
    return {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for name, data in files.items()}


def tar_bytes(entries, comment=None):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz', format=tarfile.PAX_FORMAT,
                      pax_headers={'comment': comment} if comment else {}) as archive:
        for name, data, kind in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.mode = 0o644
            item.size = len(data) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                item.linkname = '/outside'
            archive.addfile(item, io.BytesIO(data) if item.size else None)
    return result.getvalue()


def checksum_file(files):
    files['SHA256SUMS'] = ''.join('%s  %s\n' % (hashlib.sha256(data).hexdigest(), name)
                                  for name, data in sorted(files.items()) if name != 'SHA256SUMS').encode()


def bundle():
    files = {
        'yudao-server.jar': b'synthetic-code-binary',
        'console.tar.gz': tar_bytes([('console/index.html', b'<script src="/console/assets/a.js"></script>', tarfile.REGTYPE),
                                     ('console/assets/a.js', b'public code', tarfile.REGTYPE)]),
        'website.tar.gz': tar_bytes([('backend', b'', tarfile.DIRTYPE),
                                     ('backend/server.py', b'# code', tarfile.REGTYPE),
                                     ('web/creator/index.html', b'<html></html>', tarfile.REGTYPE),
                                     ('requirements.txt', b'# no dependencies', tarfile.REGTYPE)], COMMITS['website']),
    }
    files['java-release.json'] = metadata({'sourceCommit': COMMITS['backend'],
                                          'artifact': hashes(files)['yudao-server.jar']})
    manifest = {'format': 1, 'repository': 'https://github.com/' + fetcher.REPOSITORY,
                'toolsCommit': SHA, 'runId': str(RUN), 'runAttempt': str(ATTEMPT),
                'commits': COMMITS, 'websiteCommit': COMMITS['website'], 'privateInitIncluded': False,
                'state': 'cloud-built-not-deployed', 'artifacts': hashes(files)}
    files['release-manifest.json'] = metadata(manifest)
    checksum_file(files)
    return files


def daily_bundle(**changes):
    """A platform/backend push builds sources with separate controlled-main tools."""
    files = bundle()
    files['console.tar.gz'] = tar_bytes([
        ('console/index.html', b'<script src="/console/assets/a.js"></script>', tarfile.REGTYPE),
        ('console/assets/a.js', b'public code', tarfile.REGTYPE)], COMMITS['adminUi'])
    manifest = json.loads(files['release-manifest.json'])
    manifest.update({'workflowSha': COMMITS['backend'], 'workflowEvent': 'push',
                     'workflowRef': 'refs/heads/platform/backend',
                     'sourceRefs': {'website': 'main', 'backend': COMMITS['backend'], 'adminUi': 'platform/admin-ui'},
                     'branchHeads': dict(COMMITS), 'artifacts': hashes({name: files[name] for name in fetcher.CODE})})
    manifest.update(changes)
    files['release-manifest.json'] = metadata(manifest)
    checksum_file(files)
    return files


def zip_bytes(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return result.getvalue()


def run_metadata():
    return {'id': RUN, 'head_sha': SHA, 'status': 'completed', 'conclusion': 'success',
            'run_attempt': ATTEMPT, 'repository': {'full_name': fetcher.REPOSITORY},
            'head_repository': {'full_name': fetcher.REPOSITORY}}


def artifact_metadata(payload=b'zip'):
    return {'id': ARTIFACT, 'name': NAME, 'expired': False, 'size_in_bytes': len(payload),
            'digest': 'sha256:' + hashlib.sha256(payload).hexdigest(),
            'workflow_run': {'id': RUN, 'head_sha': SHA}}


class Response(io.BytesIO):
    def __init__(self, code=200, body=b'', headers=()):
        super().__init__(body)
        self.code = code
        self.headers = Message()
        for name, value in headers:
            self.headers.add_header(name, value)


class QueueClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 45
        assert self.responses, 'Unexpected extra network request'
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAPI:
    def __init__(self, run=None, pages=None, item=None):
        self.run = run if run is not None else run_metadata()
        self.item = item if item is not None else artifact_metadata()
        self.pages = pages if pages is not None else [{'total_count': 1, 'artifacts': [self.item]}]
        self.calls = []

    def get(self, suffix):
        self.calls.append(suffix)
        if suffix == '/actions/runs/123':
            return self.run
        if suffix.startswith('/actions/runs/123/artifacts?'):
            return self.pages[int(suffix.rsplit('=', 1)[1]) - 1]
        if suffix == '/actions/artifacts/456':
            return self.item
        raise AssertionError('Unexpected metadata path')


class RangeClient:
    """Thread-safe synthetic object storage, including retryable and invalid responses."""
    def __init__(self, payload, fault=None):
        self.payload, self.fault = payload, fault
        self.requests, self.attempts = [], {}
        self.lock = threading.Lock()

    def open(self, request, timeout):
        interval = request.get_header('Range')
        start, end = map(int, interval.removeprefix('bytes=').split('-'))
        with self.lock:
            self.requests.append(request)
            self.attempts[interval] = self.attempts.get(interval, 0) + 1
            attempt = self.attempts[interval]
        if self.fault:
            result = self.fault(request, start, end, attempt)
            if result is not None:
                return result
        return self.result(start, end)

    def result(self, start, end, **changes):
        headers = {'Content-Range': 'bytes %d-%d/%d' % (start, end, len(self.payload)),
                   'Content-Length': str(end - start + 1), 'ETag': '"stable-object"'} | changes
        return Response(206, self.payload[start:end + 1], list(headers.items()))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = Namespace(run_id=RUN, artifact_name=NAME, expected_workflow_sha=SHA,
                              output=self.root / 'release')
        # Any missing mock fails closed, instead of touching the network.
        self.network = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Real HTTP is forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def clients(self, payload, digest=None):
        item = artifact_metadata(payload)
        if digest:
            item['digest'] = digest
        api = QueueClient([Response(body=metadata(run_metadata())),
                           Response(body=metadata({'total_count': 1, 'artifacts': [item]})),
                           Response(body=metadata(item)), Response(302, headers=[('Location', SIGNED)])])
        download = QueueClient([Response(302, headers=[('Location', SIGNED + '-second')]), Response(body=payload)])
        return api, download

    def test_end_to_end_verifies_release_and_never_forwards_credentials(self):
        files = bundle()
        payload = zip_bytes(files)
        api, download = self.clients(payload)
        with patch.object(fetcher, 'opener', side_effect=[api, download]):
            report = fetcher.fetch(self.args, TOKEN)
        self.assertEqual({p.name for p in self.args.output.iterdir()}, fetcher.FILES)
        self.assertEqual(report['files'], hashes(files))
        self.assertEqual(report['commits'], COMMITS)
        self.assertEqual(report['zipSha256'], hashlib.sha256(payload).hexdigest())
        self.assertFalse(report['deployed'])
        self.assertEqual(list(self.root.iterdir()), [self.args.output])
        for request in api.requests:
            self.assertTrue(request.full_url.startswith(fetcher.API + '/actions/'))
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        for request in download.requests:
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(set(k.lower() for k, _ in request.header_items()), {'accept', 'user-agent'})
        self.assertNotIn(TOKEN, json.dumps(report))
        self.assertNotIn('signature=', json.dumps(report))
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.args.output.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in self.args.output.iterdir()))

    def test_existing_output_is_untouched_and_does_not_request_metadata(self):
        self.args.output.mkdir()
        (self.args.output / 'keep').write_text('keep')
        with patch.object(fetcher, 'GitHub') as api, self.assertRaisesRegex(fetcher.FetchError, 'must not exist'):
            fetcher.fetch(self.args, TOKEN)
        api.assert_not_called()
        self.assertEqual((self.args.output / 'keep').read_text(), 'keep')

    def test_run_must_be_completed_successful_exact_sha_and_same_repository(self):
        cases = [{'status': 'in_progress'}, {'conclusion': 'failure'}, {'head_sha': 'd' * 40},
                 {'id': 999}, {'repository': {'full_name': 'other/repo'}},
                 {'head_repository': {'full_name': 'fork/repo'}}, {'run_attempt': 1}, {'run_attempt': True}]
        for changes in cases:
            with self.subTest(changes=changes):
                api = FakeAPI(run=run_metadata() | changes)
                with self.assertRaises(fetcher.FetchError):
                    fetcher.artifact_for_run(api, RUN, NAME, SHA)
                self.assertEqual(len(api.calls), 1)

    def test_artifact_identity_expiry_digest_and_size_are_required(self):
        cases = [{'id': 789}, {'name': 'backend'}, {'expired': True}, {'digest': None},
                 {'digest': ''}, {'digest': 'sha256:not-a-digest'}, {'size_in_bytes': 0},
                 {'size_in_bytes': fetcher.MAX_DOWNLOAD + 1}, {'workflow_run': {'id': 124, 'head_sha': SHA}},
                 {'workflow_run': {'id': RUN, 'head_sha': 'b' * 40}}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(fetcher.FetchError):
                fetcher.artifact_for_run(FakeAPI(item=artifact_metadata() | changes,
                    pages=[{'total_count': 1, 'artifacts': [artifact_metadata()]}]), RUN, NAME, SHA)

    def test_pagination_checks_uniqueness_and_completeness(self):
        selected = artifact_metadata()
        other = {'id': 789, 'name': 'intermediate-console'}
        pages = [{'total_count': 2, 'artifacts': [other]}, {'total_count': 2, 'artifacts': [selected]}]
        self.assertEqual(fetcher.artifact_for_run(FakeAPI(pages=pages), RUN, NAME, SHA)[0], selected)
        cases = [
            [{'total_count': 2, 'artifacts': [selected]}, {'total_count': 2, 'artifacts': [selected | {'id': 789}]}],
            [{'total_count': 2, 'artifacts': [selected]}, {'total_count': 2, 'artifacts': [selected]}],
            [{'total_count': 2, 'artifacts': [selected]}, {'total_count': 2, 'artifacts': []}],
            [{'total_count': 0, 'artifacts': [selected]}],
            [{'total_count': 2, 'artifacts': [selected]}, {'total_count': 1, 'artifacts': [other]}],
            [{'total_count': 0, 'artifacts': []}],
            [{'total_count': 1000, 'artifacts': [selected if i == 0 else {'id': 700 + i, 'name': 'other'}]} for i in range(10)],
        ]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(fetcher.FetchError):
                fetcher.artifact_for_run(FakeAPI(pages=pages), RUN, NAME, SHA)

    def test_bad_digest_leaves_no_output_or_download(self):
        api, download = self.clients(zip_bytes(bundle()), digest='sha256:' + '0' * 64)
        with patch.object(fetcher, 'opener', side_effect=[api, download]), self.assertRaisesRegex(fetcher.FetchError, 'ZIP SHA-256'):
            fetcher.fetch(self.args, TOKEN)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_verified_digest_cannot_publish_an_unapproved_file(self):
        api, download = self.clients(zip_bytes(bundle() | {'private-init.tar.gz': b'synthetic forbidden content'}))
        with patch.object(fetcher, 'opener', side_effect=[api, download]), self.assertRaisesRegex(fetcher.FetchError, 'six approved files'):
            fetcher.fetch(self.args, TOKEN)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_download_is_bounded_and_all_redirects_are_https(self):
        invalid = ['http://download.example.invalid/file', '//download.example.invalid/file',
                   'https://user:pass@example.invalid/file', 'https://127.0.0.1/file', 'https://[::1]/file',
                   'https://example.invalid:444/file', 'https://example.invalid/file#fragment',
                   'https://example.invalid/\nheader', 'https://example.invalid\\evil/file']
        for url in invalid:
            client = QueueClient([Response(302, headers=[('Location', url)])])
            with self.subTest(url=url), patch.object(fetcher, 'opener', return_value=client), self.assertRaises(fetcher.FetchError):
                fetcher.download(SIGNED, self.root / 'zip', '0' * 64)
            self.assertEqual(len(client.requests), 1)
        client = QueueClient([Response(302, headers=[('Location', SIGNED)]) for _ in range(4)])
        with patch.object(fetcher, 'opener', return_value=client), self.assertRaisesRegex(fetcher.FetchError, 'redirect limit'):
            fetcher.download(SIGNED, self.root / 'zip', '0' * 64)
        client = QueueClient([Response(body=b'0123456789')])
        with patch.object(fetcher, 'MAX_DOWNLOAD', 3), patch.object(fetcher, 'opener', return_value=client), self.assertRaisesRegex(fetcher.FetchError, 'byte limit'):
            fetcher.download(SIGNED, self.root / 'zip', '0' * 64)

    def test_api_rejects_redirect_and_cannot_use_arbitrary_paths(self):
        client = QueueClient([Response(302, headers=[('Location', SIGNED)])])
        with patch.object(fetcher, 'opener', return_value=client):
            api = fetcher.GitHub(TOKEN)
            with self.assertRaisesRegex(fetcher.FetchError, 'HTTP 302'):
                api.get('/actions/runs/123')
            for path in ['/contents/file', '/actions/runs/123/cancel', '//evil.invalid', '/actions/artifacts/1/zip?extra=x']:
                with self.assertRaisesRegex(fetcher.FetchError, 'Unapproved'):
                    api.get(path)
        self.assertEqual(len(client.requests), 1)

    def test_duplicate_location_and_oversized_api_metadata_are_rejected(self):
        client = QueueClient([Response(302, headers=[('Location', SIGNED), ('Location', SIGNED)]),
                              Response(body=b'x' * 17)])
        with patch.object(fetcher, 'opener', return_value=client), patch.object(fetcher, 'MAX_JSON', 16):
            api = fetcher.GitHub(TOKEN)
            with self.assertRaisesRegex(fetcher.FetchError, 'exactly one'):
                api.get('/actions/artifacts/456/zip', redirect=True)
            with self.assertRaisesRegex(fetcher.FetchError, 'exceeds'):
                api.get('/actions/runs/123')

    def test_zip_rejects_extra_missing_traversal_duplicate_link_and_size(self):
        valid = bundle()
        cases = [valid | {name: b'forbidden'} for name in ('private-init.tar.gz', '.env', 'data.sql', '../escape')]
        cases.append({name: value for name, value in valid.items() if name != 'website.tar.gz'})
        for i, files in enumerate(cases):
            path = self.root / ('case%d.zip' % i)
            path.write_bytes(zip_bytes(files))
            with self.assertRaises(fetcher.FetchError):
                fetcher.extract_code_zip(path, self.root / 'output')
        for kind in ('duplicate', 'link', 'directory', 'size'):
            path = self.root / (kind + '.zip')
            with zipfile.ZipFile(path, 'w') as archive:
                for name, data in valid.items():
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16 if kind == 'link' and name == 'website.tar.gz' else 0
                    if kind == 'directory' and name == 'website.tar.gz':
                        info.external_attr |= 0x10
                    archive.writestr(info, data)
                if kind == 'duplicate':
                    with contextlib.redirect_stderr(io.StringIO()):
                        archive.writestr('website.tar.gz', b'duplicate')
            with self.subTest(kind=kind), patch.dict(fetcher.LIMITS, {'website.tar.gz': 1} if kind == 'size' else {}), self.assertRaises(fetcher.FetchError):
                fetcher.extract_code_zip(path, self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def test_nested_archives_reject_private_files_paths_links_and_wrong_git_stamp(self):
        invalid = ['console/data.sql', 'console/.ENV.local', 'console/private-init.tar.gz', 'console/secrets.env',
                   'console/a.pem', 'console/a.js.map', 'console/../escape', '/console/a', 'console//a', 'other/a']
        for name in invalid:
            path = self.root / 'console.tar.gz'
            path.write_bytes(tar_bytes([('console/index.html', b'html', tarfile.REGTYPE), (name, b'x', tarfile.REGTYPE)]))
            with self.subTest(name=name), self.assertRaises(fetcher.FetchError):
                fetcher.check_tar(path)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE):
            path.write_bytes(tar_bytes([('console/index.html', b'html', tarfile.REGTYPE), ('console/a', b'', kind)]))
            with self.assertRaises(fetcher.FetchError):
                fetcher.check_tar(path)
        path.write_bytes(bundle()['website.tar.gz'])
        with self.assertRaisesRegex(fetcher.FetchError, 'Git provenance'):
            fetcher.check_tar(path, 'e' * 40)
        path.write_bytes(tar_bytes([(name, b'', tarfile.DIRTYPE) for name in
                                    ('backend/server.py', 'web/creator/index.html', 'requirements.txt')], COMMITS['website']))
        with self.assertRaisesRegex(fetcher.FetchError, 'required files'):
            fetcher.check_tar(path, COMMITS['website'])

    def test_manifest_checksum_and_java_stamp_cannot_disagree(self):
        def changed_manifest(files, key, value):
            manifest = json.loads(files['release-manifest.json'])
            manifest[key] = value
            files['release-manifest.json'] = metadata(manifest)
            checksum_file(files)

        for key, value in [('runId', '124'), ('runAttempt', '1'), ('toolsCommit', 'a' * 40),
                           ('privateInitIncluded', True), ('websiteCommit', 'e' * 40), ('artifacts', {}),
                           ('repository', 'https://github.com/other/repo')]:
            files = bundle()
            changed_manifest(files, key, value)
            with self.subTest(key=key), self.assertRaises(fetcher.FetchError):
                self.verify(files)
        files = bundle()
        files['SHA256SUMS'] += files['SHA256SUMS'].splitlines(keepends=True)[0]
        with self.assertRaisesRegex(fetcher.FetchError, 'duplicate checksum'):
            self.verify(files)
        files = bundle()
        files['java-release.json'] = metadata({'sourceCommit': COMMITS['backend'], 'artifact': {'sha256': '0' * 64, 'bytes': 0}})
        changed_manifest(files, 'artifacts', hashes({name: files[name] for name in fetcher.CODE}))
        with self.assertRaisesRegex(fetcher.FetchError, 'Java release stamp'):
            self.verify(files)

    def test_legacy_manifest_without_daily_metadata_remains_compatible(self):
        files = bundle()
        manifest = json.loads(files['release-manifest.json'])
        self.assertNotIn('workflowSha', manifest)
        self.assertEqual(manifest['toolsCommit'], SHA)
        self.assertEqual(self.verify(files), COMMITS)

    def test_platform_push_allows_different_workflow_and_tools_with_console_provenance(self):
        files = daily_bundle()
        manifest = json.loads(files['release-manifest.json'])
        self.assertNotEqual(manifest['workflowSha'], manifest['toolsCommit'])
        self.assertEqual(self.verify(files, COMMITS['backend']), COMMITS)

    def test_daily_manifest_rejects_invalid_event_ref_sha_or_tools_version(self):
        cases = [{'workflowEvent': 'pull_request'}, {'workflowEvent': None},
                 {'workflowRef': 'refs/heads/arbitrary'}, {'workflowRef': 'refs/tags/main'},
                 {'workflowRef': 'refs/heads/platform/admin-ui'},
                 {'workflowSha': 'e' * 40}, {'workflowSha': None},
                 {'toolsCommit': 'main'}, {'toolsCommit': 'c' * 39}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(fetcher.FetchError):
                self.verify(daily_bundle(**changes), COMMITS['backend'])

    def test_manual_daily_build_must_use_main_workflow(self):
        files = daily_bundle(workflowEvent='workflow_dispatch', workflowRef='refs/heads/main')
        self.assertEqual(self.verify(files, COMMITS['backend']), COMMITS)
        files = daily_bundle(workflowEvent='workflow_dispatch', workflowRef='refs/heads/platform/backend')
        with self.assertRaises(fetcher.FetchError):
            self.verify(files, COMMITS['backend'])

    def test_main_push_tools_must_match_event_commit(self):
        files = daily_bundle(workflowRef='refs/heads/main', workflowSha=COMMITS['website'])
        with self.assertRaisesRegex(fetcher.FetchError, 'Push source commit drifted'):
            self.verify(files, COMMITS['website'])
        files = daily_bundle(workflowRef='refs/heads/main', workflowSha=COMMITS['website'], toolsCommit=COMMITS['website'])
        self.assertEqual(self.verify(files, COMMITS['website']), COMMITS)

    def test_daily_resolution_rejects_wrong_component_refs_and_branch_heads(self):
        refs = {'website': 'main', 'backend': COMMITS['backend'], 'adminUi': 'platform/admin-ui'}
        cases = [{'sourceRefs': None}, {'sourceRefs': []}, {'sourceRefs': {'website': 'main'}},
                 {'sourceRefs': refs | {'extra': 'main'}},
                 {'sourceRefs': refs | {'adminUi': 'main'}},
                 {'sourceRefs': refs | {'adminUi': 'refs/heads/platform/admin-ui'}},
                 {'sourceRefs': refs | {'adminUi': 'https://github.com/other/repo'}},
                 {'sourceRefs': refs | {'adminUi': 'e' * 40}},
                 {'branchHeads': None}, {'branchHeads': []}, {'branchHeads': {'website': COMMITS['website']}},
                 {'branchHeads': COMMITS | {'extra': 'f' * 40}},
                 {'branchHeads': COMMITS | {'adminUi': 'e' * 40}},
                 {'branchHeads': COMMITS | {'adminUi': 'short'}},
                 {'branchHeads': COMMITS | {'adminUi': 123}}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(fetcher.FetchError):
                self.verify(daily_bundle(**changes), COMMITS['backend'])

    def test_daily_console_provenance_cannot_be_missing_or_different(self):
        for comment in (None, 'e' * 40):
            files = daily_bundle()
            files['console.tar.gz'] = tar_bytes([('console/index.html', b'html', tarfile.REGTYPE)], comment)
            manifest = json.loads(files['release-manifest.json'])
            manifest['artifacts'] = hashes({name: files[name] for name in fetcher.CODE})
            files['release-manifest.json'] = metadata(manifest)
            checksum_file(files)
            with self.subTest(comment=comment), self.assertRaisesRegex(fetcher.FetchError, 'Console source provenance'):
                self.verify(files, COMMITS['backend'])

    def verify(self, files, expected_sha=SHA):
        directory = self.root / 'verify'
        directory.mkdir(exist_ok=True)
        for name, data in files.items():
            (directory / name).write_bytes(data)
        return fetcher.verify_bundle(directory, hashes(files), RUN, ATTEMPT, expected_sha)

    def test_credential_input_and_failure_reporting_do_not_echo_secrets(self):
        self.assertEqual(fetcher.credential(io.StringIO(json.dumps({'username': 'x-access-token', 'password': TOKEN}))), TOKEN)
        for raw in [TOKEN, 'x' * 16385, json.dumps({'username': 'user', 'password': TOKEN, 'extra': 'x'}),
                    json.dumps({'username': 'user', 'password': TOKEN + '\n'})]:
            with self.assertRaises(fetcher.FetchError) as error:
                fetcher.credential(io.StringIO(raw))
            self.assertNotIn(TOKEN, str(error.exception))
        error = urllib.error.URLError(SIGNED + TOKEN)
        with self.assertRaises(fetcher.FetchError) as caught:
            fetcher.response(QueueClient([error]), object())
        self.assertNotIn(TOKEN, str(caught.exception))
        stderr, stdout = io.StringIO(), io.StringIO()
        with patch.object(fetcher, 'main', side_effect=ValueError(SIGNED + TOKEN)), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            self.assertEqual(fetcher.cli(), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertNotIn(TOKEN, stderr.getvalue())
        self.assertNotIn('signature=', stderr.getvalue())

    def test_parallel_ranges_reassemble_exact_zip_and_isolate_credentials(self):
        payload = zip_bytes(bundle())
        api, _ = self.clients(payload)
        ranged = RangeClient(payload)
        self.args.download_workers = 8
        clients = iter([api])
        with patch.object(fetcher, 'opener', side_effect=lambda: next(clients, ranged)):
            report = fetcher.fetch(self.args, TOKEN)
        self.assertEqual(report['zipSha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual({p.name for p in self.args.output.iterdir()}, fetcher.FILES)
        self.assertEqual(len(ranged.requests), 2)  # One probe and one sub-8-MiB block.
        covered = []
        for request in ranged.requests:
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsNone(request.get_header('Cookie'))
            self.assertIsNone(request.get_header('Referer'))
            self.assertEqual(request.get_header('Accept-encoding'), 'identity')
            if request.get_header('If-match'):
                self.assertEqual(request.get_header('If-match'), '"stable-object"')
                start, end = map(int, request.get_header('Range')[6:].split('-'))
                covered.extend(range(start, end + 1))
        self.assertEqual(sorted(covered), list(range(len(payload))))
        self.assertEqual(list(self.root.iterdir()), [self.args.output])

    def test_fixed_blocks_keep_fast_workers_busy_with_bounded_concurrency(self):
        block = 8 * 1024**2
        payload = b'a' * block + b'b' * block + b'c' * 31
        client = RangeClient(payload)
        slow_started, later_started = threading.Event(), threading.Event()
        state = {'active': 0, 'peak': 0}

        class CountedResponse(Response):
            def close(self):
                if not self.closed:
                    with client.lock:
                        state['active'] -= 1
                super().close()

        def scheduling(request, start, end, attempt):
            if request.get_header('If-match') is None:
                return None
            with client.lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
            reply = CountedResponse(206, payload[start:end + 1], [
                ('Content-Range', 'bytes %d-%d/%d' % (start, end, len(payload))),
                ('Content-Length', str(end - start + 1)), ('ETag', '"stable-object"')])
            if start == 0:
                slow_started.set()
                # The first block remains slow until a fast worker starts block 3.
                if not later_started.wait(5):
                    reply.close()
                    raise AssertionError('A fast worker did not take the next queued block')
            elif start == block:
                if not slow_started.wait(5):
                    reply.close()
                    raise AssertionError('First block was not started')
            else:
                later_started.set()
            return reply

        client.fault = scheduling
        target = self.root / 'dynamic.zip'
        expected = hashlib.sha256(payload).hexdigest()
        with patch.object(fetcher, 'opener', return_value=client):
            self.assertEqual(fetcher.download_parallel(SIGNED, target, expected, len(payload), 2), (expected, len(payload)))
        self.assertTrue(later_started.is_set())
        self.assertEqual(state, {'active': 0, 'peak': 2})
        intervals = sorted(tuple(map(int, r.get_header('Range')[6:].split('-')))
                           for r in client.requests if r.get_header('If-match'))
        self.assertGreater(len(intervals), 2)
        self.assertEqual(intervals, [(0, block - 1), (block, 2 * block - 1), (2 * block, len(payload) - 1)])
        cursor = 0
        for start, end in intervals:
            self.assertEqual(start, cursor)  # No gap, overlap or duplicate task.
            self.assertLessEqual(end - start + 1, block)
            cursor = end + 1
        self.assertEqual(cursor, len(payload))
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), expected)

    def test_parallel_rejects_range_total_length_encoding_and_etag_changes(self):
        payload = b'0123456789' * 8
        changes = [{'Content-Range': 'bytes 1-10/80'}, {'Content-Range': 'bytes 0-9/81'},
                   {'Content-Length': '100'}, {'ETag': '"different"'}, {'ETag': 'W/"stable-object"'},
                   {'Content-Encoding': 'gzip'}]
        for index, change in enumerate(changes):
            client = RangeClient(payload)
            client.fault = lambda req, start, end, n: client.result(start, end, **change) if req.get_header('If-match') else None
            with self.subTest(change=change), patch.object(fetcher, 'opener', return_value=client), self.assertRaises(fetcher.FetchError):
                fetcher.download_parallel(SIGNED, self.root / ('bad%d.zip' % index), '0' * 64, len(payload), 8)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_parallel_range_transient_and_truncated_response_retry_is_bounded(self):
        payload = b'0123456789' * 8
        client = RangeClient(payload)
        def fault(request, start, end, attempt):
            if request.get_header('If-match') and attempt == 1:
                if start == 0:
                    result = client.result(start, end)
                    result.truncate(2)
                    return result
                return Response(503)
        client.fault = fault
        with patch.object(fetcher, 'opener', return_value=client), patch.object(fetcher, 'RANGE_BLOCK_BYTES', 10):
            digest, length = fetcher.download_parallel(SIGNED, self.root / 'retry.zip', hashlib.sha256(payload).hexdigest(), len(payload), 8)
        self.assertEqual(length, len(payload))
        self.assertEqual((self.root / 'retry.zip').read_bytes(), payload)
        self.assertEqual(set(client.attempts.values()), {1, 2})
        # A permanently failing probe has exactly three attempts, without creating output.
        client = RangeClient(payload, lambda *_: Response(503))
        with patch.object(fetcher, 'opener', return_value=client), self.assertRaisesRegex(fetcher.FetchError, 'three attempts'):
            fetcher.download_parallel(SIGNED, self.root / 'failed.zip', digest, len(payload), 8)
        self.assertEqual(len(client.requests), 3)
        self.assertFalse((self.root / 'failed.zip').exists())

    def test_parallel_rejects_ignored_range_or_unsafe_redirect_without_fallback(self):
        for result in (Response(200, b'not-ranged'), Response(302, headers=[('Location', 'http://example.invalid/a')])):
            client = QueueClient([result])
            with patch.object(fetcher, 'opener', return_value=client), self.assertRaises(fetcher.FetchError):
                fetcher.download_parallel(SIGNED, self.root / 'rejected.zip', '0' * 64, 80, 8)
            self.assertEqual(len(client.requests), 1)
        client = QueueClient([Response(302, headers=[('Location', SIGNED + '-hop')]),
                              Response(206, b'x', [('Content-Range', 'bytes 0-0/80'), ('Content-Length', '1'), ('ETag', '"tag"')])])
        with patch.object(fetcher, 'opener', return_value=client):
            with fetcher.range_response(SIGNED, 0, 0) as result:
                self.assertEqual(result.code, 206)
        self.assertTrue(all(request.get_header('Authorization') is None for request in client.requests))
        self.assertTrue(all(request.get_header('Range') == 'bytes=0-0' for request in client.requests))

    def test_parallel_final_digest_mismatch_cannot_publish_release(self):
        payload = zip_bytes(bundle())
        api, _ = self.clients(payload, digest='sha256:' + '0' * 64)
        ranged = RangeClient(payload)
        self.args.download_workers = 8
        clients = iter([api])
        with patch.object(fetcher, 'opener', side_effect=lambda: next(clients, ranged)), self.assertRaisesRegex(fetcher.FetchError, 'ZIP size or SHA-256'):
            fetcher.fetch(self.args, TOKEN)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
