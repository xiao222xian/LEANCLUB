#!/usr/bin/env python3
"""One-time retirement of inspected legacy apps on 152.136.108.87."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

def run(*args, check=True, input=None):
    return subprocess.run(args, check=check, text=True, input=input, capture_output=True)

assert os.geteuid() == 0, 'Run via sudo.'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://metadata.tencentyun.com/latest/meta-data/public-ipv4', timeout=4) as r:
    assert r.read().decode().strip() == '152.136.108.87', 'Wrong server.'
assert run('systemctl', 'is-active', 'bojizhuye.service').stdout.strip() == 'active'
backup = Path('/var/backups/bojizhuye-retired') / time.strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True, mode=0o700)
os.chmod(backup.parent, 0o700)

# Retain a private copy; comment only the five inspected business jobs.
cron = run('crontab', '-u', 'ubuntu', '-l').stdout
lines = cron.splitlines()
targets = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith('#')
           and ('/home/Sequoia-X' in line or '/home/ubuntu/hermes-arxiv-agent' in line)]
disabled_jobs = [line for line in lines
                 if line.startswith('# Disabled for Boji deployment 2026-10-22: ')
                 and ('/home/Sequoia-X' in line or '/home/ubuntu/hermes-arxiv-agent' in line)]
assert len(targets) + len(disabled_jobs) == 5, 'Cron changed; inspect it before stopping jobs.'
(backup / 'ubuntu.crontab').write_text(cron)
os.chmod(backup / 'ubuntu.crontab', 0o600)
for i in targets:
    lines[i] = '# Disabled for Boji deployment 2026-10-22: ' + lines[i]
run('crontab', '-u', 'ubuntu', '-', input='\n'.join(lines) + '\n')
print('DISABLED business cron jobs:', len(targets), 'already disabled:', len(disabled_jobs))

# Preserve both inspected file forms intact, never delete site data.
disabled = Path('/etc/nginx/sites-disabled')
disabled.mkdir(exist_ok=True)
moved = []
default_conf = Path('/etc/nginx/sites-available/bojizhuye-default.conf')
default_link = Path('/etc/nginx/sites-enabled/bojizhuye-default.conf')
assert not (default_conf.exists() or default_conf.is_symlink() or
            default_link.exists() or default_link.is_symlink())
try:
    for name in ('ai-for-beginners', 'omniroute'):
        source = Path('/etc/nginx/sites-enabled') / name
        destination = disabled / name
        assert source.is_file() and source.stat().st_uid == 0, 'Unexpected site ownership.'
        assert not (destination.exists() or destination.is_symlink()), 'Disabled site already exists.'
        expected = '/home/ubuntu/AI-For-Beginners' if name == 'ai-for-beginners' else 'omniroute.tensorbot.xyz'
        assert expected in source.read_text(), 'Site content changed; inspect before retirement.'
        source.rename(destination)
        moved.append((source, destination))
    default_conf.write_text('''# Managed by Boji legacy application retirement.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;
    return 404;
}
''')
    default_conf.chmod(0o644)
    default_link.symlink_to(default_conf)
    run('nginx', '-t')
    run('systemctl', 'reload', 'nginx')
except BaseException:
    default_link.unlink(missing_ok=True)
    default_conf.unlink(missing_ok=True)
    for source, destination in reversed(moved):
        destination.rename(source)
    run('nginx', '-t')
    run('systemctl', 'reload', 'nginx')
    print('Nginx restored; business cron remains disabled. Private backup:', backup)
    raise
print('DISABLED legacy nginx sites: ai-for-beginners, omniroute')

# Recheck executable/cwd at signal time, and never signal by broad process name.
stopped = []
for path in Path('/proc').iterdir():
    if not path.name.isdigit():
        continue
    try:
        pid = int(path.name)
        cwd = str((path / 'cwd').resolve())
        exe = str((path / 'exe').resolve())
        name = (path / 'comm').read_text().strip()
        legacy_python = name == 'python3' and (
            pid == 1681899 and cwd == '/home/project (deleted)' or
            pid == 2550570 and cwd == '/home/ubuntu/hermes-arxiv-agent')
        trae = exe.startswith('/home/ubuntu/.trae-cn-server/') or cwd.startswith('/home/ubuntu/.trae-cn-server/')
        if legacy_python or trae:
            os.kill(pid, signal.SIGTERM)
            stopped.append({'pid': pid, 'name': name, 'cwd': cwd, 'exe': exe})
    except (FileNotFoundError, ProcessLookupError):
        pass
(backup / 'stopped-processes.json').write_text(json.dumps(stopped, indent=2))
os.chmod(backup / 'stopped-processes.json', 0o600)
print('TERM legacy app processes:', len(stopped))

# Stop idle container services only after checking every containerd namespace.
services = ['ModemManager.service', 'upower.service', 'udisks2.service']
if not run('docker', 'ps', '-aq').stdout.strip():
    services += ['docker.socket', 'docker.service']
    namespaces = run('ctr', 'namespaces', 'list', '-q').stdout.split()
    if all(not run('ctr', '-n', ns, 'tasks', 'list', '-q').stdout.strip() for ns in namespaces):
        services.append('containerd.service')
original = {unit: {'active': run('systemctl', 'is-active', unit, check=False).stdout.strip(),
                   'enabled': run('systemctl', 'is-enabled', unit, check=False).stdout.strip()}
            for unit in services}
(backup / 'service-states.json').write_text(json.dumps(original, indent=2))
os.chmod(backup / 'service-states.json', 0o600)
failures = []
for unit in services:
    result = run('systemctl', 'disable', '--now', unit, check=False)
    state = run('systemctl', 'is-active', unit, check=False).stdout.strip()
    print('SERVICE', unit, 'rc='+str(result.returncode),
          state)
    if result.returncode or state == 'active':
        failures.append(unit)
print('BACKUP', backup)
time.sleep(2)
listeners = run('ss', '-ltnH').stdout.splitlines()
ports = {int(line.split()[3].rsplit(':', 1)[1]) for line in listeners}
old_ports = ports & {8080, 8765, 32999, 51000, 51001, 51002, 51003}
print('LEGACY ports remaining:', sorted(old_ports))
for unit in ('bojizhuye.service', 'nginx.service', 'ssh.service', 'tat_agent.service'):
    state = run('systemctl', 'is-active', unit, check=False).stdout.strip()
    print('REQUIRED', unit, state)
    if state != 'active':
        failures.append(unit)
with opener.open('http://127.0.0.1:5381/api/club/health', timeout=4) as response:
    assert json.load(response).get('status') == 'ok', 'Boji health failed.'
assert not failures and not old_ports, 'Partial completion; inspect reported failures and backup.'
print('RETIREMENT VERIFIED; app data retained, cloud security/root cron unchanged.')
