#!/usr/bin/env python3
"""Install/update the root-managed, key-only LEAN SCHOOL release SSH identity.

No SSH private key, GitHub token, password, database import or service restart.
Existing accounts are adopted only with this installer's matching private record.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import uuid

USER = 'leanschool-deploy'
HOME = Path('/var/lib/leanschool-deploy')
ETC = Path('/etc/leanschool')
RECORD = ETC / 'release-gate-install.json'
CONFIG = ETC / 'release-gate.json'
HOST = ETC / 'daily-release-host.json'
CURRENT = ETC / 'daily-current.json'
LAUNCHER = Path('/usr/local/sbin/leanschool-release-gate')
SUDOERS = Path('/etc/sudoers.d/leanschool-release-gate')
KEYS = HOME / '.ssh/authorized_keys'
CACHE = Path('/var/lib/leanschool/releases/cloud')
MANAGED = (LAUNCHER, SUDOERS, CONFIG, KEYS)
ACCOUNT_COMMENT = 'LEAN SCHOOL restricted release account'


class InstallError(Exception):
    pass


def command(args, *, check=True):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise InstallError('An installation check failed; command output withheld.')
    return result


def protected(path, directory=False, private=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.exists():
        raise InstallError('Expected an existing absolute, non-symlink root-managed path.')
    for item in (path, *path.parents):
        info = item.stat()
        if item.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise InstallError('A deployment path or ancestor is not protected by root ownership.')
    if directory and not path.is_dir() or not directory and not path.is_file():
        raise InstallError('Unexpected deployment path type.')
    if private and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise InstallError('A private deployment file has excess permissions.')
    return path


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic(path, data, mode):
    protected(path.parent, directory=True)
    if path.is_symlink():
        raise InstallError('Refusing a symlink destination.')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def public_key(raw):
    lines = raw.strip().splitlines()
    if len(lines) != 1 or len(raw) > 4096:
        raise InstallError('Provide exactly one OpenSSH Ed25519 public key, with no options or private key.')
    parts = lines[0].split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise InstallError('Only a plain OpenSSH Ed25519 public key is accepted.')
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (ValueError, base64.binascii.Error):
        raise InstallError('The Ed25519 public key encoding is invalid.') from None
    expected_prefix = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'
    if len(blob) != len(expected_prefix) + 32 or not blob.startswith(expected_prefix):
        raise InstallError('The Ed25519 public key structure is invalid.')
    return 'ssh-ed25519 ' + base64.b64encode(blob).decode('ascii')


def key_line(key):
    return ('restrict,command="/usr/bin/sudo -n /usr/local/sbin/leanschool-release-gate" '
            + key + ' leanschool-release\n').encode('ascii')


def launcher(tools):
    script = shlex.quote((tools / 'deploy/ubuntu/release-gate.py').as_posix())
    return ('#!/bin/sh\n# Managed by LEAN SCHOOL install-release-gate.py.\n'
            '[ "$#" -eq 0 ] || exit 64\n'
            '[ "${SUDO_USER:-}" = "leanschool-deploy" ] || exit 64\n'
            'exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 '
            'SUDO_USER=leanschool-deploy /usr/bin/python3 ' + script + '\n').encode()


def sudoers():
    # The empty argument string is a sudoers restriction, not a shell argument.
    return (USER + ' ALL=(root) NOPASSWD: NOSETENV: ' + LAUNCHER.as_posix() + ' ""\n').encode('ascii')


def module(name, path):
    protected(path)
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def account_info():
    import pwd
    try:
        return pwd.getpwnam(USER)
    except KeyError:
        return None


def validate_account(account, record):
    if (not record or record.get('format') != 1 or record.get('username') != USER
            or account.pw_uid <= 0 or account.pw_uid != record.get('uid')
            or account.pw_gid != record.get('gid') or account.pw_dir != str(HOME)
            or account.pw_shell != '/bin/sh' or account.pw_gecos != ACCOUNT_COMMENT):
        raise InstallError('Existing deployment account is not registered to this installer.')
    groups = command(['/usr/bin/id', '-G', USER]).stdout.split()
    if groups != [str(account.pw_gid)]:
        raise InstallError('Deployment account has unreviewed supplementary groups.')
    status = command(['/usr/bin/passwd', '-S', USER]).stdout.split()
    if len(status) < 2 or status[1] != 'L':
        raise InstallError('Deployment account password must remain locked.')
    for path in (HOME, HOME / '.ssh'):
        protected(path, directory=True)
        if stat.S_IMODE(path.stat().st_mode) != 0o755:
            raise InstallError('Deployment home and SSH directory must be root-owned mode 0755.')
    expected = {str(path) for path in MANAGED}
    if set(record.get('files', {})) != expected:
        raise InstallError('Existing gate ownership record has an unexpected file set.')
    for path in MANAGED:
        protected(path, private=path == CONFIG)
        if sha(path) != record['files'][str(path)]:
            raise InstallError('An installed gate file changed outside this installer; review before updating.')


def ensure_directory(path, mode):
    if path.exists() or path.is_symlink():
        protected(path, directory=True)
    else:
        protected(path.parent, directory=True)
        path.mkdir(mode=mode)
    os.chmod(path, mode)


def prepare_baseline(directory, tools, coordinator):
    if CURRENT.exists() or CURRENT.is_symlink():
        protected(CURRENT, private=True)
        return None
    if directory is None:
        return None
    helper = module('install_gate_fetcher', tools / 'deploy/ubuntu/fetch-github-artifact.py')
    manifest = coordinator.verify_bundle(directory, helper)
    state = json.loads(protected(ETC / 'platform-state.json', private=True).read_text())
    java = coordinator.owned_link(Path('/opt/leanschool/java/current'), Path('/opt/leanschool/java/releases'))
    ui = coordinator.owned_link(Path('/opt/leanschool/ui/current'), Path('/opt/leanschool/ui/releases'))
    site = coordinator.owned_link(Path('/opt/bojizhuye/current'), Path('/opt/bojizhuye/releases'))
    if (state.get('sourceCommit') != manifest['commits']['backend']
            or state.get('javaRelease') != str(java) or state.get('uiRelease') != str(ui)
            or sha(protected(java / 'yudao-server.jar')) != manifest['artifacts']['yudao-server.jar']['sha256']
            or state.get('jar', {}).get('sha256') != manifest['artifacts']['yudao-server.jar']['sha256']
            or protected(ui / 'artifact.sha256').read_text().strip() != manifest['artifacts']['console.tar.gz']['sha256']
            or site.name != manifest['commits']['website']
            or protected(site / '.deployment-sha256').read_text().strip() != manifest['artifacts']['website.tar.gz']['sha256']):
        raise InstallError('The supplied baseline does not match all active production components.')
    return {'format': 1, 'commits': manifest['commits'], 'artifacts': manifest['artifacts'],
            'baselineRunId': manifest['runId'], 'baselineRunAttempt': manifest['runAttempt']}


@contextmanager
def installation_lock():
    import fcntl
    opened = []
    try:
        for name in ('/run/leanschool-release-gate.lock', '/run/lock/leanschool-daily-release.lock'):
            descriptor = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stream = os.fdopen(descriptor, 'a')
            opened.append(stream)
            if os.fstat(descriptor).st_uid != 0:
                raise InstallError('Deployment lock owner differs.')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InstallError('A publication is running; retry gate installation after it finishes.') from None
        yield
    finally:
        for stream in reversed(opened):
            stream.close()


def install(args):
    tools = protected(args.tools_root, directory=True)
    coordinator = module('install_gate_daily', tools / 'deploy/ubuntu/deploy-daily-release.py')
    for relative in (*coordinator.TOOL_FILES, 'deploy/ubuntu/release-gate.py'):
        protected(tools / relative)
    protected(ETC, directory=True)
    state = json.loads(protected(ETC / 'platform-state.json', private=True).read_text())
    if state.get('database') != 'leanschool' or state.get('dbUser') != 'leanschool_app':
        raise InstallError('This is not the registered LEAN SCHOOL platform layout.')
    key = public_key(protected(args.public_key_file).read_text(encoding='ascii'))
    marker = {'format': 1, 'publicIp': '152.136.108.87',
              'machineId': protected(Path('/etc/machine-id')).read_text().strip()}
    if not re.fullmatch('[a-f0-9]{32}', marker['machineId']):
        raise InstallError('The production machine ID is invalid.')
    if HOST.exists() or HOST.is_symlink():
        if json.loads(protected(HOST, private=True).read_text()) != marker:
            raise InstallError('The production host marker identifies a different machine.')
    record = json.loads(protected(RECORD, private=True).read_text()) if RECORD.exists() or RECORD.is_symlink() else None
    account = account_info()
    if account is not None:
        validate_account(account, record)
    elif record or any(path.exists() or path.is_symlink() for path in (*MANAGED, HOME)):
        raise InstallError('Unregistered gate paths exist; refusing to overwrite them.')
    baseline = prepare_baseline(args.baseline_release_dir, tools, coordinator)
    for parent in (LAUNCHER.parent, SUDOERS.parent, HOME.parent):
        protected(parent, directory=True)
    command(['/usr/sbin/visudo', '-c'])
    temporary_sudoers = SUDOERS.with_name('.leanschool-gate-check-' + uuid.uuid4().hex)
    atomic(temporary_sudoers, sudoers(), 0o440)
    try:
        command(['/usr/sbin/visudo', '-cf', temporary_sudoers])
    finally:
        temporary_sudoers.unlink()
    if account is None:
        command(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                 '--home-dir', HOME, '--shell', '/bin/sh', '--comment', ACCOUNT_COMMENT, USER])
        command(['/usr/sbin/usermod', '--lock', USER])
        account = account_info()
        if account is None or account.pw_uid == 0:
            raise InstallError('Restricted deployment account creation failed.')
    ensure_directory(HOME, 0o755)
    ensure_directory(HOME / '.ssh', 0o755)
    ensure_directory(CACHE.parent, 0o700)
    ensure_directory(CACHE, 0o700)
    desired_config = {'format': 1, 'repositoryId': 1429653401, 'workflowId': 394724036,
                      'toolsRoot': str(tools)}
    paths = (*MANAGED, HOST, CURRENT, RECORD)
    before = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
              if path.exists() else None for path in paths}
    try:
        atomic(LAUNCHER, launcher(tools), 0o755)
        atomic(CONFIG, encoded(desired_config), 0o600)
        atomic(HOST, encoded(marker), 0o600)
        if baseline is not None:
            atomic(CURRENT, encoded(baseline), 0o600)
        atomic(SUDOERS, sudoers(), 0o440)
        command(['/usr/sbin/visudo', '-c'])
        # Enable the restricted key only after every privileged destination validates.
        atomic(KEYS, key_line(key), 0o644)
        installed = {'format': 1, 'username': USER, 'uid': account.pw_uid, 'gid': account.pw_gid,
                     'toolsRoot': str(tools), 'files': {str(path): sha(path) for path in MANAGED}}
        atomic(RECORD, encoded(installed), 0o600)
        validate_account(account_info(), installed)
    except BaseException:
        for path, saved in before.items():
            if saved:
                atomic(path, saved[0], saved[1])
            else:
                path.unlink(missing_ok=True)
        raise
    return {'ok': True, 'installed': True, 'username': USER, 'toolsRoot': str(tools),
            'baselineCreated': baseline is not None, 'passwordLocked': True,
            'privateKeyReceived': False, 'productionServicesRestarted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-root', required=True, type=Path)
    parser.add_argument('--public-key-file', required=True, type=Path)
    parser.add_argument('--baseline-release-dir', type=Path)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise InstallError('Run this installer as root on the existing production Linux server.')
    os.umask(0o077)
    with installation_lock():
        print(json.dumps(install(args), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except InstallError as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print(json.dumps({'ok': False, 'error': 'Gate installation failed; details withheld. Inspect the locked account and private installation record.'}), file=sys.stderr)
        sys.exit(1)
