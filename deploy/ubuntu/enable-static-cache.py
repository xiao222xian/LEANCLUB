"""Add browser caching for public static images on the registered origin only."""
import json
import os
from pathlib import Path
import subprocess

marker = json.loads(Path('/etc/leanschool/daily-release-host.json').read_text())
if os.geteuid() != 0 or marker['publicIp'] != '152.136.108.87':
    raise RuntimeError('Registered origin and root required')
block = '''    # BEGIN LEAN STATIC CACHE
    location ~* \\.(?:webp|png|jpe?g|svg|ico|woff2)$ {
        expires 7d;
        add_header X-Content-Type-Options nosniff always;
        try_files $uri =404;
    }
    # END LEAN STATIC CACHE
'''
changed = {}
try:
    for name in ('bojizhuye.conf', 'leanschool.conf'):
        target = (Path('/etc/nginx/sites-enabled') / name).resolve(strict=True)
        if not target.is_relative_to('/etc/nginx'):
            raise RuntimeError('Unexpected nginx file')
        previous = target.read_text()
        if '# BEGIN LEAN STATIC CACHE' in previous:
            continue
        anchor = '    location / { try_files $uri =404; }'
        if previous.count(anchor) != 1:
            raise RuntimeError('Static route anchor changed')
        changed[target] = previous
        target.write_text(previous.replace(anchor, block + anchor))
    subprocess.run(['nginx', '-t'], check=True)
    if changed:
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
except Exception:
    for target, previous in changed.items():
        target.write_text(previous)
    raise
print(json.dumps({'updatedHosts': len(changed), 'staticImageMaxAge': 604800}))
