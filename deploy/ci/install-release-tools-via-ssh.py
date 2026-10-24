#!/usr/bin/env python3
"""One-shot, fixed-version maintenance; never publishes an application release.

The temporary password is only passed to OpenSSH's askpass child environment.
Neither child output nor exception details are exposed to Actions logs.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

COMMIT = '5ac6d1b39b391d038e1813ca28a142c0eacd99bd'
ARCHIVE_SHA256 = '135189734925a5f6e439771d9cba9c946e446db69d948f9c57c405929ce994be'
ARCHIVE_PATHS = ('deploy', 'tests/test_daily_release.py', 'tests/test_fetch_github_artifact.py',
                 'tests/test_install_release_gate.py', 'tests/test_release_gate.py')
TARGET = 'ubuntu@152.136.108.87'
UPLOAD = '/home/ubuntu/leanschool-daily-tools-v3.tar.gz'
TOOLS_ROOT = '/opt/leanschool/release-tools/' + COMMIT
REMOTE_COMMAND = ('sudo -n /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin '
                  'LANG=C.UTF-8 /usr/bin/python3 -B -')
ASKPASS = '''#!/usr/bin/python3
import os, sys
password = os.environ.get("LS_ONE_SHOT_SSH_PASSWORD")
if password is None:
    sys.exit(1)
sys.stdout.write(password + "\\n")
'''
REMOTE_ERRORS = frozenset(('REMOTE_PREFLIGHT_FAILED', 'REMOTE_ARCHIVE_MISMATCH',
    'REMOTE_ARCHIVE_INVALID', 'REMOTE_TREE_MISMATCH', 'REMOTE_TESTS_FAILED',
    'GATE_INSTALL_FAILED', 'POSTCHECK_FAILED', 'MAINTENANCE_BUSY', 'REMOTE_INTERNAL_ERROR'))
CLIENT_ERRORS = frozenset(('INVALID_CONTEXT', 'MISSING_CREDENTIALS', 'ARCHIVE_FAILED',
    'ARCHIVE_MISMATCH', 'UPLOAD_FAILED', 'SSH_FAILED', 'SSH_STATUS_UNKNOWN', 'CLIENT_INTERNAL_ERROR'))


def report_failure(code):
    if not isinstance(code, str) or code not in REMOTE_ERRORS | CLIENT_ERRORS:
        code = 'CLIENT_INTERNAL_ERROR'
    print(json.dumps({'ok': False, 'errorCode': code}))
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print('::error title=LEAN SCHOOL maintenance::' + code)

# Fixed program sent on stdin; no shell expansion, arguments or user-provided paths.
REMOTE_PROGRAM = r'''
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

COMMIT = '5ac6d1b39b391d038e1813ca28a142c0eacd99bd'
PREVIOUS = '580d417e912310f9762bc4c6d341b2f26a649dad'
ARCHIVE_SHA256 = '135189734925a5f6e439771d9cba9c946e446db69d948f9c57c405929ce994be'
PARENT = Path('/opt/leanschool/release-tools')
DESTINATION = PARENT / COMMIT
UPLOAD = Path('/home/ubuntu/leanschool-daily-tools-v3.tar.gz')
TESTS = ('test_daily_release.py', 'test_fetch_github_artifact.py',
         'test_install_release_gate.py', 'test_release_gate.py')
SERVICES = ('nginx', 'mysql', 'leanschool-redis', 'leanschool-java',
            'bojizhuye', 'bojizhuye-public-gateway')
PRODUCTION_FILES = (
    '/etc/nginx/sites-available/bojizhuye.conf',
    '/etc/nginx/sites-available/leanschool.conf',
    '/etc/nginx/snippets/leanschool-proxy.conf',
    '/etc/leanschool/platform-secrets.json',
    '/etc/leanschool/bridge-secrets.env',
    '/etc/bojizhuye/bojizhuye.env',
    '/etc/bojizhuye/public-gateway.env')
CURRENT_LINKS = ('/opt/bojizhuye/current', '/opt/leanschool/java/current', '/opt/leanschool/ui/current')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
       'PYTHONDONTWRITEBYTECODE': '1'}

class MaintenanceError(Exception):
    pass

def fail(code):
    raise MaintenanceError(code)

def protected(path, directory=False, private=False):
    path = Path(path)
    if not path.is_absolute() or not path.exists():
        fail('REMOTE_PREFLIGHT_FAILED')
    for entry in (path, *path.parents):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            fail('REMOTE_PREFLIGHT_FAILED')
    info = path.stat()
    if (directory and not stat.S_ISDIR(info.st_mode)
            or not directory and not stat.S_ISREG(info.st_mode)
            or private and info.st_mode & 0o077):
        fail('REMOTE_PREFLIGHT_FAILED')
    return path

def preflight():
    if sys.platform != 'linux' or os.geteuid() != 0:
        fail('REMOTE_PREFLIGHT_FAILED')
    marker = json.loads(protected('/etc/leanschool/daily-release-host.json', private=True).read_text())
    machine_id = protected('/etc/machine-id').read_text().strip()
    if (marker.get('format') != 1 or marker.get('publicIp') != '152.136.108.87'
            or not re.fullmatch('[a-f0-9]{32}', machine_id) or marker.get('machineId') != machine_id):
        fail('REMOTE_PREFLIGHT_FAILED')
    config = json.loads(protected('/etc/leanschool/release-gate.json', private=True).read_text())
    if (config.get('format') != 1 or config.get('repositoryId') != 1429653401
            or config.get('workflowId') != 394724036
            or config.get('toolsRoot') not in (str(PARENT / PREVIOUS), str(DESTINATION))):
        fail('REMOTE_PREFLIGHT_FAILED')
    protected(PARENT, directory=True)
    protected(Path(config['toolsRoot']), directory=True)
    protected('/etc/leanschool/github-release-key.pub')
    protected('/var/lib/leanschool/releases/bootstrap-40049018447', directory=True)

def inspect_archive(raw):
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        fail('REMOTE_ARCHIVE_MISMATCH')
    files, directories, seen = {}, set(), set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        if archive.pax_headers.get('comment') != COMMIT:
            fail('REMOTE_ARCHIVE_INVALID')
        total = 0
        for index, member in enumerate(archive):
            name = member.name.rstrip('/') if member.isdir() else member.name
            path = PurePosixPath(name)
            if (index > 2000 or not name or '\\' in name or str(path) != name or path.is_absolute()
                    or any(part in ('', '.', '..') for part in path.parts) or name in seen
                    or not (member.isfile() or member.isdir())
                    or member.size < 0 or member.size > 8 * 1024 * 1024):
                fail('REMOTE_ARCHIVE_INVALID')
            allowed = (path.parts[0] == 'deploy' or name == 'tests' and member.isdir()
                       or name in {'tests/' + test for test in TESTS} and member.isfile())
            if not allowed or name == 'deploy' and not member.isdir():
                fail('REMOTE_ARCHIVE_INVALID')
            seen.add(name)
            if member.isdir():
                directories.add(name)
            else:
                total += member.size
                if total > 64 * 1024 * 1024:
                    fail('REMOTE_ARCHIVE_INVALID')
                content = archive.extractfile(member).read()
                if len(content) != member.size:
                    fail('REMOTE_ARCHIVE_INVALID')
                files[name] = content
    if len(files) != 45 or not {'tests/' + test for test in TESTS}.issubset(files):
        fail('REMOTE_ARCHIVE_INVALID')
    for name in files:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and str(parent) not in directories:
                fail('REMOTE_ARCHIVE_INVALID')
    if any(name in files for name in directories):
        fail('REMOTE_ARCHIVE_INVALID')
    return files, directories

def read_archive():
    descriptor = os.open(UPLOAD, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 32 * 1024 * 1024:
            fail('REMOTE_ARCHIVE_INVALID')
        raw = stream.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        fail('REMOTE_ARCHIVE_INVALID')
    return inspect_archive(raw)

def verify_tree(root, files, directories):
    protected(root, directory=True)
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*')}
    if actual != set(files) | directories:
        fail('REMOTE_TREE_MISMATCH')
    for name in directories:
        path = protected(root / name, directory=True)
        if stat.S_IMODE(path.stat().st_mode) != 0o755:
            fail('REMOTE_TREE_MISMATCH')
    for name, content in files.items():
        path = protected(root / name)
        if (stat.S_IMODE(path.stat().st_mode) != 0o644
                or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(content).digest()):
            fail('REMOTE_TREE_MISMATCH')

def install_tree(files, directories):
    if DESTINATION.exists() or DESTINATION.is_symlink():
        verify_tree(DESTINATION, files, directories)
        return
    stage = Path(tempfile.mkdtemp(prefix='.maintenance-', dir=PARENT))
    try:
        os.chmod(stage, 0o755)
        for name in sorted(directories, key=lambda value: (value.count('/'), value)):
            (stage / name).mkdir(mode=0o755)
            os.chmod(stage / name, 0o755)
        for name, content in files.items():
            path = stage / name
            with path.open('xb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(path, 0o644)
        verify_tree(stage, files, directories)
        if DESTINATION.exists() or DESTINATION.is_symlink():
            fail('REMOTE_TREE_MISMATCH')
        stage.rename(DESTINATION)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

def command(args, code, timeout=30, cwd=None):
    try:
        result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, env=ENV, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        fail(code)
    if result.returncode:
        fail(code)
    return result

def service_check(code):
    command(['/usr/sbin/nginx', '-t'], code)
    for unit in SERVICES:
        command(['/usr/bin/systemctl', 'is-active', '--quiet', unit], code)

def production_snapshot():
    # These private digests only live in memory and are never returned or logged.
    files = {name: hashlib.sha256(protected(name).read_bytes()).hexdigest() for name in PRODUCTION_FILES}
    certificates = set()
    for name in PRODUCTION_FILES[:2]:
        for directive in re.findall(r'^\s*ssl_certificate(?:_key)?\s+([^;]+);',
                                    protected(name).read_text(), flags=re.M):
            parts = shlex.split(directive)
            if len(parts) != 1:
                fail('REMOTE_PREFLIGHT_FAILED')
            path = Path(parts[0])
            if not path.is_relative_to('/etc/nginx/ssl') or '..' in path.parts:
                fail('REMOTE_PREFLIGHT_FAILED')
            certificates.add(str(protected(path)))
    if len(certificates) != 4:
        fail('REMOTE_PREFLIGHT_FAILED')
    files.update({name: hashlib.sha256(protected(name).read_bytes()).hexdigest() for name in certificates})
    links = {}
    for name in CURRENT_LINKS:
        path = Path(name)
        protected(path.parent, directory=True)
        info = path.lstat()
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0:
            fail('REMOTE_PREFLIGHT_FAILED')
        destination = path.resolve(strict=True)
        if not destination.is_relative_to(path.parent / 'releases'):
            fail('REMOTE_PREFLIGHT_FAILED')
        links[name] = os.readlink(path)
    processes = {}
    for unit in SERVICES:
        result = command(['/usr/bin/systemctl', 'show', '--property=MainPID', '--value', unit],
                         'REMOTE_PREFLIGHT_FAILED')
        if not re.fullmatch(r'[1-9][0-9]*\s*', result.stdout):
            fail('REMOTE_PREFLIGHT_FAILED')
        processes[unit] = int(result.stdout)
    return files, links, processes

def maintain():
    preflight()
    service_check('REMOTE_PREFLIGHT_FAILED')
    before = production_snapshot()
    files, directories = read_archive()
    install_tree(files, directories)
    count = 0
    for test in TESTS:
        result = command(['/usr/bin/python3', '-B', '-m', 'unittest', 'discover',
                          '-s', DESTINATION / 'tests', '-p', test], 'REMOTE_TESTS_FAILED',
                          timeout=120, cwd=DESTINATION)
        matches = re.findall(r'^Ran (\d+) tests? in ', result.stderr, flags=re.M)
        if len(matches) != 1 or re.search(r'\bskipped=', result.stderr):
            fail('REMOTE_TESTS_FAILED')
        count += int(matches[0])
    if count != 88:
        fail('REMOTE_TESTS_FAILED')
    # Re-check the old registered tool root immediately before the guarded installer.
    preflight()
    verify_tree(DESTINATION, files, directories)
    result = command(['/usr/bin/python3', '-B', DESTINATION / 'deploy/ubuntu/install-release-gate.py',
        '--tools-root', DESTINATION, '--public-key-file', '/etc/leanschool/github-release-key.pub',
        '--baseline-release-dir', '/var/lib/leanschool/releases/bootstrap-40049018447'],
        'GATE_INSTALL_FAILED', timeout=120)
    try:
        installed = json.loads(result.stdout)
    except (TypeError, ValueError):
        fail('GATE_INSTALL_FAILED')
    if (installed.get('ok') is not True or installed.get('toolsRoot') != str(DESTINATION)
            or installed.get('passwordLocked') is not True
            or installed.get('productionServicesRestarted') is not False):
        fail('GATE_INSTALL_FAILED')
    service_check('POSTCHECK_FAILED')
    if production_snapshot() != before:
        fail('POSTCHECK_FAILED')
    return {'ok': True, 'toolsRoot': str(DESTINATION), 'tests': count,
            'nginx': 'valid', 'services': 'active', 'applicationPublished': False,
            'productionUnchanged': True}

def main():
    os.umask(0o077)
    protected('/run', directory=True)
    descriptor = os.open('/run/leanschool-tools-maintenance.lock',
                         os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        info = os.fstat(lock.fileno())
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            fail('REMOTE_PREFLIGHT_FAILED')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('MAINTENANCE_BUSY')
        return maintain()

if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True))
    except MaintenanceError as error:
        print(json.dumps({'ok': False, 'errorCode': str(error), 'applicationPublished': False}))
        sys.exit(1)
    except BaseException:
        print(json.dumps({'ok': False, 'errorCode': 'REMOTE_INTERNAL_ERROR', 'applicationPublished': False}))
        sys.exit(1)
'''


class ClientError(Exception):
    pass


def execute(args, code, **kwargs):
    try:
        return subprocess.run(args, capture_output=True, **kwargs)
    except subprocess.TimeoutExpired:
        raise ClientError('SSH_STATUS_UNKNOWN' if code == 'SSH_FAILED' else code) from None
    except OSError:
        raise ClientError(code) from None


def archive(root, destination):
    result = execute(['git', 'archive', '--format=tar.gz', '--output=' + str(destination),
                      COMMIT, *ARCHIVE_PATHS], 'ARCHIVE_FAILED', cwd=root, timeout=60,
                     stdin=subprocess.DEVNULL)
    if result.returncode:
        raise ClientError('ARCHIVE_FAILED')
    if hashlib.sha256(destination.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ClientError('ARCHIVE_MISMATCH')


def ssh_options(known_hosts):
    options = ['-F', '/dev/null']
    for value in ('StrictHostKeyChecking=yes', 'UserKnownHostsFile=' + str(known_hosts),
                  'GlobalKnownHostsFile=/dev/null', 'PreferredAuthentications=password',
                  'PasswordAuthentication=yes', 'PubkeyAuthentication=no',
                  'KbdInteractiveAuthentication=no', 'NumberOfPasswordPrompts=1',
                  'ConnectionAttempts=1', 'ConnectTimeout=20', 'LogLevel=ERROR',
                  'ForwardAgent=no', 'ClearAllForwardings=yes', 'RequestTTY=no'):
        options.extend(('-o', value))
    return options


def safe_response(result):
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise ClientError('SSH_FAILED') from None
    if not isinstance(value, dict):
        raise ClientError('SSH_FAILED')
    if value.get('ok') is False and value.get('errorCode') in REMOTE_ERRORS:
        raise ClientError(value['errorCode'])
    expected = {'ok': True, 'toolsRoot': TOOLS_ROOT, 'tests': 88,
                'nginx': 'valid', 'services': 'active', 'applicationPublished': False,
                'productionUnchanged': True}
    if result.returncode or value != expected:
        raise ClientError('SSH_FAILED')
    # Construct our own output, never forward untrusted stdout/stderr fields.
    return expected


def main():
    if len(sys.argv) != 1 or sys.platform != 'linux':
        raise ClientError('INVALID_CONTEXT')
    password = os.environ.pop('LEANSCHOOL_MAINTENANCE_SSH_PASSWORD', '')
    hosts = os.environ.pop('LEANSCHOOL_DEPLOY_KNOWN_HOSTS', '')
    if (not password or '\n' in password or '\r' in password or '\x00' in password
            or not hosts.strip() or '\x00' in hosts or len(hosts) > 65536):
        raise ClientError('MISSING_CREDENTIALS')
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='leanschool-maintenance-') as temporary:
        workspace = Path(temporary)
        os.chmod(workspace, 0o700)
        package = workspace / 'tools.tar.gz'
        archive(root, package)
        helper = workspace / 'askpass'
        helper.write_text(ASKPASS, encoding='utf-8')
        helper.chmod(0o700)
        known_hosts = workspace / 'known_hosts'
        known_hosts.write_text(hosts.rstrip() + '\n', encoding='utf-8')
        known_hosts.chmod(0o600)
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'DISPLAY': 'leanschool:0',
               'SSH_ASKPASS': str(helper), 'SSH_ASKPASS_REQUIRE': 'force',
               'LS_ONE_SHOT_SSH_PASSWORD': password}
        options = ssh_options(known_hosts)
        uploaded = execute(['/usr/bin/scp', *options, str(package), TARGET + ':' + UPLOAD],
                           'UPLOAD_FAILED', env=env, stdin=subprocess.DEVNULL,
                           timeout=120, start_new_session=True)
        if uploaded.returncode:
            raise ClientError('UPLOAD_FAILED')
        result = execute(['/usr/bin/ssh', '-T', *options, TARGET, REMOTE_COMMAND],
                         'SSH_FAILED', env=env, input=REMOTE_PROGRAM.encode('utf-8'),
                         timeout=900, start_new_session=True)
        return safe_response(result)


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True))
    except ClientError as error:
        report_failure(str(error))
        sys.exit(1)
    except BaseException:
        report_failure('CLIENT_INTERNAL_ERROR')
        sys.exit(1)
