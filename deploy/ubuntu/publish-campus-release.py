"""Small verified website delta on the registered shared origin."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone

ROOT = Path('/opt/bojizhuye')
STATE = Path('/etc/leanschool/daily-current.json')


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Command failed: ' + args[0])
    return result.stdout.decode()


def prepare(manifest, patch):
    if os.geteuid() != 0:
        raise ValueError('Root required on the registered origin')
    marker = json.loads(Path('/etc/leanschool/daily-release-host.json').read_text())
    if marker['publicIp'] != '152.136.108.87' or marker['machineId'] != Path('/etc/machine-id').read_text().strip():
        raise ValueError('Wrong host')
    current = (ROOT / 'current').resolve(strict=True)
    if current.parent != ROOT / 'releases' or current.name != manifest['base']:
        raise ValueError('Current source differs from the reviewed base')
    commit = manifest['commit']
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('Invalid commit')
    destination = ROOT / 'releases' / commit
    if destination.exists():
        raise ValueError('Candidate directory already exists')
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest['patchSha256']:
        raise ValueError('Patch hash mismatch')
    shutil.copytree(current, destination, symlinks=True, ignore=shutil.ignore_patterns('.venv', '__pycache__'))
    (destination / '.venv').symlink_to((current / '.venv').resolve(strict=True))
    def safe(name):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or not (
                path.parts[0] in ('backend', 'scripts', 'web', 'deploy') or name == 'requirements.txt'):
            raise ValueError('Invalid source path')
        return destination / name
    with tarfile.open(patch) as archive:
        for item in archive:
            if item.isdir():
                continue
            if not item.isfile() or item.name not in manifest['files']:
                raise ValueError('Unexpected patch entry')
            target = safe(item.name)
            raw = archive.extractfile(item).read()
            if hashlib.sha256(raw).hexdigest() != manifest['files'][item.name]:
                raise ValueError('Patch entry hash mismatch')
            if target.is_symlink():
                raise ValueError('Source symlink')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o644)
    for name in manifest['deleted']:
        safe(name).unlink(missing_ok=True)
    for name, digest in manifest['files'].items():
        path = safe(name)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Full source verification failed')
    (destination / '.deployment-sha256').write_text(manifest['artifact']['sha256'] + '\n')
    run([str(destination / '.venv/bin/python'), '-m', 'compileall', '-q', str(destination / 'backend')])
    return current, destination


def activate(manifest, destination):
    previous = (ROOT / 'current').resolve(strict=True)
    if previous.name != manifest['base'] or destination.name != manifest['commit']:
        raise ValueError('Release identity changed')
    def switch(path):
        pending = ROOT / 'campus-next'
        if pending.exists() or pending.is_symlink():
            raise ValueError('Pending release already exists')
        pending.symlink_to(path)
        os.replace(pending, ROOT / 'current')
    try:
        switch(destination)
        run(['systemctl', 'restart', 'bojizhuye', 'bojizhuye-public-gateway'])
        for endpoint in ['/api/club/health', '/api/club/schools', '/app-api/leanclub/site/capabilities']:
            response = run(['curl', '--retry', '5', '--retry-connrefused', '--retry-delay', '1', '-fsS', '--max-time', '15',
                '--resolve', 'boji.tensorbot.xyz:443:127.0.0.1', 'https://boji.tensorbot.xyz' + endpoint])
            data = json.loads(response)
            if endpoint.endswith('/schools') and len(data.get('published', [])) != manifest['expectedClubs']:
                raise ValueError('School count mismatch')
    except Exception:
        switch(previous)
        run(['systemctl', 'restart', 'bojizhuye', 'bojizhuye-public-gateway'])
        raise
    state = json.loads(STATE.read_text())
    state['commits']['website'] = manifest['commit']
    state['artifacts']['website.tar.gz'] = manifest['artifact']
    state.update(publishedAtUtc=datetime.now(timezone.utc).isoformat(), schemaChanged=bool(manifest.get('schemaChanged', False)),
        databaseRestored=False, changed={'website': True, 'backend': False, 'adminUi': False})
    state.pop('manualMigration', None)
    if manifest.get('manualMigration'):
        state['manualMigration'] = manifest['manualMigration']
    temporary = STATE.with_suffix('.campus-next')
    temporary.write_text(json.dumps(state, indent=2))
    temporary.chmod(0o600)
    os.replace(temporary, STATE)
    print(json.dumps({'active': manifest['commit'], 'schools': manifest['expectedClubs'], 'javaUnchanged': True}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'activate'])
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--patch', type=Path)
    a = p.parse_args()
    m = json.loads(a.manifest.read_text())
    if a.action == 'prepare':
        _, target = prepare(m, a.patch)
        print(json.dumps({'prepared': str(target)}))
    else:
        activate(m, ROOT / 'releases' / m['commit'])
