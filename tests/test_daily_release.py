"""Daily release boundaries, using temporary artifacts and mocked system services."""
import importlib.util
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('daily_release', ROOT / 'deploy/ubuntu/deploy-daily-release.py')
daily = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(daily)


def jar(path, fields=b'field-v1', other=b'logic-v1', mapper=b'SELECT id FROM example', module='1.0'):
    module_bytes = io.BytesIO()
    with zipfile.ZipFile(module_bytes, 'w') as nested:
        nested.writestr('cn/project/dal/dataobject/ExampleDO.class', fields)
        nested.writestr('cn/project/service/ExampleService.class', other)
        nested.writestr('mapper/ExampleMapper.xml', mapper)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('BOOT-INF/lib/yudao-module-system-' + module + '.jar', module_bytes.getvalue())


class SchemaGateTests(unittest.TestCase):
    def test_java_business_change_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after = Path(folder) / 'old.jar', Path(folder) / 'new.jar'
            jar(before)
            jar(after, other=b'logic-v2')
            self.assertEqual(daily.jar_schema(before), daily.jar_schema(after))

    def test_java_data_field_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after = Path(folder) / 'old.jar', Path(folder) / 'new.jar'
            jar(before)
            jar(after, fields=b'field-v2')
            self.assertNotEqual(daily.jar_schema(before), daily.jar_schema(after))

    def test_java_mapper_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after = Path(folder) / 'old.jar', Path(folder) / 'new.jar'
            jar(before)
            jar(after, mapper=b'SELECT missing_column FROM example')
            self.assertNotEqual(daily.jar_schema(before), daily.jar_schema(after))

    def test_java_version_label_alone_does_not_change_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after = Path(folder) / 'old.jar', Path(folder) / 'new.jar'
            jar(before, module='1.0')
            jar(after, module='2.0')
            self.assertEqual(daily.jar_schema(before), daily.jar_schema(after))

    def test_java_missing_persistence_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'empty.jar'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('unrelated', b'x')
            with self.assertRaises(daily.ReleaseError):
                daily.jar_schema(path)

    def test_website_ordinary_handler_change_does_not_change_schema(self):
        before = {'backend/server.py': b'def get():\n    return "old"\n'}
        after = {'backend/server.py': b'def get():\n    return "new"\n'}
        self.assertEqual(daily.website_schema(before), daily.website_schema(after))

    def test_website_added_ddl_is_detected(self):
        before = {'backend/new.py': b'def init(db):\n    return 1\n'}
        after = {'backend/new.py': b'def init(db):\n    db.execute("CREATE TABLE added(id int)")\n'}
        self.assertNotEqual(daily.website_schema(before), daily.website_schema(after))

    def test_website_schema_function_condition_change_is_detected(self):
        before = {'backend/store.py': b'def init(db):\n    if True:\n        db.execute("CREATE TABLE same(id int)")\n'}
        after = {'backend/store.py': b'def init(db):\n    if False:\n        db.execute("CREATE TABLE same(id int)")\n'}
        self.assertNotEqual(daily.website_schema(before), daily.website_schema(after))

    def test_website_migration_and_schema_modules_are_exact(self):
        for name in ('scripts/migrate_x.py', 'backend/x_schema.py'):
            with self.subTest(name=name):
                self.assertNotEqual(daily.website_schema({name: b'x = 1'}),
                                    daily.website_schema({name: b'x = 2'}))

    def test_executescript_dynamic_argument_change_is_detected(self):
        before = {'backend/db.py': b'def init(db):\n    db.executescript(old_sql)\n'}
        after = {'backend/db.py': b'def init(db):\n    db.executescript(new_sql)\n'}
        self.assertNotEqual(daily.website_schema(before), daily.website_schema(after))


class ArtifactAndBoundaryTests(unittest.TestCase):
    def test_component_changes_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'artifact.sha256').write_text('old-ui')
            manifest = {'commits': {'website': 'old-site', 'backend': 'same-source'}, 'artifacts': {
                'yudao-server.jar': {'sha256': 'old-java'}, 'console.tar.gz': {'sha256': 'new-ui'}}}
            self.assertEqual(daily.component_changes(manifest, {'jar': {'sha256': 'old-java'}, 'sourceCommit': 'same-source'},
                Path('old-site'), path), {'website': False, 'backend': False, 'adminUi': True})

    def test_same_source_rebuild_does_not_restart_java_or_switch_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'artifact.sha256').write_text('old-ui')
            commits = {'website': 'old-site', 'backend': 'b' * 40, 'adminUi': 'c' * 40}
            manifest = {'commits': commits, 'artifacts': {
                'yudao-server.jar': {'sha256': 'new-java'}, 'console.tar.gz': {'sha256': 'new-ui'}}}
            recorded = {'format': 1, 'commits': commits, 'artifacts': {
                'yudao-server.jar': {'sha256': 'old-java'}, 'console.tar.gz': {'sha256': 'old-ui'}}}
            state = {'jar': {'sha256': 'old-java'}, 'sourceCommit': commits['backend']}
            self.assertEqual(daily.component_changes(manifest, state, Path('old-site'), path, recorded),
                             {'website': False, 'backend': False, 'adminUi': False})

    def test_stale_active_provenance_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'artifact.sha256').write_text('ui-current')
            with self.assertRaises(daily.ReleaseError):
                daily.component_changes({}, {'sourceCommit': 'a' * 40, 'jar': {'sha256': 'java'}},
                                        Path('site'), path, {'format': 1})

    def test_extra_release_file_is_rejected_before_helper_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'secret.env').write_text('private-placeholder')
            helper = SimpleNamespace(FILES={'release-manifest.json'})
            with patch.object(daily, 'trusted_directory', return_value=path):
                with self.assertRaises(daily.ReleaseError):
                    daily.verify_bundle(path, helper)

    def test_platform_build_passes_workflow_sha_separately_from_tools_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            manifest = {'runId': '8', 'runAttempt': '1', 'toolsCommit': 'a' * 40, 'workflowSha': 'b' * 40}
            (path / 'release-manifest.json').write_text(json.dumps(manifest))
            helper = SimpleNamespace(FILES={'release-manifest.json'}, LIMITS={'release-manifest.json': 4096},
                                     verify_bundle=Mock())
            with patch.object(daily, 'trusted_directory', return_value=path), \
                    patch.object(daily, 'regular', side_effect=lambda p, private=False: p):
                daily.verify_bundle(path, helper)
            self.assertEqual(helper.verify_bundle.call_args.args[-1], 'b' * 40)

    def test_nonzero_subprocess_output_never_escapes(self):
        result = subprocess.CompletedProcess([], 2, 'SECRET_OUTPUT', 'SECRET_ERROR')
        with patch.object(daily.subprocess, 'run', return_value=result):
            with self.assertRaises(daily.ReleaseError) as caught:
                daily.run(['test'])
            self.assertNotIn('SECRET', str(caught.exception))

    def test_subprocess_timeout_is_bounded_and_redacted(self):
        with patch.object(daily.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['private'], 2)):
            with self.assertRaises(daily.ReleaseError) as caught:
                daily.run(['test'], timeout=2)
            self.assertNotIn('private', str(caught.exception))

    def test_platform_prepare_never_requests_database_initialization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'console.tar.gz').write_bytes(b'archive')
            state = {'seedDir': '/var/lib/leanschool/install/reviewed', 'javaPath': '/opt/jdk/java',
                     'database': 'leanschool', 'dbUser': 'leanschool_app', 'redisPort': 6382,
                     'javaXmxMiB': 1536}
            command = daily.platform_prepare_command(path, {'state': state}, Path('/trusted'))
            self.assertIn('prepare', command)
            self.assertNotIn('init-db', command)
            self.assertIn(str(Path('/var/lib/leanschool/install/reviewed')), command)
            self.assertNotIn('--admin-password-file', command)

class PublishTests(unittest.TestCase):
    def plan(self, website=False, backend=False, ui=False):
        artifacts = {name: {'sha256': 'a' * 64, 'bytes': 1} for name in
                     ('console.tar.gz', 'website.tar.gz', 'yudao-server.jar', 'java-release.json')}
        return {'changes': {'website': website, 'backend': backend, 'adminUi': ui},
                'state': {'uiRelease': '/old-ui', 'uiSha256': 'a' * 64, 'uiFiles': 1, 'uiExtractedBytes': 1},
                'manifest': {'runId': '7', 'runAttempt': '1', 'commits': {}, 'artifacts': artifacts},
                'recorded': {'artifacts': artifacts},
                'links': {'website': Path('/old-site'), 'backend': Path('/old-java'), 'adminUi': Path('/old-ui')},
                'certificates': {}, 'schema': 'known'}

    def helper(self):
        return SimpleNamespace(root=Path('/trusted'), platform=SimpleNamespace(swap_link=Mock()),
                               site=SimpleNamespace(assert_stopped=Mock()))

    def test_noop_does_not_create_backup_or_restart_any_service(self):
        with patch.object(daily, 'snapshot_configuration') as snapshot, patch.object(daily, 'run') as execute:
            result = daily.publish(Path('/bundle'), self.plan(), self.helper())
            self.assertFalse(result['deployed'])
            snapshot.assert_not_called()
            execute.assert_not_called()

    def harness(self, folder):
        # Independent mocks leave filesystem writes constrained to this temporary directory.
        path = Path(folder)
        return [patch.object(daily, 'BACKUPS', path / 'backups'),
                patch.object(daily, 'CURRENT', path / 'current.json'),
                patch.object(daily, 'ETC', path),
                patch.object(daily, 'trusted_directory', side_effect=lambda p: p),
                patch.object(daily, 'snapshot_configuration', return_value=[]),
                patch.object(daily, 'backup_databases'),
                patch.object(daily, 'database_structure', return_value='known'),
                patch.object(daily, 'prepare_ui', return_value=Path('/new-ui')),
                patch.object(daily, 'prepare_website', return_value=Path('/new-site')),
                patch.object(daily, 'platform_prepare_command', return_value=['platform', 'prepare']),
                patch.object(daily, 'read_json', return_value={}),
                patch.object(daily, 'wait_health')]

    def test_ui_only_switch_does_not_restart_java(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            execute = stack.enter_context(patch.object(daily, 'run'))
            helper = self.helper()
            result = daily.publish(Path('/bundle'), self.plan(ui=True), helper)
            self.assertTrue(result['deployed'])
            execute.assert_not_called()
            helper.platform.swap_link.assert_called_once_with(daily.PLATFORM / 'ui/current', Path('/new-ui'))

    def test_backend_stops_existing_java_before_memory_preflight(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            execute = stack.enter_context(patch.object(daily, 'run'))
            daily.publish(Path('/bundle'), self.plan(backend=True), self.helper())
            calls = [call.args[0] for call in execute.call_args_list]
            self.assertEqual(calls[0], ['systemctl', 'stop', 'leanschool-java'])
            self.assertEqual(calls[1], ['platform', 'prepare'])
            self.assertEqual(calls[2][-1], 'activate')

    def test_failed_prepare_restores_control_plane_and_reports_no_database_restore(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            stack.enter_context(patch.object(daily, 'run', side_effect=[None, daily.ReleaseError('failed')]))
            rollback = stack.enter_context(patch.object(daily, 'rollback'))
            with self.assertRaises(daily.ReleaseError) as error:
                daily.publish(Path('/bundle'), self.plan(backend=True), self.helper())
            self.assertIn('previous code/configuration recovered', str(error.exception))
            self.assertEqual(error.exception.code, 'PUBLISH_FAILED_ROLLED_BACK')
            rollback.assert_called_once()
            report = json.loads(next((Path(folder) / 'backups').glob('*/result.json')).read_text())
            self.assertFalse(report['databaseRestored'])
            self.assertTrue(report['codeRollback'])

    def test_failed_rollback_is_not_reported_as_recovered(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            stack.enter_context(patch.object(daily, 'run', side_effect=daily.ReleaseError('failed')))
            stack.enter_context(patch.object(daily, 'rollback', side_effect=daily.ReleaseError('also failed')))
            with self.assertRaises(daily.ReleaseError) as error:
                daily.publish(Path('/bundle'), self.plan(website=True), self.helper())
            self.assertIn('recovery needs inspection', str(error.exception))
            self.assertEqual(error.exception.code, 'ROLLBACK_FAILED')
            report = json.loads(next((Path(folder) / 'backups').glob('*/result.json')).read_text())
            self.assertFalse(report['codeRollback'])
            self.assertFalse(report['databaseRestored'])

    def test_backup_failure_never_stops_any_service(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            stack.enter_context(patch.object(daily, 'backup_databases', side_effect=daily.ReleaseError('backup')))
            execute = stack.enter_context(patch.object(daily, 'run'))
            with self.assertRaises(daily.ReleaseError):
                daily.publish(Path('/bundle'), self.plan(backend=True), self.helper())
            execute.assert_not_called()

    def test_schema_change_during_snapshot_prevents_activation(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            stack.enter_context(patch.object(daily, 'database_structure', return_value='changed'))
            execute = stack.enter_context(patch.object(daily, 'run'))
            with self.assertRaises(daily.ReleaseError):
                daily.publish(Path('/bundle'), self.plan(website=True), self.helper())
            execute.assert_not_called()

    def test_website_switch_runs_no_candidate_install_or_migration(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            for item in self.harness(folder):
                stack.enter_context(item)
            execute = stack.enter_context(patch.object(daily, 'run'))
            helper = self.helper()
            daily.publish(Path('/bundle'), self.plan(website=True), helper)
            commands = [call.args[0] for call in execute.call_args_list]
            self.assertEqual(commands, [
                ['systemctl', 'stop', 'bojizhuye-public-gateway', 'bojizhuye'],
                ['systemctl', 'start', 'bojizhuye', 'bojizhuye-public-gateway']])
            helper.site.assert_stopped.assert_called_once()


class BackupTests(unittest.TestCase):
    def test_online_snapshot_preserves_source_and_has_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            source, backup = path / 'source.sqlite3', path / 'backup'
            backup.mkdir()
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE values_table (value TEXT)')
                db.execute('INSERT INTO values_table VALUES (?)', ('synthetic data',))
                db.commit()
            before = source.read_bytes()
            def dump(*args, **kwargs):
                kwargs['stdout'].write(b'-- synthetic SQL backup\n')
                return subprocess.CompletedProcess(args[0], 0)
            with patch.object(daily, 'SITE_DB', source), patch.object(daily.subprocess, 'run', side_effect=dump) as runner:
                daily.backup_databases(backup)
            self.assertEqual(source.read_bytes(), before)
            with closing(sqlite3.connect(backup / 'website.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT value FROM values_table').fetchone()[0], 'synthetic data')
            argv = runner.call_args.args[0]
            self.assertIn('--single-transaction', argv)
            self.assertIn('--skip-lock-tables', argv)
            self.assertNotIn('--password', ' '.join(argv))

    def test_mysql_dump_failure_rejects_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.CompletedProcess([], 1, stderr=b'PRIVATE ERROR')
            with patch.object(daily.subprocess, 'run', return_value=result):
                with self.assertRaises(daily.ReleaseError) as error:
                    daily.backup_databases(Path(folder))
            self.assertNotIn('PRIVATE', str(error.exception))


class WebsitePreparationTests(unittest.TestCase):
    def test_reuses_installed_dependencies_without_running_candidate_code(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            site = root / 'site'
            previous = site / 'releases' / ('a' * 40)
            (previous / '.venv').mkdir(parents=True)
            bundle = root / 'bundle'
            bundle.mkdir()
            archive = bundle / 'website.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                for name, data in {'backend/server.py': b'print("synthetic code")',
                                   'requirements.txt': b'example==1.0\n'}.items():
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    output.addfile(item, io.BytesIO(data))
            expected = daily.digest(archive)
            plan = {'manifest': {'commits': {'website': 'b' * 40},
                                 'artifacts': {'website.tar.gz': {'sha256': expected}}},
                    'links': {'website': previous}}
            validator = Mock()
            helper = SimpleNamespace(site=SimpleNamespace(install=SimpleNamespace(validate_archive=validator)))
            with patch.object(daily, 'SITE', site), patch.object(daily, 'trusted_directory'), \
                    patch.object(Path, 'symlink_to') as link, patch.object(daily, 'run') as execute:
                target = daily.prepare_website(bundle, plan, helper)
            self.assertEqual((target / 'backend/server.py').read_bytes(), b'print("synthetic code")')
            self.assertEqual((target / '.deployment-sha256').read_text().strip(), expected)
            validator.assert_called_once_with(archive, 'b' * 40)
            link.assert_called_once_with((previous / '.venv').resolve(), target_is_directory=True)
            execute.assert_not_called()

    def test_partial_existing_release_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            revision = 'a' * 40
            target = site / 'releases' / revision
            target.mkdir(parents=True)
            sentinel = target / 'preserve.txt'
            sentinel.write_text('existing incomplete install')
            plan = {'manifest': {'commits': {'website': revision},
                                 'artifacts': {'website.tar.gz': {'sha256': 'b' * 64}}}}
            helper = SimpleNamespace(site=SimpleNamespace(install=SimpleNamespace(validate_archive=Mock())))
            with patch.object(daily, 'SITE', site):
                with self.assertRaises(daily.ReleaseError):
                    daily.prepare_website(site, plan, helper)
            self.assertEqual(sentinel.read_text(), 'existing incomplete install')


if __name__ == '__main__':
    unittest.main()
