"""Add a restricted edge ingress; preserve existing domains, certificates and Java."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    if p.returncode:
        raise RuntimeError('Command failed: ' + argv[0] + ' (output withheld)')
    return p.stdout


def add_origin(text):
    lines = text.splitlines()
    positions = [i for i, line in enumerate(lines) if line.startswith('CLUB_COMPAT_ORIGINS=')]
    if len(positions) != 1:
        raise ValueError('Expected one origin configuration')
    index = positions[0]
    origins = lines[index].split('=', 1)[1].split(',')
    if origins != ['https://boji.tensorbot.xyz']:
        raise ValueError('Origin already configured or unexpected origins')
    lines[index] += ',https://leanmuscleclub.com'
    return '\n'.join(lines) + '\n'


def main(source):
    if os.geteuid() != 0 or not Path('/etc/leanschool/daily-release-host.json').exists():
        raise ValueError('Expected registered origin host')
    target = Path('/etc/nginx/conf.d/lean-edge-origin.conf')
    if target.exists():
        raise ValueError('Ingress already exists; inspect rather than overwrite')
    paths = [Path('/etc/bojizhuye/bojizhuye.env'), Path('/etc/bojizhuye/public-gateway.env')]
    updates = {path: add_origin(path.read_text()) for path in paths}
    backup = Path('/var/backups/lean-edge-origin') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    protected = [Path('/etc/nginx/sites-available/bojizhuye.conf'), Path('/etc/nginx/sites-available/leanschool.conf')]
    for domain in ('boji.tensorbot.xyz', 'www.leanschool.net'):
        protected.extend(Path('/etc/nginx/ssl') / domain / name for name in ('fullchain.pem', 'privkey.pem'))
    fingerprints = {p: hashlib.sha256(p.read_bytes()).digest() for p in protected}
    for path in paths:
        shutil.copy2(path, backup / path.name)
    try:
        shutil.copyfile(source, target)
        run(['nginx', '-t'])
        for path, text in updates.items():
            path.write_text(text)
        run(['systemctl', 'restart', 'bojizhuye', 'bojizhuye-public-gateway'])
        run(['systemctl', 'reload', 'nginx'])
        for domain in ('boji.tensorbot.xyz', 'www.leanschool.net'):
            code = run(['curl', '--noproxy', '*', '--max-time', '15', '-sS', '--retry', '4', '--retry-connrefused', '--retry-delay', '1',
                        '-o', '/dev/null', '-w', '%{http_code}', '--resolve', domain + ':443:127.0.0.1',
                        'https://' + domain + '/api/club/health'])
            if code != '200':
                raise ValueError('Existing domain health failed')
        if any(hashlib.sha256(p.read_bytes()).digest() != value for p, value in fingerprints.items()):
            raise ValueError('Existing domain configuration changed')
    except Exception:
        for path in paths:
            shutil.copyfile(backup / path.name, path)
        target.unlink(missing_ok=True)
        run(['nginx', '-t'])
        run(['systemctl', 'restart', 'bojizhuye', 'bojizhuye-public-gateway'])
        run(['systemctl', 'reload', 'nginx'])
        raise
    result = {'ok': True, 'backup': str(backup), 'existingDomainsAndCertificatesUnchanged': True,
              'allowedEdge': '47.88.2.70', 'newOrigin': 'https://leanmuscleclub.com'}
    (backup / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
