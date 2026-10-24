#!/usr/bin/env python3
"""Publish a verified code release on the registered LEAN SCHOOL production host.

Default is a read-only plan (apart from the process lock). No schema import,
certificate change, candidate installation script, or database restore is run.
The caller must authenticate the Actions run before handing over its six files.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
import urllib.error
import uuid
import zipfile


ETC = Path('/etc/leanschool')
SITE = Path('/opt/bojizhuye')
PLATFORM = Path('/opt/leanschool')
SITE_DB = Path('/var/lib/bojizhuye/clubs.sqlite3')
BACKUPS = Path('/var/backups/leanschool-daily')
LOCK = Path('/run/lock/leanschool-daily-release.lock')
HOST_MARKER = ETC / 'daily-release-host.json'
CURRENT = ETC / 'daily-current.json'
HOSTS = ('boji.tensorbot.xyz', 'www.leanschool.net')
SERVICES = ('nginx', 'mysql', 'leanschool-redis', 'leanschool-java',
            'bojizhuye', 'bojizhuye-public-gateway')
TOOL_FILES = ('deploy/ubuntu/fetch-github-artifact.py', 'deploy/ubuntu/install.py',
              'deploy/ubuntu/upgrade-fullstack-site.py', 'deploy/yudao/preflight.py',
              'deploy/yudao/platform-install.py', 'deploy/yudao/prepare-bootstrap.py',
              'deploy/yudao/application-prod.yaml', 'deploy/yudao/leanschool-java.service',
              'deploy/yudao/leanschool-redis.service')
CONFIG_FILES = tuple(ETC / name for name in (
    'platform-state.json', 'schema-ready.json', 'java.env', 'platform-secrets.json',
    'bridge-secrets.env', 'application-prod.yaml', 'redis.conf', 'previous-release.json',
    'daily-current.json')) + tuple(Path(p) for p in (
    '/etc/systemd/system/leanschool-java.service', '/etc/systemd/system/leanschool-redis.service',
    '/etc/systemd/system/bojizhuye.service', '/etc/systemd/system/bojizhuye-public-gateway.service',
    '/etc/bojizhuye/bojizhuye.env', '/etc/bojizhuye/public-gateway.env',
    '/etc/nginx/sites-available/bojizhuye.conf', '/etc/nginx/sites-available/leanschool.conf',
    '/etc/nginx/snippets/leanschool-proxy.conf',
    '/opt/leanschool/deploy/preflight.py', '/opt/leanschool/deploy/prepare-bootstrap.py',
    '/opt/leanschool/deploy/platform-install.py'))
FIXED_SITE_FILES = ('requirements.txt', 'deploy/ubuntu/bojizhuye.service.in',
                    'deploy/ubuntu/bojizhuye.env.in', 'deploy/ubuntu/bojizhuye.nginx.conf.in',
                    'deploy/ubuntu/bojizhuye-public-gateway.service.in',
                    'deploy/ubuntu/bojizhuye-public-gateway.env.in')
DDL = re.compile(r'\b(?:CREATE\s+(?:(?:UNIQUE|TEMP|TEMPORARY)\s+)?(?:TABLE|INDEX|TRIGGER|VIEW)|'
                 r'ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|TRIGGER|VIEW)|'
                 r'PRAGMA\s+(?:user_version|schema_version))\b', re.I)


class ReleaseError(Exception):
    """Messages are fixed operator guidance, never subprocess/config contents."""

    def __init__(self, message, code='PREFLIGHT_FAILED'):
        super().__init__(message)
        self.code = code


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(argv, *, timeout=60, input_text=None, check=True):
    try:
        result = subprocess.run([str(a) for a in argv], input=input_text, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ReleaseError('A deployment operation exceeded its deadline; output withheld.') from None
    if check and result.returncode:
        raise ReleaseError('A deployment operation failed; output withheld.')
    return result


def regular(path, private=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseError('An expected regular deployment file is missing or is a symlink.')
    if os.name == 'posix':
        info = path.stat()
        mask = 0o077 if private else 0o022
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & mask:
            raise ReleaseError('Deployment inputs must be root owned with restricted write permissions.')
    return path


def trusted_directory(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or not parent.is_dir():
            raise ReleaseError('Deployment directory or an ancestor is not a real directory.')
        if os.name == 'posix' and (parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022):
            raise ReleaseError('Deployment directory ancestry must be root owned and not writable by others.')
    return path


def read_json(path, private=False):
    regular(path, private)
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    if path.is_symlink():
        raise ReleaseError('Refusing a symlink metadata destination.')
    temporary = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def modules(root):
    root = trusted_directory(root)
    for name in TOOL_FILES:
        trusted_directory((root / name).parent)
        regular(root / name)
    preflight = load('preflight', root / 'deploy/yudao/preflight.py')
    sys.modules['preflight'] = preflight
    return SimpleNamespace(
        root=root, preflight=preflight,
        platform=load('daily_platform', root / 'deploy/yudao/platform-install.py'),
        site=load('daily_site', root / 'deploy/ubuntu/upgrade-fullstack-site.py'),
        fetch=load('daily_fetch', root / 'deploy/ubuntu/fetch-github-artifact.py'))


@contextmanager
def deployment_lock():
    import fcntl
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(LOCK, flags, 0o600)
    with os.fdopen(fd, 'a') as stream:
        if os.fstat(stream.fileno()).st_uid != 0:
            raise ReleaseError('The deployment lock is not root owned.')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError('Another daily release is already running.') from None
        yield


def verify_bundle(directory, helper):
    directory = trusted_directory(directory)
    if {p.name for p in directory.iterdir()} != helper.FILES:
        raise ReleaseError('The release directory must contain exactly the verified six code files.')
    hashes = {}
    for name in helper.FILES:
        path = regular(directory / name, private=True)
        size = path.stat().st_size
        if not 0 < size <= helper.LIMITS[name]:
            raise ReleaseError('A release file is outside its approved size limit.')
        hashes[name] = {'sha256': digest(path), 'bytes': size}
    manifest = read_json(directory / 'release-manifest.json', private=True)
    helper.verify_bundle(directory, hashes, int(manifest['runId']), int(manifest['runAttempt']),
                         manifest.get('workflowSha', manifest['toolsCommit']))
    return manifest


def owned_link(path, parent):
    if not path.is_symlink():
        raise ReleaseError('The active application path is not a managed release symlink.')
    target = path.resolve(strict=True)
    if target.parent != parent or not target.is_dir():
        raise ReleaseError('An active release points outside its owned release directory.')
    trusted_directory(target)
    return target


def assert_host():
    if os.name != 'posix' or sys.platform != 'linux' or os.geteuid() != 0:
        raise ReleaseError('Daily deployment requires root on the registered Linux host.')
    record = read_json(HOST_MARKER, private=True)
    if (record.get('format') != 1 or record.get('publicIp') != '152.136.108.87'
            or record.get('machineId') != Path('/etc/machine-id').read_text().strip()):
        raise ReleaseError('This machine does not match the registered production host.')
    for unit in SERVICES:
        if run(['systemctl', 'is-active', '--quiet', unit], check=False).returncode:
            raise ReleaseError('An existing production service is not healthy; repair it before publishing.')
    run(['nginx', '-t'])


def mysql(sql):
    return run(['/usr/bin/mysql', '--no-defaults', '--user=root', '--protocol=socket', '--batch',
                '--skip-column-names', '--default-character-set=utf8mb4'], input_text=sql).stdout.strip()


def database_structure():
    # Metadata only; never query business rows for a deployment gate.
    sql = """SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COALESCE(COLUMN_DEFAULT,'<NULL>'), EXTRA
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='leanschool' ORDER BY TABLE_NAME, ORDINAL_POSITION;
SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME
FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='leanschool' ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;"""
    with closing(sqlite3.connect(SITE_DB.resolve().as_uri() + '?mode=ro', uri=True)) as database:
        sqlite_schema = database.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256((mysql(sql) + '\n' + json.dumps(sqlite_schema, ensure_ascii=False)).encode()).hexdigest()


def website_sources(path, archive=False):
    if archive:
        with tarfile.open(path, 'r:gz') as source:
            result = {}
            for item in source:
                selected = item.name in FIXED_SITE_FILES or (
                    item.name.startswith(('backend/', 'scripts/')) and item.name.endswith('.py'))
                if not item.isfile() or not selected:
                    continue
                if item.size > 16 * 1024**2:
                    raise ReleaseError('A website source file exceeds its review limit.')
                result[item.name] = source.extractfile(item).read()
            return result
    result = {}
    for folder in ('backend', 'scripts'):
        for item in (path / folder).rglob('*.py'):
            if item.is_symlink():
                raise ReleaseError('Current source tree contains an unexpected symlink.')
            if item.stat().st_size > 16 * 1024**2:
                raise ReleaseError('A website source file exceeds its review limit.')
            result[item.relative_to(path).as_posix()] = item.read_bytes()
    for name in FIXED_SITE_FILES:
        result[name] = regular(path / name).read_bytes()
    return result


def website_schema(source):
    """Conservative review gate, not a proof about arbitrary Python behavior."""
    result = {}
    for name, raw in sorted(source.items()):
        if not name.endswith('.py'):
            continue
        if name.startswith('scripts/migrate_') or name.endswith('_schema.py'):
            result[name] = hashlib.sha256(raw).hexdigest()
            continue
        tree = ast.parse(raw.decode('utf-8-sig'))
        parts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and DDL.search(node.value):
                parts.append(('sql', node.value))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any((isinstance(child, ast.Constant) and isinstance(child.value, str)
                        and DDL.search(child.value)) or
                       (isinstance(child, ast.Attribute) and child.attr == 'executescript')
                       for child in ast.walk(node)):
                    parts.append(('function', ast.dump(node, include_attributes=False)))
        if parts:
            result[name] = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
    return result


def jar_schema(path):
    """Compare owned persistence bytecode/resources, including nested module JARs."""
    result = {}

    def scan(archive, prefix):
        for item in archive.infolist():
            name = item.filename
            lowered = name.lower()
            selected = ('/dal/dataobject/' in '/' + lowered or '/dal/mysql/' in '/' + lowered
                        or lowered.endswith('.sql') or '/db/migration/' in '/' + lowered
                        or '/changelog/' in '/' + lowered
                        or lowered.endswith('.xml') and ('mapper/' in lowered or 'liquibase' in lowered))
            if item.is_dir():
                continue
            if selected:
                if item.file_size > 32 * 1024**2:
                    raise ReleaseError('A schema resource exceeds its review limit.')
                if prefix + name in result:
                    raise ReleaseError('Duplicate Java persistence resources need explicit review.')
                result[prefix + name] = hashlib.sha256(archive.read(item)).hexdigest()

    with zipfile.ZipFile(path) as archive:
        scan(archive, '')
        for item in archive.infolist():
            if item.filename.startswith('BOOT-INF/lib/yudao-module-') and item.filename.endswith('.jar'):
                if item.file_size > 128 * 1024**2:
                    raise ReleaseError('A platform module exceeds its review limit.')
                # Module versions in filenames can change without changing the persistence contract.
                module = re.sub(r'-\d[^/]*\.jar$', '.jar', item.filename.rsplit('/', 1)[-1])
                with zipfile.ZipFile(io.BytesIO(archive.read(item))) as nested:
                    scan(nested, module + '!/')
    if not result:
        raise ReleaseError('No owned persistence resources were found in a full-platform JAR.')
    return result


def component_changes(manifest, state, site_release, ui_release, recorded=None):
    ui_digest = (ui_release / 'artifact.sha256').read_text().strip()
    if recorded is not None:
        if (recorded.get('format') != 1 or
                recorded.get('commits', {}).get('website') != site_release.name or
                recorded.get('commits', {}).get('backend') != state['sourceCommit'] or
                recorded.get('artifacts', {}).get('yudao-server.jar', {}).get('sha256') != state['jar']['sha256'] or
                recorded.get('artifacts', {}).get('console.tar.gz', {}).get('sha256') != ui_digest or
                not re.fullmatch(r'[a-f0-9]{40}', recorded.get('commits', {}).get('adminUi', ''))):
            raise ReleaseError('Recorded daily release provenance differs from active files; review it before publishing.')
    return {
        'website': site_release.name != manifest['commits']['website'],
        'backend': state['sourceCommit'] != manifest['commits']['backend'],
        'adminUi': (recorded['commits']['adminUi'] != manifest['commits']['adminUi'] if recorded else
                    ui_digest != manifest['artifacts']['console.tar.gz']['sha256']),
    }


def certificate_fingerprints():
    result = {}
    for name in ('bojizhuye', 'leanschool'):
        config = regular(Path('/etc/nginx/sites-available') / (name + '.conf'))
        for raw in re.findall(r'^\s*ssl_certificate(?:_key)?\s+([^;\s]+)\s*;', config.read_text(), re.M):
            path = Path(raw)
            if not path.is_absolute() or not path.is_relative_to('/etc/nginx/ssl'):
                raise ReleaseError('TLS files must stay under the existing Nginx certificate directory.')
            result[str(path)] = digest(regular(path))
    if len(result) < 4:
        raise ReleaseError('Both domains must have existing certificate and key files.')
    return result


def preflight(directory, helper):
    assert_host()
    manifest = verify_bundle(directory, helper.fetch)
    state = helper.platform.load_state()
    if (state.get('database') != 'leanschool' or state.get('dbUser') != 'leanschool_app'
            or state.get('dbPort') != 3306 or state.get('redisPort') != 6382
            or state.get('javaXmxMiB') != 1536):
        raise ReleaseError('Production identity or sizing differs from the reviewed daily release layout.')
    ready = read_json(ETC / 'schema-ready.json', private=True)
    seed = Path(state['seedDir'])
    if ready.get('database') != state['database'] or ready.get('schemaSha256') != state['schemaSha256']:
        raise ReleaseError('The installed database requires an explicit migration review.')
    if digest(regular(seed / 'schema-manifest.json', private=True)) != state['schemaSha256']:
        raise ReleaseError('The installed private schema manifest has changed.')
    links = {
        'website': owned_link(SITE / 'current', SITE / 'releases'),
        'backend': owned_link(PLATFORM / 'java/current', PLATFORM / 'java/releases'),
        'adminUi': owned_link(PLATFORM / 'ui/current', PLATFORM / 'ui/releases'),
    }
    if links['backend'] != Path(state['javaRelease']) or links['adminUi'] != Path(state['uiRelease']):
        raise ReleaseError('Prepared and active platform versions differ; resolve the unfinished release first.')
    if digest(regular(links['backend'] / 'yudao-server.jar')) != state['jar']['sha256']:
        raise ReleaseError('The active Java artifact differs from its recorded hash.')
    recorded = read_json(CURRENT, private=True) if CURRENT.exists() else None
    changes = component_changes(manifest, state, links['website'], links['adminUi'], recorded)
    candidate_sources = website_sources(directory / 'website.tar.gz', archive=True)
    current_sources = website_sources(links['website'])
    if any(candidate_sources.get(name) != current_sources.get(name) for name in FIXED_SITE_FILES):
        raise ReleaseError('Website dependencies or service templates changed; use a separately reviewed release.')
    if website_schema(candidate_sources) != website_schema(current_sources):
        raise ReleaseError('Website schema or migration code changed; perform an explicit migration review.')
    if changes['backend'] and jar_schema(directory / 'yudao-server.jar') != jar_schema(links['backend'] / 'yudao-server.jar'):
        raise ReleaseError('Java persistence/schema resources changed; perform an explicit migration review.')
    args = platform_args(directory, state)
    artifact_report = helper.platform.artifacts(args)
    if artifact_report['schemaSha256'] != ready['schemaSha256']:
        raise ReleaseError('Candidate schema does not match the initialized production schema.')
    engine_count = int(mysql("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='leanschool' AND TABLE_TYPE='BASE TABLE' AND ENGINE<>'InnoDB';"))
    if engine_count:
        raise ReleaseError('Consistent online backup requires all platform business tables to use InnoDB.')
    db_bytes = int(mysql("SELECT COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) FROM information_schema.TABLES WHERE TABLE_SCHEMA='leanschool';"))
    required = sum(v['bytes'] for v in manifest['artifacts'].values()) * 3 + db_bytes * 2 + SITE_DB.stat().st_size * 2 + 1024**3
    if shutil.disk_usage('/var').free < required or shutil.disk_usage('/opt').free < required:
        raise ReleaseError('There is insufficient disk space for the new release and private recovery snapshots.')
    helper.site.assert_single_service()
    helper.site.service_health()
    helper.site.console_ready()
    helper.site.legacy_https_health()
    return {'manifest': manifest, 'state': state, 'links': links, 'changes': changes, 'recorded': recorded,
            'schema': database_structure(), 'certificates': certificate_fingerprints()}


def platform_args(directory, state):
    return SimpleNamespace(jar=directory / 'yudao-server.jar', manifest=directory / 'java-release.json',
        ui_archive=directory / 'console.tar.gz', ui_sha256=digest(directory / 'console.tar.gz'),
        ui_prefix='console', seed_dir=Path(state['seedDir']), java=Path(state['javaPath']),
        mysql_admin_file=None, database=state['database'], db_user=state['dbUser'],
        redis_port=state['redisPort'], java_xmx_mib=state['javaXmxMiB'])


def snapshot_configuration(backup):
    result = []
    folder = backup / 'configuration'
    folder.mkdir(mode=0o700)
    for index, path in enumerate(CONFIG_FILES):
        if path.is_symlink():
            raise ReleaseError('A managed configuration file is an unexpected symlink.')
        item = {'path': str(path), 'exists': path.exists()}
        if path.exists():
            regular(path)
            saved = folder / str(index)
            shutil.copyfile(path, saved)
            saved.chmod(0o600)
            info = path.stat()
            item.update(saved=str(saved), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        result.append(item)
    atomic_json(backup / 'configuration.json', result)
    return result


def backup_databases(backup):
    # Each store gets a consistent snapshot. There is no cross-store distributed snapshot.
    destination = backup / 'mysql.sql'
    with destination.open('xb') as output:
        destination.chmod(0o600)
        argv = ['/usr/bin/mysqldump', '--no-defaults', '--user=root', '--protocol=socket',
                '--single-transaction', '--quick', '--skip-lock-tables', '--no-tablespaces',
                '--set-gtid-purged=OFF', '--routines', '--triggers', '--events', '--hex-blob',
                '--databases', 'leanschool']
        try:
            result = subprocess.run(argv, stdout=output, stderr=subprocess.PIPE, timeout=600)
        except subprocess.TimeoutExpired:
            raise ReleaseError('The private MySQL snapshot timed out; no release was activated.') from None
    if result.returncode or destination.stat().st_size == 0:
        raise ReleaseError('The private MySQL snapshot failed; no release was activated.')
    if SITE_DB.is_symlink() or not SITE_DB.is_file():
        raise ReleaseError('The website database is missing or is an unexpected symlink.')
    target = backup / 'website.sqlite3'
    with closing(sqlite3.connect(SITE_DB.resolve().as_uri() + '?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(target)) as saved:
        target.chmod(0o600)
        deadline = time.monotonic() + 120
        def progress(*_):
            if time.monotonic() >= deadline:
                raise ReleaseError('The private SQLite snapshot timed out; no release was activated.')
        source.backup(saved, pages=256, progress=progress, sleep=0.05)
        if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or saved.execute('PRAGMA foreign_key_check').fetchone():
            raise ReleaseError('The private SQLite snapshot did not pass integrity checks.')


def prepare_website(directory, plan, helper):
    revision = plan['manifest']['commits']['website']
    expected = plan['manifest']['artifacts']['website.tar.gz']['sha256']
    archive = directory / 'website.tar.gz'
    helper.site.install.validate_archive(archive, revision)
    target = SITE / 'releases' / revision
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or digest_marker(target) != expected:
            raise ReleaseError('The candidate website release exists with different or incomplete contents.')
        trusted_directory(target)
        return target
    previous_venv = (plan['links']['website'] / '.venv').resolve(strict=True)
    if not previous_venv.is_relative_to(SITE / 'releases') or previous_venv.name != '.venv':
        raise ReleaseError('Installed website dependencies are outside the owned release directory.')
    trusted_directory(previous_venv)
    stage = target.with_name(target.name + '.staging-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o755)
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            output = stage / member.name
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with source.extractfile(member) as stream, output.open('xb') as destination:
                    shutil.copyfileobj(stream, destination)
                output.chmod(0o644)
    for folder, _, _ in os.walk(stage):
        os.chmod(folder, 0o755)
    (stage / '.venv').symlink_to(previous_venv, target_is_directory=True)
    (stage / '.deployment-sha256').write_text(expected + '\n')
    (stage / '.deployment-sha256').chmod(0o644)
    os.rename(stage, target)
    return target


def digest_marker(path):
    return regular(path / '.deployment-sha256').read_text().strip()


def prepare_ui(directory, plan, helper):
    sha = plan['manifest']['artifacts']['console.tar.gz']['sha256']
    target = PLATFORM / 'ui/releases' / sha[:16]
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or regular(target / 'artifact.sha256').read_text().strip() != sha:
            raise ReleaseError('The candidate UI release exists with different or incomplete contents.')
        trusted_directory(target)
        return target
    stage = target.with_name(target.name + '.staging-' + uuid.uuid4().hex)
    (stage / 'console').mkdir(parents=True, mode=0o755)
    archive, entries, _ = helper.platform.archive_entries(directory / 'console.tar.gz', 'console')
    try:
        for name, entry, _ in entries:
            output = stage / 'console' / name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with archive.extractfile(entry) as stream, output.open('xb') as destination:
                shutil.copyfileobj(stream, destination)
            output.chmod(0o644)
    finally:
        archive.close()
    (stage / 'artifact.sha256').write_text(sha + '\n')
    (stage / 'artifact.sha256').chmod(0o644)
    for folder, _, _ in os.walk(stage):
        os.chmod(folder, 0o755)
    os.rename(stage, target)
    return target


def platform_prepare_command(directory, plan, root):
    args = platform_args(directory, plan['state'])
    command = [sys.executable, root / 'deploy/yudao/platform-install.py', 'prepare']
    for field in ('jar', 'manifest', 'ui_archive', 'ui_sha256', 'ui_prefix', 'seed_dir', 'java',
                  'database', 'db_user', 'redis_port', 'java_xmx_mib'):
        command.extend(['--' + field.replace('_', '-'), str(getattr(args, field))])
    return command


def health(helper, certificates, schema):
    helper.site.service_health()
    helper.site.console_ready()
    helper.site.legacy_https_health()
    try:
        anonymous = helper.site.json_get('http://127.0.0.1:48080/admin-api/system/auth/get-permission-info', tenant=True)
        if anonymous.get('code') != 401:
            raise ReleaseError('An anonymous management request was not rejected.')
    except urllib.error.HTTPError as response:
        if response.code != 401:
            raise ReleaseError('Anonymous management rejection returned an unexpected status.') from None
    for host in HOSTS:
        for route in ('/', '/console/', '/api/club/health', '/app-api/leanclub/site/published'):
            result = run(['curl', '--noproxy', '*', '--silent', '--show-error', '--fail', '--max-time', '12',
                          '--resolve', host + ':443:127.0.0.1', '--output', '/dev/null', '--write-out',
                          '%{http_code}', 'https://' + host + route], timeout=15)
            if result.stdout.strip() != '200':
                raise ReleaseError('A strict local HTTPS acceptance check failed.')
        for route in ('/v3/api-docs', '/actuator/health', '/console/assets/daily-release-missing.js'):
            result = run(['curl', '--noproxy', '*', '--silent', '--show-error', '--max-time', '12',
                          '--resolve', host + ':443:127.0.0.1', '--output', '/dev/null', '--write-out',
                          '%{http_code}', 'https://' + host + route], timeout=15)
            if result.stdout.strip() != '404':
                raise ReleaseError('An internal route or nonexistent UI asset is unexpectedly exposed.')
    if certificate_fingerprints() != certificates:
        raise ReleaseError('A certificate changed unexpectedly during the release; inspect immediately.')
    if database_structure() != schema:
        raise ReleaseError('Database structure changed during the release; inspect the migration boundary.')


def restore_configuration(snapshots):
    for item in snapshots:
        path = Path(item['path'])
        if path.is_symlink():
            raise ReleaseError('A configuration destination became a symlink during deployment.')
        if item['exists']:
            temporary = path.with_name(path.name + '.rollback-' + uuid.uuid4().hex)
            shutil.copyfile(item['saved'], temporary)
            os.chmod(temporary, item['mode'])
            os.chown(temporary, item['uid'], item['gid'])
            os.replace(temporary, path)
        else:
            path.unlink(missing_ok=True)


def rollback(plan, snapshots, helper, touched):
    # Only control-plane restoration; never execute mysql/sqlite restore commands.
    if touched['website']:
        run(['systemctl', 'stop', 'bojizhuye-public-gateway', 'bojizhuye'])
    if touched['backend']:
        run(['systemctl', 'stop', 'leanschool-java'])
    restore_configuration(snapshots)
    for component, path in (('website', SITE / 'current'), ('backend', PLATFORM / 'java/current'),
                            ('adminUi', PLATFORM / 'ui/current')):
        if touched[component]:
            helper.platform.swap_link(path, plan['links'][component])
    run(['systemctl', 'daemon-reload'])
    if touched['backend']:
        run(['systemctl', 'start', 'leanschool-java'])
        wait_java(helper)
    if touched['website']:
        run(['systemctl', 'start', 'bojizhuye', 'bojizhuye-public-gateway'])
    wait_health(helper, plan)


def wait_java(helper):
    deadline = time.monotonic() + 300
    while True:
        try:
            helper.site.java_ready()
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise ReleaseError('Java did not recover within its health deadline.') from None
            time.sleep(3)


def wait_health(helper, plan):
    for attempt in range(20):
        try:
            health(helper, plan['certificates'], plan['schema'])
            return
        except Exception:
            if attempt == 19:
                raise ReleaseError('Production acceptance checks did not recover; inspect privately.') from None
            time.sleep(0.5)


def publish(directory, plan, helper):
    if not any(plan['changes'].values()):
        return {'ok': True, 'mode': 'publish', 'changed': plan['changes'], 'deployed': False,
                'reason': 'All active artifact versions already match.', 'databaseRestored': False}
    trusted_directory(BACKUPS.parent)
    if BACKUPS.is_symlink():
        raise ReleaseError('The private backup root must not be a symlink.')
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    trusted_directory(BACKUPS)
    BACKUPS.chmod(0o700)
    backup = BACKUPS / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex)
    backup.mkdir(mode=0o700)
    snapshots = snapshot_configuration(backup)
    backup_databases(backup)
    if database_structure() != plan['schema']:
        raise ReleaseError('Database structure changed during snapshots; release stopped before activation.')
    atomic_json(backup / 'before.json', {'links': {k: str(v) for k, v in plan['links'].items()},
        'changed': plan['changes'], 'runId': plan['manifest']['runId'], 'databaseRestored': False,
        'snapshotScope': 'Each database is internally consistent; no cross-store transaction snapshot.'})
    # Prepare static code before stopping any service. Dependencies are reused, never installed as root.
    site_release = prepare_website(directory, plan, helper) if plan['changes']['website'] else None
    ui_release = prepare_ui(directory, plan, helper) if plan['changes']['adminUi'] else None
    touched = {'website': False, 'backend': False, 'adminUi': False}
    try:
        if plan['changes']['backend']:
            touched['backend'] = True
            run(['systemctl', 'stop', 'leanschool-java'])
            # The existing installer budgets available RAM. Stop the old JVM first, then keep
            # its exact files/config snapshots available for rollback if preparation fails.
            run(platform_prepare_command(directory, plan, helper.root), timeout=600)
            if not plan['changes']['adminUi']:
                # A full cloud rebuild may produce new archive timestamps from the same UI commit.
                # Keep the actual reviewed UI release when its source provenance did not change.
                prepared = read_json(ETC / 'platform-state.json', private=True)
                for field in ('uiRelease', 'uiSha256', 'uiFiles', 'uiExtractedBytes'):
                    prepared[field] = plan['state'][field]
                atomic_json(ETC / 'platform-state.json', prepared)
            touched['adminUi'] = plan['changes']['adminUi']
            run([sys.executable, helper.root / 'deploy/yudao/platform-install.py', 'activate'], timeout=370)
        elif plan['changes']['adminUi']:
            touched['adminUi'] = True
            helper.platform.swap_link(PLATFORM / 'ui/current', ui_release)
            state = dict(plan['state'])
            state.update(uiRelease=str(ui_release), uiSha256=plan['manifest']['artifacts']['console.tar.gz']['sha256'])
            atomic_json(ETC / 'platform-state.json', state)
        if plan['changes']['website']:
            touched['website'] = True
            run(['systemctl', 'stop', 'bojizhuye-public-gateway', 'bojizhuye'])
            helper.site.assert_stopped(SITE_DB)
            helper.platform.swap_link(SITE / 'current', site_release)
            run(['systemctl', 'start', 'bojizhuye', 'bojizhuye-public-gateway'])
        wait_health(helper, plan)
        active_artifacts = dict(plan['manifest']['artifacts'])
        previous = plan.get('recorded')
        for component, names in (('backend', ('yudao-server.jar', 'java-release.json')),
                                 ('adminUi', ('console.tar.gz',)), ('website', ('website.tar.gz',))):
            if not plan['changes'][component] and previous:
                for name in names:
                    active_artifacts[name] = previous['artifacts'][name]
        if not plan['changes']['backend'] and not previous:
            active_artifacts['yudao-server.jar'] = {key: plan['state']['jar'][key] for key in ('sha256', 'bytes')}
            stamp = plan['links']['backend'] / 'release.json'
            active_artifacts['java-release.json'] = {'sha256': digest(stamp), 'bytes': stamp.stat().st_size}
        if not plan['changes']['website'] and not previous:
            active_artifacts['website.tar.gz'] = {'sha256': digest_marker(plan['links']['website']),
                                                'bytes': None}  # Historical archive size is not inferred.
        record = {'format': 1, 'runId': plan['manifest']['runId'], 'runAttempt': plan['manifest']['runAttempt'],
            'commits': plan['manifest']['commits'], 'artifacts': active_artifacts,
            'publishedAtUtc': datetime.now(timezone.utc).isoformat(), 'backup': str(backup),
            'changed': plan['changes'], 'databaseRestored': False, 'schemaChanged': False,
            'verification': 'Strict server-local TLS, services and read-only APIs; external access not implied.'}
        atomic_json(CURRENT, record)
        atomic_json(backup / 'result.json', {'ok': True, 'codeRollback': False, 'databaseRestored': False})
        return {'ok': True, 'mode': 'publish', 'deployed': True, 'changed': plan['changes'],
                'runId': record['runId'], 'backup': str(backup), 'databaseRestored': False,
                'verification': record['verification']}
    except BaseException:
        restored = False
        try:
            rollback(plan, snapshots, helper, touched)
            restored = True
        except BaseException:
            pass
        atomic_json(backup / 'result.json', {'ok': False, 'codeRollback': restored,
                                            'databaseRestored': False, 'needsInspection': not restored})
        if restored:
            raise ReleaseError('Publish failed; previous code/configuration recovered. Business databases were not restored.',
                               'PUBLISH_FAILED_ROLLED_BACK') from None
        raise ReleaseError('Publish failed and recovery needs inspection. Business databases were not restored.',
                           'ROLLBACK_FAILED') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('plan', 'publish'), default='plan')
    parser.add_argument('--release-dir', required=True, type=Path)
    parser.add_argument('--tools-root', required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if os.name != 'posix' or sys.platform != 'linux' or os.geteuid() != 0:
        raise ReleaseError('Daily deployment requires root on the registered Linux host.')
    def interrupted(_signal, _frame):
        raise ReleaseError('Deployment interruption received; applying the current recovery boundary.')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with deployment_lock():
        helper = modules(args.tools_root)
        plan = preflight(args.release_dir, helper)
        if args.mode == 'plan':
            result = {'ok': True, 'mode': 'plan', 'runId': plan['manifest']['runId'],
                      'commits': plan['manifest']['commits'], 'changed': plan['changes'],
                      'deployed': False, 'schemaCompatible': True, 'databaseRestored': False}
        else:
            result = publish(args.release_dir, plan, helper)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except ReleaseError as error:
        print(json.dumps({'ok': False, 'error': str(error), 'errorCode': error.code,
                          'databaseRestored': False}), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print(json.dumps({'ok': False, 'error': 'Deployment validation failed; details withheld.',
                          'errorCode': 'INTERNAL_ERROR',
                          'databaseRestored': False}), file=sys.stderr)
        sys.exit(1)
