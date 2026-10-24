"""Fixed-host edge release. Read only committed Git objects; never package runtime data."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request

PREFIX = 'web/creator/'
ORIGIN = 'https://leanmuscleclub.com'
BASE = Path('/srv/www/leanmuscleclub')
CONFIG = Path('/etc/nginx/conf.d/leanmuscleclub.conf')
PROXY = Path('/etc/nginx/snippets/lean-edge-proxy.conf')


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Command failed: ' + argv[0] + ' (output withheld)')
    return result.stdout


def manifest(root, commit):
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('A complete commit is required')
    raw = subprocess.check_output(['git', '-C', str(root), 'archive', commit, 'web/creator'])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for item in archive:
            if item.isdir():
                continue
            if not item.isfile() or not item.name.startswith(PREFIX):
                raise ValueError('Only regular static files are allowed')
            data = archive.extractfile(item).read()
            files[item.name[len(PREFIX):]] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    return {'format': 1, 'commit': commit, 'origin': ORIGIN, 'files': files}


def write_static(archive_path, record, stage):
    commit = record['commit']
    if not re.fullmatch('[a-f0-9]{40}', commit) or record.get('origin') != ORIGIN:
        raise ValueError('Invalid release identity')
    expected = record['files']
    if not {'index.html', 'site-filing.js', 'city-vote/comments.js', 'events/index.html'} <= expected.keys():
        raise ValueError('Missing required static files')
    stage.mkdir(parents=True, exist_ok=False)
    seen = set()
    with tarfile.open(archive_path, 'r:gz') as archive:
        for item in archive:
            prefix = 'LEANCLUB-' + commit + '/' + PREFIX
            if not item.name.startswith(prefix) or item.isdir():
                continue
            name = item.name[len(prefix):]
            path = PurePosixPath(name)
            if (not item.isfile() or name not in expected or name in seen or path.is_absolute()
                    or '..' in path.parts or '\\' in name or ':' in name or item.size > 32 * 1024**2):
                raise ValueError('Unexpected archive member')
            data = archive.extractfile(item).read()
            if len(data) != expected[name]['bytes'] or hashlib.sha256(data).hexdigest() != expected[name]['sha256']:
                raise ValueError('Source digest mismatch')
            if path.suffix in ('.html', '.js', '.json'):
                data = data.replace(b'https://www.leanschool.net', ORIGIN.encode())
            # Shared host-aware filing code hides unverified hosts itself.
            # Do not fork the application JavaScript during edge deployment.
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
            seen.add(name)
    if seen != expected.keys():
        raise ValueError('Archive is incomplete')
    return len(seen)


def symlink(target, destination):
    temporary = destination.with_name(destination.name + '.edge-next')
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('A pending release link already exists')
    temporary.symlink_to(target)
    os.replace(temporary, destination)


def deploy(record_path, config_dir, skip_data_backup=False):
    if os.geteuid() != 0 or not CONFIG.is_file() or not (BASE / 'current').is_symlink():
        raise ValueError('Expected registered edge host and root access')
    record = json.loads(record_path.read_text())
    previous = (BASE / 'current').resolve(strict=True)
    if previous.parent != BASE / 'releases':
        raise ValueError('Current release outside expected directory')
    tag = stamp() + '-' + record['commit'][:12]
    backup = Path('/var/backups/lean-edge') / tag
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    for path, name in ((CONFIG, 'nginx.conf'), (Path('/etc/systemd/system/leanmuscleclub-api.service'), 'api.service')):
        shutil.copy2(path, backup / name)
    if PROXY.exists():
        shutil.copy2(PROXY, backup / 'proxy.conf')
    api_path = Path('/srv/leanmuscleclub-api/current').resolve(strict=True)
    if not skip_data_backup:
        with tarfile.open(backup / 'previous-code.tar.gz', 'w:gz') as archive:
            archive.add(previous, arcname='web', recursive=True)
            archive.add(api_path, arcname='api', recursive=True)
    certificate = Path('/etc/letsencrypt/live/leanmuscleclub.com')
    cert_hashes = {}
    for name in ('fullchain.pem', 'privkey.pem'):
        raw = (certificate / name).read_bytes()
        if not skip_data_backup:
            (backup / name).write_bytes(raw)
            (backup / name).chmod(0o600)
        cert_hashes[name] = hashlib.sha256(raw).digest()
    (backup / 'previous.json').write_text(json.dumps({'web': str(previous), 'api': str(api_path)}))
    url = 'https://codeload.github.com/xiao222xian/LEANCLUB/tar.gz/' + record['commit']
    package = backup / 'source.tar.gz'
    with urllib.request.urlopen(url, timeout=90) as response, package.open('wb') as out:
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 512 * 1024**2:
                raise ValueError('Source archive too large')
            out.write(chunk)
    stage = BASE / 'releases' / tag
    count = write_static(package, record, stage)
    PROXY.parent.mkdir(exist_ok=True)
    old_proxy = PROXY.read_bytes() if PROXY.exists() else None
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'leanmuscleclub-api']).returncode == 0
    try:
        if not skip_data_backup:
            run(['systemctl', 'stop', 'leanmuscleclub-api'])
            with sqlite3.connect('/var/lib/leanmuscleclub/clubs.sqlite3') as source, sqlite3.connect(backup / 'legacy.sqlite3') as dest:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                    raise ValueError('Legacy database backup invalid')
        shutil.copyfile(config_dir / 'edge-proxy.conf', PROXY)
        shutil.copyfile(config_dir / 'edge.nginx.conf', CONFIG)
        Path('/var/cache/nginx/lean-public-media').mkdir(parents=True, exist_ok=True)
        Path('/var/cache/nginx/lean-group-qr').mkdir(parents=True, exist_ok=True)
        run(['nginx', '-t'])
        symlink(stage, BASE / 'current')
        run(['systemctl', 'reload', 'nginx'])
        health_paths = ['/', '/api/club/health', '/app-api/leanclub/site/capabilities', '/console/']
        if 'design/index.html' in record['files']:
            health_paths.extend(['/design/', '/design/monogram-mvp/generator.js'])
        for path in health_paths:
            for attempt in range(5):
                status = run(['curl', '--noproxy', '*', '-sS', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '20',
                              '--resolve', 'leanmuscleclub.com:443:127.0.0.1', ORIGIN + path])
                if status == '200':
                    break
                time.sleep(1)
            else:
                raise ValueError('Post-release HTTP check failed: ' + path + ' status=' + status)
        for name, digest in cert_hashes.items():
            if hashlib.sha256((certificate / name).read_bytes()).digest() != digest:
                raise ValueError('Certificate unexpectedly changed')
    except Exception:
        shutil.copyfile(backup / 'nginx.conf', CONFIG)
        if old_proxy is None:
            PROXY.unlink(missing_ok=True)
        else:
            PROXY.write_bytes(old_proxy)
        symlink(previous, BASE / 'current')
        run(['nginx', '-t'])
        run(['systemctl', 'reload', 'nginx'])
        if was_active:
            run(['systemctl', 'start', 'leanmuscleclub-api'])
        raise
    run(['systemctl', 'disable', '--now', 'leanmuscleclub-api'])
    result = {'ok': True, 'commit': record['commit'], 'origin': ORIGIN, 'files': count,
              'backup': str(backup), 'release': str(stage), 'sharedBackend': '152.136.108.87',
              'certificatesUnchanged': True, 'legacyApiStopped': True}
    (backup / 'result.json').write_text(json.dumps(result, indent=2))
    (BASE / 'edge-current.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    m = sub.add_parser('manifest')
    m.add_argument('--root', type=Path, required=True)
    m.add_argument('--commit', required=True)
    m.add_argument('--output', type=Path, required=True)
    d = sub.add_parser('deploy')
    d.add_argument('--manifest', type=Path, required=True)
    d.add_argument('--config-dir', type=Path, required=True)
    d.add_argument('--skip-data-backup', action='store_true', help='Explicit operator opt-out; retain previous release and rollback config only.')
    args = parser.parse_args()
    if args.action == 'manifest':
        result = manifest(args.root, args.commit)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'commit': args.commit, 'files': len(result['files'])}))
    else:
        deploy(args.manifest, args.config_dir, args.skip_data_backup)
