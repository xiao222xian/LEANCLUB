#!/usr/bin/env python3
"""Upgrade only the existing HTTPS website and its public Java API gateway.

Requires pre-provisioned Java and /console artifacts. Does not install Java,
MySQL, certificates, issue notifications, or restore business data on failure.
"""
import argparse
from contextlib import closing
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import uuid


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load_module('site_install_helpers', Path(__file__).with_name('install.py'))
BASE, STATE, ENV, UNIT, CONF = install.BASE, install.STATE, install.ENV, install.UNIT, install.CONF
GATEWAY_ENV = Path('/etc/bojizhuye/public-gateway.env')
GATEWAY_UNIT = Path('/etc/systemd/system/bojizhuye-public-gateway.service')
CONSOLE = Path('/opt/leanschool/ui/current/console')
BACKUPS = Path('/var/backups/bojizhuye-fullstack')
ORIGIN = 'https://www.leanschool.net'
COMPAT_ORIGIN = 'https://boji.tensorbot.xyz'
HOSTS = ('www.leanschool.net', 'boji.tensorbot.xyz')
SERVICE = 'bojizhuye.service'
GATEWAY = 'bojizhuye-public-gateway.service'
BEGIN = '    # BEGIN LEAN SCHOOL full-stack compatibility\n'
END = '    # END LEAN SCHOOL full-stack compatibility\n'
REQUIRED = {
    'backend/campaign_hosts.py', 'backend/public_app_gateway.py',
    'backend/campaign_comments.py', 'backend/member_orders_schema.py',
    'backend/payment_schema.py', 'scripts/migrate_member_orders.py',
    'scripts/migrate_payments.py', 'deploy/ubuntu/public-gateway.py',
    'deploy/ubuntu/bojizhuye-public-gateway.service.in',
    'deploy/ubuntu/bojizhuye-public-gateway.env.in',
    'web/creator/events/index.html', 'web/creator/ticket/index.html',
}


def command(*args, check=True):
    """Never echo subprocess output: migration/runtime errors can contain data."""
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError('A required command failed; inspect the service locally (output withheld).')
    return result


def active(unit):
    return command('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0


def enabled(unit):
    return command('systemctl', 'is-enabled', '--quiet', unit, check=False).returncode == 0


def environment(text):
    """Parse ordinary systemd EnvironmentFile entries; never execute a shell."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=(.*)', line)
        if not match or match[1] in values:
            raise ValueError('Invalid or duplicate environment entry (value withheld).')
        key, value = match.groups()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0] or '\\' in value:
                raise ValueError('Unsupported quoted environment entry (value withheld).')
            value = value[1:-1]
        if '\x00' in value or value.endswith('\\'):
            raise ValueError('Unsupported multiline environment entry (value withheld).')
        values[key] = value
    return values


def merge_environment(old, updates):
    environment(old)  # Reject ambiguous duplicate settings instead of shadowing them.
    result, pending = [], dict(updates)
    for line in old.splitlines():
        key = line.strip().split('=', 1)[0]
        result.append(key + '=' + pending.pop(key) if key in pending else line)
    result.extend(key + '=' + value for key, value in pending.items())
    return '\n'.join(result) + '\n'


def site_environment(old, bridge):
    values, runtime = environment(old), environment(bridge)
    expected = {'CLUB_DB': str(STATE / 'clubs.sqlite3'), 'CLUB_MEDIA_DIR': str(STATE / 'media'),
                'CLUB_PORT': '5381', 'CLUB_ALLOW_LOCAL_SETUP': '0',
                'CLUB_TRUSTED_PROXY_IPS': '127.0.0.1'}
    if any(values.get(key) != value for key, value in expected.items()):
        raise ValueError('Existing website environment differs from the reviewed production layout.')
    if values.get('CLUB_ORIGIN') not in (COMPAT_ORIGIN, ORIGIN):
        raise ValueError('Unexpected previous website origin.')
    if values.get('CLUB_COMPAT_ORIGINS', '') not in ('', COMPAT_ORIGIN):
        raise ValueError('Unexpected previous compatibility origins.')
    if runtime.get('LS_PUBLIC_TENANT_ID', '1') != '1':
        raise ValueError('The reviewed deployment requires tenant 1.')
    secrets = [runtime.get(key, '') for key in ('LS_OPERATIONS_SECRET', 'LS_MEMBER_ORDERS_SECRET')]
    if any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in secrets) or secrets[0] == secrets[1]:
        raise ValueError('Bridge credentials must be two independent 64-character lowercase hex values.')
    return merge_environment(old, {
        'CLUB_ORIGIN': ORIGIN, 'CLUB_COMPAT_ORIGINS': COMPAT_ORIGIN,
        'LEANCLUB_OPERATIONS_SECRET': secrets[0], 'LEANCLUB_MEMBER_ORDERS_SECRET': secrets[1],
        'LEANCLUB_OPERATIONS_TENANT': '1', 'LEANCLUB_MEMBER_ORDERS_TENANT': '1',
    })


def server_blocks(text):
    """Locate top-level server blocks without treating comments/quoted braces as syntax."""
    tokens = list(re.finditer(r'#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{};]|[^\s{};#"\']+', text))
    depth, start, previous = 0, None, ''
    result = []
    for token in tokens:
        value = token[0]
        if value.startswith('#'):
            continue
        if value == '{':
            if depth == 0 and previous == 'server':
                start = token.start()
            depth += 1
        elif value == '}':
            depth -= 1
            if depth < 0:
                raise ValueError('Unbalanced Nginx configuration.')
            if depth == 0 and start is not None:
                result.append((start, token.end()))
                start = None
        previous = value
    if depth:
        raise ValueError('Unbalanced Nginx configuration.')
    return result


def patch_legacy_nginx(text):
    """Add only reviewed routes to the existing old-domain TLS block."""
    if not text.startswith(('# Managed by bojizhuye HTTPS configuration.', install.MARKER)):
        raise ValueError('Legacy HTTPS Nginx file is not a recognized managed configuration.')
    matches = []
    for start, end in server_blocks(text):
        body = re.sub(r'#[^\n]*', '', text[start:end])
        if (re.search(r'\bserver_name\s+boji\.tensorbot\.xyz\s*;', body)
                and re.search(r'\blisten\s+443\s+ssl\s*;', body)):
            matches.append((start, end))
    if len(matches) != 1:
        raise ValueError('Expected exactly one old-domain HTTPS server block.')
    start, end = matches[0]
    block = text[start:end]
    if not all(re.search(r'\b' + field + r'\s+[^;]+;', block)
               for field in ('ssl_certificate', 'ssl_certificate_key')):
        raise ValueError('Old-domain certificate directives must already exist.')
    if BEGIN in block or END in block:
        if block.count(BEGIN) != 1 or block.count(END) != 1:
            raise ValueError('Ambiguous managed compatibility block.')
        before, rest = block.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        block = before + after
    if re.search(r'\blocation\s+(?:[=~^*]+\s+)?/(?:app-api|admin-api|console)(?:[ /;{]|$)', block):
        raise ValueError('Legacy public/admin/console routes exist outside the managed block; inspect first.')
    common = ('        proxy_set_header Host $http_host;\n'
              '        proxy_set_header X-Real-IP $remote_addr;\n'
              '        proxy_set_header X-Forwarded-For $remote_addr;\n'
              '        proxy_set_header X-Forwarded-Proto $scheme;\n'
              '        proxy_connect_timeout 5s;\n'
              '        proxy_read_timeout 120s;\n'
              '        proxy_send_timeout 25s;\n'
              '        proxy_cache off;\n'
              '        access_log off;\n')
    addition = BEGIN
    for prefix, port, limit in (('app-api', 5382, '32k'), ('admin-api', 48080, '16m')):
        addition += ('    location ^~ /' + prefix + '/ {\n'
                     '        client_max_body_size ' + limit + ';\n'
                     '        proxy_pass http://127.0.0.1:' + str(port) + ';\n' + common + '    }\n')
    addition += ('    location = /console { return 302 /console/; }\n'
                 '    location ^~ /console/ {\n'
                 '        root /opt/leanschool/ui/current;\n'
                 '        add_header Cache-Control no-cache;\n'
                 '        add_header X-Content-Type-Options nosniff always;\n'
                 '        add_header Referrer-Policy strict-origin-when-cross-origin always;\n'
                 '        location ~ /\\. { return 404; }\n'
                 '        location /console/assets/ { try_files $uri =404; }\n'
                 '        location /console/brand/ { try_files $uri =404; }\n'
                 '        location ~* \\.(?:html|css|js|mjs|map|json|png|jpe?g|webp|gif|svg|ico|avif|woff2?|ttf|otf|eot|txt|xml|pdf|wasm)$ {\n'
                 '            try_files $uri =404;\n'
                 '        }\n'
                 '        try_files $uri $uri/ /console/index.html;\n'
                 '    }\n')
    # Older 4499b01 does not have the newer event HTML routes.
    for route, target in (('events', '/events/index.html'), ('ticket', '/ticket/index.html'),
                          ('check-in', '/console/leanclub/registrations')):
        for suffix in ('', '/'):
            path = '/' + route + suffix
            if not re.search(r'\blocation\s+=\s+' + re.escape(path) + r'\s*\{', block):
                action = ('return 302 ' if route == 'check-in' else 'try_files ') + target
                addition += '    location = ' + path + ' { ' + action + ('' if route == 'check-in' else ' =404') + '; }\n'
    addition += END
    return text[:start] + block[:-1] + addition + '}' + text[end:]


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.assets.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') in ('stylesheet', 'modulepreload') and attrs.get('href'):
            self.assets.append(attrs['href'])


def console_ready(console=CONSOLE):
    index = console / 'index.html'
    if not index.is_file() or index.stat().st_size < 100:
        raise ValueError('The production console/index.html is missing or empty.')
    parser = AssetParser()
    parser.feed(index.read_text(encoding='utf-8'))
    if not any(urllib.parse.urlsplit(asset).path.endswith('.js') for asset in parser.assets):
        raise ValueError('Console HTML must reference built JavaScript under /console/.')
    root = console.resolve(strict=True)
    for asset in parser.assets:
        parsed = urllib.parse.urlsplit(asset)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith('/console/') or '%' in parsed.path:
            raise ValueError('Console assets must use the production /console/ base.')
        path = (root / parsed.path.removeprefix('/console/')).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
            raise ValueError('A referenced console asset is missing or outside the console release.')


def json_get(url, host=None, tenant=False):
    headers = {'Host': host} if host else {}
    if tenant:
        headers['tenant-id'] = '1'
    request = urllib.request.Request(url, headers=headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=8) as response:
        if response.status != 200 or 'json' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('A required public endpoint did not return JSON 200.')
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('Readiness response exceeded its limit.')
        return json.loads(data)


def java_ready(base='http://127.0.0.1:48080', host=None, direct=True):
    site = json_get(base + '/app-api/leanclub/site/published', host, direct)
    schools = json_get(base + '/app-api/leanclub/school/list?pageNo=1&pageSize=1', host, direct)
    if not isinstance(site, dict) or site.get('code') != 0:
        raise ValueError('Java public site API is not ready.')
    data = schools.get('data', {}) if isinstance(schools, dict) else {}
    if (not isinstance(schools, dict) or schools.get('code') != 0 or not isinstance(data, dict)
            or not isinstance(data.get('list'), list) or not data['list']
            or not isinstance(data.get('total'), int) or data['total'] < 1
            or not all(isinstance(row, dict) and row.get('id') and row.get('name') for row in data['list'])):
        raise ValueError('Java public school directory must be populated and queryable before website downtime.')


def private_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected an existing regular private configuration file.')
    if os.name == 'posix' and (path.stat().st_uid != 0 or path.stat().st_mode & 0o077):
        raise ValueError('Private environment files must be owned by root with mode 0600 or stricter.')


def backup_database(database, target):
    if not database.is_file() or database.is_symlink() or target.exists():
        raise ValueError('Refusing missing/symlink source or an existing SQLite backup.')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite backup integrity verification failed.')
        if destination.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('SQLite backup has foreign key violations; inspect before migration.')
    target.chmod(0o600)


def assert_single_service():
    if not active(SERVICE):
        raise ValueError('The existing website service must be active before this upgrade.')
    pid = command('systemctl', 'show', SERVICE, '--property=MainPID', '--value').stdout.strip()
    if not pid.isdigit() or int(pid) <= 1:
        raise ValueError('Cannot identify the existing website service process.')
    listeners = command('ss', '-H', '-ltnp', '( sport = :5381 )').stdout
    if set(re.findall(r'pid=(\d+)', listeners)) != {pid} or '127.0.0.1:5381' not in listeners:
        raise ValueError('The website listener must be owned only by its existing systemd service.')


def assert_stopped(database):
    if active(SERVICE):
        raise ValueError('Website service/worker must be stopped before backup and migration.')
    with socket.socket() as probe:
        # A closed HTTP connection can remain in TIME_WAIT after the listener
        # exits. Match HTTPServer's reuse policy while still rejecting a live listener.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', 5381))
    targets = {str(database), str(database) + '-wal', str(database) + '-shm'}
    # Fail if another live process still has the business SQLite files open.
    for process in Path('/proc').glob('[0-9]*'):
        for fd in (process / 'fd').glob('*'):
            try:
                if os.readlink(fd).removesuffix(' (deleted)') in targets:
                    raise ValueError('Another process still has the website database open; inspect before migration.')
            except (FileNotFoundError, ProcessLookupError):
                continue


def migrate_database(release, database, backup, uid, gid):
    """Called only while the worker is stopped; never import server.py/start workers."""
    python = release / '.venv/bin/python'
    initializer = ('import sys; sys.path.insert(0, sys.argv[1]); '
                   'from campaign import CampaignStore; '
                   'CampaignStore(sys.argv[2], sys.argv[3])')
    try:
        command(python, '-c', initializer, release / 'backend', database, STATE / 'media')
        for script in ('migrate_member_orders.py', 'migrate_payments.py'):
            result = command(python, release / 'scripts' / script, '--db', database,
                             '--backup-dir', backup, '--apply', '--confirm-service-stopped')
            report = json.loads(result.stdout)
            if report.get('ok') is not True or report.get('state') != 'current':
                raise RuntimeError('An explicit database migration did not finish successfully.')
        with closing(sqlite3.connect(database)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Post-migration database verification failed.')
    finally:
        for path in (database, Path(str(database) + '-wal'), Path(str(database) + '-shm')):
            if path.exists():
                os.chown(path, uid, gid)
                path.chmod(0o600)
        for path in backup.glob('*.sqlite3'):
            path.chmod(0o600)


def service_health():
    for host in HOSTS:
        if json_get('http://127.0.0.1:5381/api/club/health', host).get('status') != 'ok':
            raise ValueError('Website health failed for a configured host.')
        session = json_get('http://127.0.0.1:5381/api/admin/session', host)
        if session.get('setupAvailable') is not False or session.get('setupRequired') is not False:
            raise ValueError('Existing administrator must remain configured with public setup disabled.')
        if json_get('http://127.0.0.1:5382/healthz', host).get('status') != 'ok':
            raise ValueError('Public gateway health failed for a configured host.')
        java_ready('http://127.0.0.1:5382', host, direct=False)


def legacy_https_health():
    # TLS verification and SNI are kept enabled; DNS is pinned only for this local check.
    for route in ('/', '/city-vote', '/admin', '/console/', '/api/club/health',
                  '/app-api/leanclub/school/list?pageNo=1&pageSize=1'):
        result = command('curl', '--noproxy', '*', '--silent', '--show-error', '--fail',
                         '--max-time', '12', '--resolve', 'boji.tensorbot.xyz:443:127.0.0.1',
                         '--output', '/dev/null', '--write-out', '%{http_code}', COMPAT_ORIGIN + route)
        if result.stdout.strip() != '200':
            raise ValueError('Old-domain HTTPS route failed after the upgrade.')


def save_snapshots(paths, backup):
    result = {}
    for path in paths:
        if path.is_symlink():
            raise ValueError('Managed configuration may not be a symlink.')
        saved = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
        result[path] = saved
        if saved:
            install.atomic_write(backup / path.name, saved[0], 0o600)
    return result


def restore_configuration(snapshots, previous_link, gateway_active, gateway_enabled):
    """Best-effort control-plane rollback, explicitly never copying a database."""
    errors = []
    for unit in (GATEWAY, SERVICE):
        if unit == GATEWAY and not GATEWAY_UNIT.exists() and not gateway_active:
            continue
        if command('systemctl', 'stop', unit, check=False).returncode:
            errors.append('stop ' + unit)
    for path, saved in snapshots.items():
        try:
            if saved:
                install.atomic_write(path, saved[0], saved[1])
            else:
                path.unlink(missing_ok=True)
        except OSError:
            errors.append('restore configuration')
    try:
        install.set_current(previous_link)
    except (OSError, ValueError):
        errors.append('restore current symlink')
    if command('systemctl', 'daemon-reload', check=False).returncode:
        errors.append('daemon-reload')
    # Never restart under a partially restored mixture of code and credentials.
    # Leave stopped services stopped and the currently loaded Nginx untouched.
    if errors:
        return errors
    if command('systemctl', 'restart', SERVICE, check=False).returncode:
        errors.append('restart previous website')
    if gateway_active and command('systemctl', 'restart', GATEWAY, check=False).returncode:
        errors.append('restart previous gateway')
    if not gateway_enabled:
        command('systemctl', 'disable', GATEWAY, check=False)
    if command('nginx', '-t', check=False).returncode == 0:
        if command('systemctl', 'reload', 'nginx', check=False).returncode:
            errors.append('reload previous Nginx')
    else:
        errors.append('previous Nginx validation')
    return errors


def apply_upgrade(release, new_env, nginx, backup, account):
    # A failed dependency preflight must not restart or stop the current site.
    java_ready()
    console_ready()
    assert_single_service()
    snapshots = save_snapshots((ENV, UNIT, CONF, GATEWAY_ENV, GATEWAY_UNIT), backup)
    previous_link = os.readlink(BASE / 'current')
    gateway_active, gateway_enabled = active(GATEWAY), enabled(GATEWAY)
    install.atomic_write(backup / 'previous.json', json.dumps({
        'current': previous_link, 'gatewayActive': gateway_active, 'gatewayEnabled': gateway_enabled,
        'newRelease': release.name, 'databaseRestoredAutomatically': False,
    }, indent=2).encode(), 0o600)
    try:
        command('systemctl', 'stop', SERVICE)
        if gateway_active:
            command('systemctl', 'stop', GATEWAY)
        database = STATE / 'clubs.sqlite3'
        assert_stopped(database)
        backup_database(database, backup / 'before.sqlite3')
        assert_stopped(database)
        migrate_database(release, database, backup, account.pw_uid, account.pw_gid)
        install.atomic_write(ENV, new_env.encode(), 0o600)
        install.atomic_write(CONF, nginx.encode(), 0o644)
        for path, template, mode in (
                (GATEWAY_ENV, 'bojizhuye-public-gateway.env.in', 0o600),
                (GATEWAY_UNIT, 'bojizhuye-public-gateway.service.in', 0o644)):
            install.atomic_write(path, (release / 'deploy/ubuntu' / template).read_bytes(), mode)
        command('nginx', '-t')
        install.set_current(release)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'start', SERVICE)
        command('systemctl', 'start', GATEWAY)
        for attempt in range(20):
            try:
                service_health()
                break
            except Exception:
                if attempt == 19:
                    raise
                time.sleep(0.5)
        command('systemctl', 'reload', 'nginx')
        legacy_https_health()
        command('systemctl', 'enable', GATEWAY)
    except BaseException:
        errors = restore_configuration(snapshots, previous_link, gateway_active, gateway_enabled)
        print('Upgrade failed. Previous code/configuration restore attempted; business database was NOT restored.', file=sys.stderr)
        if errors:
            print('Rollback needs inspection: ' + ', '.join(errors), file=sys.stderr)
        print('Private recovery directory: ' + str(backup), file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--bridge-env', type=Path, required=True,
                        help='Explicit root-private Java EnvironmentFile; only signing secrets/tenant are read.')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0 or sys.version_info < (3, 10):
        raise ValueError('Run as root on Ubuntu with Python 3.10 or newer.')
    os.umask(0o077)
    import fcntl
    with open('/run/lock/bojizhuye-fullstack.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for program in ('nginx', 'systemctl', 'ss', 'curl'):
            if not shutil.which(program):
                raise ValueError('Install the prerequisite before upgrading: ' + program)
        if not re.fullmatch(r'[a-f0-9]{40}', args.revision) or not re.fullmatch(r'[a-fA-F0-9]{64}', args.sha256):
            raise ValueError('Full Git commit and SHA-256 are required.')
        args.archive = args.archive.resolve(strict=True)
        digest = install.archive_digest(args.archive)
        if digest != args.sha256.lower():
            raise ValueError('Archive SHA-256 mismatch; no changes made.')
        install.validate_archive(args.archive, args.revision)
        with tarfile.open(args.archive, 'r:gz') as archive:
            if not REQUIRED <= set(archive.getnames()):
                raise ValueError('Archive lacks required full-stack migration/gateway files.')
        for path in (ENV, args.bridge_env):
            private_file(path)
        for path in (BASE, BASE / 'releases', STATE, STATE / 'media', CONF, UNIT):
            if path.is_symlink() or not path.exists():
                raise ValueError('Expected an existing non-symlink production path.')
        current = BASE / 'current'
        if not current.is_symlink() or not current.resolve(strict=True).is_relative_to(BASE / 'releases'):
            raise ValueError('Current must point to an existing release under /opt/bojizhuye/releases.')
        if not install.ENABLED.is_symlink() or install.ENABLED.resolve() != CONF:
            raise ValueError('Existing legacy HTTPS site must remain enabled at its managed path.')
        unit = UNIT.read_text(encoding='utf-8')
        if not unit.startswith(install.MARKER) or 'User=bojizhuye\n' not in unit or (
                'ExecStart=/opt/bojizhuye/current/.venv/bin/python /opt/bojizhuye/current/backend/server.py\n') not in unit:
            raise ValueError('Existing website unit is outside the reviewed service layout.')
        for path, marker in ((GATEWAY_ENV, '# Public routing configuration only.'),
                             (GATEWAY_UNIT, '# Managed by bojizhuye')):
            if path.is_symlink() or (path.exists() and not path.read_text().startswith(marker)):
                raise ValueError('Gateway configuration already exists outside this project.')
        account = install.pwd.getpwnam('bojizhuye')
        if account.pw_uid == 0 or account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'):
            raise ValueError('Expected the existing dedicated non-login service account.')
        new_env = site_environment(ENV.read_text(encoding='utf-8'), args.bridge_env.read_text(encoding='utf-8-sig'))
        nginx = patch_legacy_nginx(CONF.read_text(encoding='utf-8'))
        if not active('nginx'):
            raise ValueError('Existing Nginx must be active.')
        command('nginx', '-t')
        java_ready()
        console_ready()
        assert_single_service()
        # Source and venv are public code; retain traversable 0755 directories.
        # Restore the private mask before creating any backup/configuration.
        private_mask = os.umask(0o022)
        try:
            release = install.prepare_release(args.archive, args.revision, digest)
        finally:
            os.umask(private_mask)
        command(release / '.venv/bin/python', '-c', 'import PIL, qrcode, sqlite3')
        if BACKUPS.is_symlink():
            raise ValueError('Backup directory must not be a symlink.')
        BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        BACKUPS.chmod(0o700)
        backup = BACKUPS / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex)
        backup.mkdir(mode=0o700)
        apply_upgrade(release, new_env, nginx, backup, account)
        print('Website/gateway upgraded to ' + args.revision + '. Both configured Host routes and old-domain TLS passed.')
        print('New-domain certificate/vhost verification is a separate deployment step.')
        print('Private SQLite/config backup retained at ' + str(backup) + '; never include it in a release archive.')


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Do not interpolate raw exceptions: subprocess/SQLite/config values may be sensitive.
        print('ERROR: website upgrade did not complete. Inspect private recovery files and service status locally.', file=sys.stderr)
        sys.exit(1)
