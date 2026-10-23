#!/usr/bin/env python3
"""Deploy a verified local Git archive; never clone, fetch, or import runtime data."""
import argparse
from contextlib import closing
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
try:
    import pwd
except ImportError:  # Rendering/validation also runs on the Windows development host.
    pwd = None
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import urllib.error

COMMIT = 'cb66b6a4d6417d3f047cb7ec636fa4f7aea2c2fb'
BASE = Path('/opt/bojizhuye')
STATE = Path('/var/lib/bojizhuye')
UNIT = Path('/etc/systemd/system/bojizhuye.service')
ENV = Path('/etc/bojizhuye/bojizhuye.env')
CONF = Path('/etc/nginx/sites-available/bojizhuye.conf')
ENABLED = Path('/etc/nginx/sites-enabled/bojizhuye.conf')
MARKER = '# Managed by bojizhuye deploy/ubuntu.'
REQUIRED = {'backend/server.py', 'backend/campaign.py', 'backend/campaign_http.py',
            'requirements.txt', 'web/creator/index.html',
            'web/creator/city-vote/index.html', 'web/creator/admin/index.html'}


def run(*args, capture=False, check=True):
    return subprocess.run(args, check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def active(unit):
    return run('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0


def archive_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_archive(archive, expected_commit=COMMIT):
    """Reject links, traversal and runtime/credential files before any extraction."""
    seen = set()
    total = 0
    with tarfile.open(archive, 'r:gz') as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or not path.parts or '\\' in member.name
                    or '..' in path.parts or ':' in member.name
                    or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe archive member: ' + member.name)
            if member.name in seen:
                raise ValueError('Duplicate archive member: ' + member.name)
            seen.add(member.name)
            if (any(p in ('.git', 'data', '.venv', 'node_modules') for p in path.parts)
                    or path.name.startswith('.env') and path.name != '.env.example'
                    or path.suffix.lower() in ('.sqlite3', '.db', '.pem', '.key', '.p12', '.pfx')):
                raise ValueError('Runtime or credential file is not allowed: ' + member.name)
            total += member.size
            if total > 1024 * 1024 * 1024:
                raise ValueError('Archive exceeds the 1 GiB extracted size limit.')
        if not REQUIRED <= seen:
            raise ValueError('Archive is missing required application files.')
        recorded_commit = source.pax_headers.get('comment', '').strip()
        if recorded_commit != expected_commit:
            raise ValueError('Archive Git commit does not match the pinned release.')


def validate_host(value):
    """Accept canonical IPv4 or an ASCII FQDN; never accept ports or Nginx syntax."""
    if not isinstance(value, str):
        raise ValueError('Host must be a fully qualified domain name or IPv4 address.')
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        pass
    host = value.lower()
    labels = host.split('.')
    if (len(host) > 253 or len(labels) < 2
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                       for label in labels)
            or not re.search('[a-z]', labels[-1])):
        raise ValueError('Host must be a fully qualified ASCII domain name or canonical IPv4 address.')
    return host


def origin_for(host, port):
    return 'http://' + host + (':' + str(port) if port != 80 else '')


def render(host, port, redirect_ip=None):
    host = validate_host(host)
    if not 1 <= port <= 65535 or port == 5381:
        raise ValueError('HTTP port must be 1–65535 and cannot be the API port 5381.')
    origin = origin_for(host, port)
    redirect_block = ''
    if redirect_ip is not None:
        redirect_ip = str(ipaddress.IPv4Address(redirect_ip))
        if host == redirect_ip or re.fullmatch(r'[0-9.]+', host):
            raise ValueError('An IP redirect requires a canonical domain name as --host.')
        redirect_block = ('\n# Redirect only the explicit legacy IPv4 host, preserving path/query.\n'
                          'server {\n    listen 80;\n    server_name ' + redirect_ip + ';\n'
                          '    return 301 ' + origin + '$request_uri;\n}\n')
    values = {'@@HOST@@': host, '@@HTTP_PORT@@': str(port), '@@ORIGIN@@': origin,
              '@@IP_REDIRECT_BLOCK@@': redirect_block}
    result = {}
    for name in ('bojizhuye.service', 'bojizhuye.env', 'bojizhuye.nginx.conf'):
        text = Path(__file__).with_name(name + '.in').read_text(encoding='utf-8')
        for key, value in values.items():
            text = text.replace(key, value)
        if '@@' in text:
            raise ValueError('Unresolved template placeholder.')
        result[name] = text
    return result


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def set_current(target):
    link = BASE / ('.current-' + str(os.getpid()))
    if link.exists() or link.is_symlink():
        raise ValueError('Temporary current symlink already exists.')
    link.symlink_to(target)
    os.replace(link, BASE / 'current')


def http_check(url, host=None, json_result=False):
    headers = {'Host': host} if host else {}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=4) as response:
        if response.status != 200:
            raise RuntimeError('Unexpected HTTP status for ' + url)
        return json.load(response) if json_result else response.read(1)


def http_redirect_check(ip, origin):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, new_url):
            return None
    path = '/city-vote?from=ip-check'
    request = urllib.request.Request('http://127.0.0.1' + path, headers={'Host': ip})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=4):
            raise RuntimeError('IP host did not redirect to the canonical domain.')
    except urllib.error.HTTPError as response:
        with response:
            if response.code != 301 or response.headers.get('Location') != origin + path:
                raise RuntimeError('IP redirect status or canonical location is incorrect.')


def check_owned(path):
    if path.is_symlink():
        raise ValueError('Refusing to replace a symlink configuration: ' + str(path))
    if path.exists() and not path.read_text(encoding='utf-8').startswith(MARKER):
        raise ValueError('Existing configuration is not managed by this project: ' + str(path))


def prepare_release(archive, revision, digest):
    """Extract a previously validated archive and prepare its immutable venv.

    Call validate_archive and verify SHA-256 before calling this helper.
    """
    release = BASE / 'releases' / revision
    marker = release / '.deployment-sha256'
    if release.is_symlink():
        raise ValueError('Pinned release directory must not be a symlink.')
    if release.exists():
        if not marker.is_file() or marker.read_text().strip() != digest:
            raise ValueError('Existing release is incomplete or differs; inspect it, do not overwrite it.')
    else:
        release.mkdir(parents=True, mode=0o755)
        # All members were validated above; avoid adopting archive ownership/modes.
        with tarfile.open(archive, 'r:gz') as source:
            for member in source.getmembers():
                destination = release / member.name
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True, mode=0o755)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with source.extractfile(member) as src, destination.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    destination.chmod(0o644)
        run(sys.executable, '-m', 'venv', str(release / '.venv'))
        run(str(release / '.venv/bin/python'), '-m', 'pip', 'install', '--disable-pip-version-check',
            '-r', str(release / 'requirements.txt'))
        marker.write_text(digest + '\n')
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--revision', default=COMMIT, help='Full pinned Git commit for this release.')
    parser.add_argument('--host', default='www.leanschool.net', help='Canonical ASCII FQDN or IPv4 address.')
    parser.add_argument('--http-port', type=int, default=80)
    parser.add_argument('--redirect-ip', help='Optional IPv4 host on port 80 to redirect to the canonical domain.')
    parser.add_argument('--render-only', type=Path, help='Render templates locally, without server changes.')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.revision):
        parser.error('--revision must be a full lowercase Git commit SHA.')
    args.host = validate_host(args.host)
    rendered = render(args.host, args.http_port, args.redirect_ip)
    if args.render_only:
        args.render_only.mkdir(parents=True, exist_ok=True)
        for name, contents in rendered.items():
            (args.render_only / name).write_text(contents, encoding='utf-8', newline='\n')
        print('Templates rendered. No services or system configuration changed.')
        return
    if not args.archive or not args.sha256 or not re.fullmatch('[a-fA-F0-9]{64}', args.sha256):
        parser.error('--archive and its independently verified --sha256 are required.')
    if sys.version_info < (3, 10) or os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('Run as root on Ubuntu with Python 3.10 or newer.')
    for program in ('nginx', 'systemctl', 'useradd', 'getent'):
        if not shutil.which(program):
            raise ValueError('Install the prerequisite first: ' + program)
    args.archive = args.archive.resolve(strict=True)
    digest = archive_digest(args.archive)
    if digest != args.sha256.lower():
        raise ValueError('Archive SHA-256 mismatch; nothing deployed.')
    validate_archive(args.archive, args.revision)
    run(sys.executable, '-c', 'import venv, ensurepip')
    if not active('nginx'):
        raise ValueError('Nginx must already be running; review existing sites before deployment.')
    for path in (UNIT, ENV, CONF):
        check_owned(path)
    if ENABLED.exists() or ENABLED.is_symlink():
        if not ENABLED.is_symlink() or ENABLED.resolve() != CONF:
            raise ValueError('Existing enabled site is not the project symlink.')
    if BASE.is_symlink() or (BASE / 'releases').is_symlink():
        raise ValueError('Application and release directories must not be symlinks.')
    current = BASE / 'current'
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or not current.resolve().is_relative_to(BASE / 'releases'):
            raise ValueError('Existing current path is not a managed release symlink.')
    dump = run('nginx', '-T', capture=True)
    # Inspect all currently included configurations, excluding only our own file.
    sections = re.split(r'^# configuration file (.+):\s*$', dump.stdout, flags=re.M)
    outside = sections[0]
    for i in range(1, len(sections), 2):
        if Path(sections[i]).resolve() != CONF:
            outside += sections[i + 1]
    outside = re.sub(r'#[^\n]*', '', outside)
    requested_names = {args.host}
    if args.redirect_ip:
        requested_names.add(args.redirect_ip)
    for names in re.findall(r'\bserver_name\s+([^;]+);', outside):
        existing_names = {name.strip('\'"').lower() for name in names.split()}
        if requested_names & existing_names:
            raise ValueError('Another Nginx site already owns a requested domain/IP server_name; inspect it first.')
    was_active = active('bojizhuye.service')
    was_enabled = run('systemctl', 'is-enabled', '--quiet', 'bojizhuye.service', check=False).returncode == 0
    if not was_active:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 5381))
    try:
        account = pwd.getpwnam('bojizhuye')
        if account.pw_uid == 0 or account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'):
            raise ValueError('Existing bojizhuye account is not a dedicated service account.')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', str(STATE),
            '--no-create-home', '--shell', '/usr/sbin/nologin', 'bojizhuye')
        account = pwd.getpwnam('bojizhuye')
    for path in (STATE, STATE / 'media'):
        if path.is_symlink():
            raise ValueError('State directory must not be a symlink: ' + str(path))
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, account.pw_uid, account.pw_gid)
        os.chmod(path, 0o700)
    release = prepare_release(args.archive, args.revision, digest)
    run(str(release / '.venv/bin/python'), '-c', 'from PIL import Image; import sqlite3')
    snapshots = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                 for path in (UNIT, ENV, CONF)}
    previous_link = os.readlink(current) if current.is_symlink() else None
    previous_enabled_link = ENABLED.is_symlink()
    backup = Path('/var/backups/bojizhuye-deploy') / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    for path, saved in snapshots.items():
        if saved:
            atomic_write(backup / path.name, saved[0], 0o600)
    atomic_write(backup / 'previous.json', json.dumps({
        'current': previous_link, 'service_active': was_active, 'service_enabled': was_enabled,
        'site_enabled': previous_enabled_link, 'commit': args.revision, 'archive_sha256': digest
    }, indent=2).encode(), 0o600)
    try:
        atomic_write(UNIT, rendered['bojizhuye.service'].encode(), 0o644)
        atomic_write(ENV, rendered['bojizhuye.env'].encode(), 0o600)
        atomic_write(CONF, rendered['bojizhuye.nginx.conf'].encode(), 0o644)
        if not previous_enabled_link:
            ENABLED.symlink_to(CONF)
        run('nginx', '-t')
        # Snapshot the running database before a newer application can migrate it.
        # The service and its notification worker are stopped during this step.
        if was_active:
            run('systemctl', 'stop', 'bojizhuye.service')
        database = STATE / 'clubs.sqlite3'
        if database.exists():
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, \
                    closing(sqlite3.connect(backup / 'before.sqlite3')) as destination:
                source.backup(destination)
            (backup / 'before.sqlite3').chmod(0o600)
        set_current(release)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'restart', 'bojizhuye.service')
        for attempt in range(20):
            try:
                result = http_check('http://127.0.0.1:5381/api/club/health', json_result=True)
                if result.get('status') != 'ok':
                    raise RuntimeError('API health did not report ok.')
                break
            except Exception:
                if attempt == 19:
                    raise
                time.sleep(0.5)
        run('systemctl', 'reload', 'nginx')
        host = args.host + (':' + str(args.http_port) if args.http_port != 80 else '')
        base_url = 'http://127.0.0.1:' + str(args.http_port)
        routes = ['/', '/schools', '/join', '/city-vote', '/city-vote/', '/admin', '/admin/', '/api/club/health']
        # Older pinned archives need not contain the event module.
        if (release / 'web/creator/events/index.html').is_file():
            routes.extend(('/events', '/events/', '/ticket', '/ticket/', '/check-in', '/check-in/', '/api/campaign/events'))
        for route in routes:
            http_check(base_url + route, host)
        session = http_check(base_url + '/api/admin/session', host, json_result=True)
        if session.get('setupAvailable'):
            raise RuntimeError('Public administrator setup must stay disabled.')
        if args.redirect_ip:
            http_redirect_check(args.redirect_ip, origin_for(args.host, args.http_port))
        run('systemctl', 'enable', 'bojizhuye.service')
    except BaseException:
        print('Deployment failed. Restoring only this project configuration.', file=sys.stderr)
        for path, saved in snapshots.items():
            if saved:
                atomic_write(path, saved[0], saved[1])
            else:
                path.unlink(missing_ok=True)
        if not previous_enabled_link:
            ENABLED.unlink(missing_ok=True)
        if previous_link is None:
            current.unlink(missing_ok=True)
        else:
            set_current(previous_link)
        run('systemctl', 'daemon-reload', check=False)
        run('systemctl', 'restart' if was_active else 'stop', 'bojizhuye.service', check=False)
        if not was_enabled:
            run('systemctl', 'disable', 'bojizhuye.service', check=False)
        # Never reload a configuration that failed validation.
        if run('nginx', '-t', check=False).returncode == 0:
            run('systemctl', 'reload', 'nginx', check=False)
        print('Release/data retained for diagnosis. Backup: ' + str(backup), file=sys.stderr)
        raise
    print('Deployed commit ' + args.revision + ' at http://' + host)
    print('No administrator, password, robot configuration or test data was imported.')
    print('Existing application data and credentials retained. Private rollback metadata: ' + str(backup))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
