"""CI package contract tests: synthetic files only; no network, services or secrets."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_package', ROOT / 'deploy/ci/package-public-release.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


def repository(root, files):
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'CI synthetic test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    for name, text in files.items():
        item = root / name
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text(text, encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'synthetic fixture')
    return git(root, 'rev-parse', 'HEAD')


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ui = self.root / 'ui'
        self.site = self.root / 'site'
        ui_sha = repository(self.ui, {'.gitignore': 'dist-leanschool/\n'})
        site_sha = repository(self.site, {f'{d}/file.txt': 'source' for d in ('backend', 'web', 'scripts', 'deploy')} | {
            'requirements.txt': 'example==1\n', 'frontend/package.json': '{}',
            'web/creator/design/index.html': '<script type="module" src="/design/assets/app.js"></script>',
            'web/creator/design/assets/app.js': '/* compiled public design */',
        })
        self.commits = {'website': site_sha, 'backend': 'b' * 40, 'adminUi': ui_sha}
        self.resolution = {'format': 1, 'repository': 'https://github.com/papaoo/bojizhuye',
                           'workflowEvent': 'push', 'workflowRef': 'refs/heads/platform/backend',
                           'workflowSha': 'b' * 40, 'toolsCommit': 'c' * 40,
                           'commits': dict(self.commits), 'branchHeads': dict(self.commits),
                           'sourceRefs': {'website': 'main', 'backend': 'b' * 40, 'adminUi': 'platform/admin-ui'}}
        dist = self.ui / 'dist-leanschool'
        (dist / 'assets').mkdir(parents=True)
        (dist / 'index.html').write_text('<script src="/console/assets/app.js"></script>', encoding='utf-8')
        (dist / 'assets/app.js').write_text('/* public code */', encoding='utf-8')
        self.backend = self.root / 'backend-artifact'
        self.backend.mkdir()
        jar = self.backend / 'yudao-server.jar'
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('BOOT-INF/classes/cn/iocoder/yudao/server/YudaoServerApplication.class', b'\xca\xfe\xba\xbe\x00\x00\x00\x45')
            for module in ci.preflight.REQUIRED_MODULES:
                archive.writestr(f'BOOT-INF/lib/yudao-module-{module}-1.jar', b'placeholder')
        (self.backend / 'java-release.json').write_text(json.dumps({'sourceCommit': 'b' * 40, 'artifact': ci.preflight.inspect_jar(jar)}), encoding='utf-8')
        self.console = self.root / 'console-artifact'
        ci.package_console(self.ui, self.console, self.commits['adminUi'])

    def bundle(self):
        ci.package_bundle(self.site, self.backend, self.console, self.root / 'release', self.resolution, '123', '1')
        return self.root / 'release'

    def test_bundle_roundtrip_has_exact_allowlist_and_git_provenance(self):
        output = self.bundle()
        self.assertEqual({p.name for p in output.iterdir()}, ci.ARTIFACTS | {'release-manifest.json', 'SHA256SUMS'})
        manifest = json.loads((output / 'release-manifest.json').read_text())
        self.assertFalse(manifest['privateInitIncluded'])
        self.assertEqual(manifest['toolsCommit'], 'c' * 40)
        self.assertEqual(manifest['workflowSha'], self.commits['backend'])
        self.assertNotEqual(manifest['workflowSha'], manifest['toolsCommit'])
        self.assertEqual(manifest['commits'], self.commits)
        with tarfile.open(output / 'website.tar.gz') as archive:
            self.assertEqual(archive.pax_headers['comment'], self.commits['website'])
            self.assertIn('web/creator/design/index.html', archive.getnames())
            self.assertIn('web/creator/design/assets/app.js', archive.getnames())
            self.assertNotIn('frontend/package.json', archive.getnames())
        with tarfile.open(output / 'console.tar.gz') as archive:
            self.assertEqual(archive.pax_headers['comment'], self.commits['adminUi'])
            self.assertTrue(all(m.isfile() and m.mode == 0o644 and m.name.startswith('console/') for m in archive.getmembers()))
        for line in (output / 'SHA256SUMS').read_text().splitlines():
            checksum, name = line.split('  ', 1)
            self.assertEqual(checksum, hashlib.sha256((output / name).read_bytes()).hexdigest())

    def test_extra_private_downloaded_file_is_rejected(self):
        (self.backend / 'private-init.tar.gz').write_bytes(b'synthetic')
        with self.assertRaisesRegex(ValueError, 'Unexpected files'):
            self.bundle()

    def test_jar_tamper_is_rejected(self):
        with (self.backend / 'yudao-server.jar').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.bundle()

    def test_dirty_fixed_source_is_rejected(self):
        (self.site / 'requirements.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'clean reviewed'):
            self.bundle()

    def test_secret_or_sourcemap_in_dist_is_rejected(self):
        for name in ('.env.local', 'secret.pem', 'data.sql', 'source.js.map'):
            item = self.ui / 'dist-leanschool' / name
            item.write_text('synthetic')
            with self.assertRaisesRegex(ValueError, 'Forbidden'):
                ci.package_console(self.ui, self.root / 'unexpected', self.commits['adminUi'])
            item.unlink()

    def test_archive_path_traversal_and_duplicate_are_rejected(self):
        for names in (['console/../escape'], ['console/index.html', 'console/index.html']):
            path = self.root / 'unsafe.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                for name in names:
                    info = tarfile.TarInfo(name)
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                ci.validate_console(path)

    def test_missing_console_static_file_is_rejected(self):
        (self.ui / 'dist-leanschool/assets/app.js').unlink()
        with self.assertRaisesRegex(ValueError, 'missing or out-of-prefix'):
            ci.package_console(self.ui, self.root / 'bad-console', self.commits['adminUi'])

    def test_wrong_backend_version_rejected(self):
        self.resolution['commits']['backend'] = 'd' * 40
        self.resolution['branchHeads']['backend'] = 'd' * 40
        self.resolution['sourceRefs']['backend'] = 'd' * 40
        self.resolution['workflowSha'] = 'd' * 40
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.bundle()

    def test_wrong_console_version_rejected(self):
        self.resolution['commits']['adminUi'] = 'd' * 40
        self.resolution['branchHeads']['adminUi'] = 'd' * 40
        with self.assertRaisesRegex(ValueError, 'Console archive source commit mismatch'):
            self.bundle()

    def test_push_sha_cannot_drift(self):
        self.resolution['workflowSha'] = 'e' * 40
        with self.assertRaisesRegex(ValueError, 'Push event source version drifted'):
            self.bundle()

    def test_resolution_rejects_wrong_origin_or_components(self):
        cases = [('repository', 'https://github.com/other/repo'), ('workflowRef', 'refs/heads/other'),
                 ('workflowEvent', 'pull_request'), ('workflowSha', 'abc'),
                 ('toolsCommit', 'main'), ('commits', {'website': 'd' * 40}),
                 ('sourceRefs', {**self.resolution['sourceRefs'], 'backend': 'main'})]
        for key, value in cases:
            with self.subTest(key=key), self.assertRaises(ValueError):
                ci.validate_resolution({**self.resolution, key: value})

    def test_workflow_auto_builds_only_allowed_branches_and_actions_pinned(self):
        text = (ROOT / '.github/workflows/leanschool-release.yml').read_text()
        self.assertIn('  workflow_dispatch:', text)
        self.assertIn('    branches: [main, platform/backend, platform/admin-ui]', text)
        self.assertNotRegex(text, r'(?m)^  (pull_request|pull_request_target|schedule|workflow_run):')
        self.assertEqual(text.count("if: github.repository == 'papaoo/bojizhuye'"), 1)
        self.assertIn('  contents: read', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('private-init', text)
        self.assertNotIn('build-release.py', text)
        uses = re.findall(r'uses:\s*(\S+)', text)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r'actions/[a-z-]+@[0-9a-f]{40}', name) for name in uses))
        verified = json.loads((ROOT / 'deploy/ci/actions.lock.json').read_text())['actions']
        self.assertEqual(set(uses), {name + '@' + meta['sha'] for name, meta in verified.items()})
        self.assertTrue(all(int(days) <= 3 for days in re.findall(r'retention-days:\s*(\d+)', text)))
        self.assertEqual(text.count('persist-credentials: false'), 8)
        self.assertIn("github.event_name == 'push' && github.ref == 'refs/heads/main' && github.sha || 'main'", text)
        self.assertEqual(text.count('ref: ${{ needs.resolve.outputs.toolsCommit }}'), 3)
        self.assertIn('needs: [resolve, website, backend, admin-ui]', text)
        self.assertIn('--resolution "$RUNNER_TEMP/resolution/resolved-release.json"', text)
        self.assertIn('-Dsurefire.failIfNoSpecifiedTests=false', text)
        self.assertIn('LocalPaymentBindingServiceTest', text)
        self.assertIn('--max_old_space_size=6144', text)
        self.assertNotIn('--max_old_space_size=8192', text)
        self.assertLess(text.index('Build console within runner memory budget'),
                        text.index('Check generated types and production route isolation'))


if __name__ == '__main__':
    unittest.main()
