#!/usr/bin/env python3
"""Validate the uploaded certificate and add the new host, leaving the old vhost intact.

Default is validation only. --apply requires the full stack to be healthy first.
No key material is printed, copied into the application release, or sent elsewhere.
"""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.request
import zipfile

DOMAIN = 'www.leanschool.net'
OLD_DOMAIN = 'boji.tensorbot.xyz'
MARKER = b'# Managed by LEAN SCHOOL full-stack deployment.'
CONF = Path('/etc/nginx/sites-available/leanschool.conf')
ENABLED = Path('/etc/nginx/sites-enabled/leanschool.conf')
SNIPPET = Path('/etc/nginx/snippets/leanschool-proxy.conf')
TLS = Path('/etc/nginx/ssl') / DOMAIN
BACKUP_ROOT = Path('/var/backups/leanschool-https')
UI_INDEX = Path('/opt/leanschool/ui/current/console/index.html')


class CommandError(RuntimeError):
    def __init__(self, command, returncode):
        self.command, self.returncode = command, returncode
        super().__init__('Command failed: ' + command + ' (exit ' + str(returncode) + ')')


def run(*args, data=None, timeout=60):
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # Do not echo command output: tools may include sensitive input in errors.
        raise CommandError(args[0], result.returncode)
    return result.stdout


def read_bundle(path):
    certs, keys = [], []
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Unexpected certificate archive size.')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 20 or sum(x.file_size for x in entries) > 1024 * 1024:
            raise ValueError('Unexpected certificate archive size.')
        seen = set()
        for item in entries:
            if item.orig_filename != item.filename:
                raise ValueError('Non-canonical certificate archive path.')
            p = PurePosixPath(item.filename)
            parts = item.filename.rstrip('/').split('/')
            if (p.is_absolute() or any(part in ('', '.', '..') for part in parts)
                    or '\\' in item.filename or ':' in item.filename
                    or any(ord(char) < 32 for char in item.filename)):
                raise ValueError('Unsafe certificate archive path.')
            if item.filename in seen:
                raise ValueError('Duplicate certificate archive path.')
            seen.add(item.filename)
            kind = stat.S_IFMT(item.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                raise ValueError('Linked or encrypted certificate entries are not supported.')
            if item.is_dir():
                continue
            data = archive.read(item)
            blocks = tuple(re.findall(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', data, re.S))
            if blocks:
                certs.append(blocks)
            if b'-----BEGIN ENCRYPTED PRIVATE KEY-----' in data or b'Proc-Type: 4,ENCRYPTED' in data:
                raise ValueError('An unencrypted Nginx private key is required.')
            keys.extend(match.group(0) + b'\n' for match in re.finditer(
                rb'-----BEGIN (?P<kind>(?:RSA |EC )?PRIVATE KEY)-----.*?-----END (?P=kind)-----', data, re.S))
    if len(keys) != 1 or not certs:
        raise ValueError('Missing or ambiguous certificate/private key.')
    longest = max(map(len, certs))
    candidates = {blocks for blocks in certs if len(blocks) == longest}
    if len(candidates) != 1:
        raise ValueError('Ambiguous certificate chains.')
    blocks = next(iter(candidates))
    if len(blocks) < 2:
        raise ValueError('A complete intermediate certificate chain is required.')
    if len(set(blocks)) != len(blocks):
        raise ValueError('Duplicate certificates in the chain.')
    return blocks, keys[0]


@contextmanager
def validated_bundle(path):
    blocks, key_data = read_bundle(path)
    with tempfile.TemporaryDirectory(prefix='leanschool-cert-') as folder:
        leaf, chain, key = [Path(folder) / p for p in ('leaf.pem', 'chain.pem', 'key.pem')]
        leaf.write_bytes(blocks[0] + b'\n')
        chain.write_bytes(b'\n'.join(blocks[1:]) + b'\n')
        atomic_write(key, key_data, 0o600)
        run('openssl', 'x509', '-in', str(leaf), '-noout', '-checkend', '86400')
        run('openssl', 'verify', '-purpose', 'sslserver', '-verify_hostname', DOMAIN,
            '-untrusted', str(chain), str(leaf))
        pub = run('openssl', 'x509', '-in', str(leaf), '-pubkey', '-noout')
        cert_pub = run('openssl', 'pkey', '-pubin', '-outform', 'DER', data=pub)
        key_pub = run('openssl', 'pkey', '-in', str(key), '-pubout', '-outform', 'DER', '-passin', 'pass:')
        if hashlib.sha256(cert_pub).digest() != hashlib.sha256(key_pub).digest():
            raise ValueError('Private key does not match certificate.')
        print(run('openssl', 'x509', '-in', str(leaf), '-noout', '-issuer', '-dates', '-ext',
                  'subjectAltName').decode().strip())
        yield b'\n'.join(blocks) + b'\n', key_data


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def local_json(port, path, host):
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + path, headers={'Host': host})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.load(response)


def preflight():
    for host in (DOMAIN, OLD_DOMAIN):
        if local_json(5381, '/api/club/health', host).get('status') != 'ok':
            raise ValueError('Python dual-host health check failed.')
    for path in ('/app-api/leanclub/site/published', '/app-api/leanclub/school/list?pageSize=1'):
        if local_json(5382, path, DOMAIN).get('code') != 0:
            raise ValueError('Public Java gateway is not ready.')
    if not UI_INDEX.is_file():
        raise ValueError('Production /console/ frontend is not installed.')
    for path in (CONF, SNIPPET):
        if path.is_symlink() or path.exists() and not path.read_bytes().startswith(MARKER):
            raise ValueError('Existing configuration is not owned by this installer: ' + str(path))
    if (ENABLED.exists() or ENABLED.is_symlink()) and (not ENABLED.is_symlink() or ENABLED.resolve() != CONF):
        raise ValueError('Enabled site path is already owned by another configuration.')
    config = run('nginx', '-T').decode()
    sections = re.split(r'^# configuration file (.+):\s*$', config, flags=re.M)
    for i in range(1, len(sections), 2):
        if Path(sections[i]).resolve() == CONF:
            continue
        text = re.sub(r'#[^\n]*', '', sections[i + 1])
        if any(DOMAIN in names.split() for names in re.findall(r'\bserver_name\s+([^;]+);', text)):
            raise ValueError('Another Nginx file already owns www.leanschool.net.')
    if TLS.is_symlink():
        raise ValueError('Certificate directory must not be a symlink.')
    if TLS.exists() and not TLS.is_dir():
        raise ValueError('Certificate directory is not a directory.')
    for path in (TLS / 'fullchain.pem', TLS / 'privkey.pem'):
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError('Certificate target must be a regular file: ' + str(path))


def https_status(host, route, timeout=12):
    return run('curl', '--noproxy', '*', '--silent', '--show-error', '--max-time', str(timeout),
               '--resolve', host + ':443:127.0.0.1', '-o', '/dev/null', '-w', '%{http_code}',
               'https://' + host + route, timeout=timeout).decode()


def wait_for_https_ready():
    """Allow old Nginx workers to drain; every attempt still verifies TLS and SNI."""
    deadline = time.monotonic() + 20.0
    last = 'no response'
    while True:
        remaining = deadline - time.monotonic()
        # Never round curl's timeout to zero, which would mean no timeout.
        request_ms = min(3000, int(remaining * 1000))
        if request_ms < 1:
            raise RuntimeError('New-domain HTTPS readiness timed out after 20 seconds; last ' + last)
        try:
            code = https_status(DOMAIN, '/', timeout=request_ms / 1000)
            if code == '200':
                return
            last = 'HTTP ' + code
        except CommandError as error:
            if error.command != 'curl' or error.returncode not in {7, 28, 35, 52, 56, 60}:
                raise
            last = 'curl exit ' + str(error.returncode)
        except subprocess.TimeoutExpired:
            last = 'request timeout'
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, remaining))


def apply_certificate(cert, key):
    preflight()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = BACKUP_ROOT / (stamp + '-' + str(os.getpid()))
    backup.mkdir(parents=True, mode=0o700)
    backup.parent.chmod(0o700)
    paths = (CONF, SNIPPET, TLS / 'fullchain.pem', TLS / 'privkey.pem')
    saved = {p: p.read_bytes() if p.exists() else None for p in paths}
    modes = {p: stat.S_IMODE(p.stat().st_mode) if p.exists() else None for p in paths}
    tls_mode = stat.S_IMODE(TLS.stat().st_mode) if TLS.exists() else None
    had_link = ENABLED.is_symlink()
    for i, (path, data) in enumerate(saved.items()):
        if data is not None:
            atomic_write(backup / str(i), data, 0o600)
    atomic_write(backup / 'manifest.json', json.dumps({'paths': [str(p) for p in paths],
        'existed': [saved[p] is not None for p in paths], 'modes': [modes[p] for p in paths],
        'site_enabled': had_link, 'tls_directory_mode': tls_mode}).encode(), 0o600)
    try:
        TLS.mkdir(parents=True, exist_ok=True, mode=0o700)
        TLS.chmod(0o700)
        atomic_write(TLS / 'fullchain.pem', cert, 0o644)
        atomic_write(TLS / 'privkey.pem', key, 0o600)
        atomic_write(CONF, Path(__file__).with_name('leanschool.nginx.conf.in').read_bytes(), 0o644)
        atomic_write(SNIPPET, Path(__file__).with_name('leanschool-proxy.conf.in').read_bytes(), 0o644)
        if not had_link:
            ENABLED.symlink_to(CONF)
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        wait_for_https_ready()
        for host, routes in ((DOMAIN, ('/', '/console/', '/console/login', '/console/leanclub/content',
                                      '/api/campaign', '/app-api/leanclub/site/published')),
                             (OLD_DOMAIN, ('/', '/city-vote', '/admin', '/api/campaign'))):
            for route in routes:
                code = https_status(host, route)
                if code != '200':
                    raise ValueError('HTTPS verification failed: ' + host + route + ' status=' + code)
                print('HTTPS_OK', host + route, code)
        for route in ('/console/assets/__leanschool_missing__.js', '/console/brand/__leanschool_missing__.svg',
                      '/console/__leanschool_missing__.json', '/internal/leanclub/operations',
                      '/app-api/system/auth/login', '/doc.html', '/v3/api-docs',
                      '/swagger-ui/index.html', '/actuator/env', '/druid/'):
            if https_status(DOMAIN, route) != '404':
                raise ValueError('Public route isolation failed: ' + route)
        for route, expected in (('/admin', '/console/login?redirect=/leanclub/content'),
                                ('/check-in', '/console/leanclub/registrations')):
            result = run('curl', '--noproxy', '*', '--silent', '--show-error', '--max-time', '12',
                         '--resolve', DOMAIN + ':443:127.0.0.1', '-o', '/dev/null',
                         '-w', '%{http_code}\n%{redirect_url}', 'https://' + DOMAIN + route).decode()
            if result != '302\nhttps://' + DOMAIN + expected:
                raise ValueError('Application redirect verification failed: ' + route)
    except BaseException as error:
        failures = []
        for path, data in saved.items():
            try:
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, data, modes[path])
            except Exception:
                failures.append(str(path))
        try:
            if not had_link:
                ENABLED.unlink(missing_ok=True)
            if tls_mode is None:
                if TLS.exists():
                    TLS.rmdir()
            else:
                TLS.chmod(tls_mode)
        except Exception:
            failures.append('site link or certificate directory')
        if not failures:
            try:
                run('nginx', '-t')
                run('systemctl', 'reload', 'nginx')
            except Exception:
                failures.append('Nginx configuration validation or reload')
        if failures:
            raise RuntimeError('ROLLBACK_FAILED; inspect private backup: ' + str(backup)
                               + '; targets: ' + ', '.join(failures)) from error
        print('ROLLED_BACK; private backup:', backup)
        raise
    print('NEW_HOST_ENABLED; old virtual host unchanged; private backup:', backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=Path('/data/28042900_www.leanschool.net_nginx.zip'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and (os.name != 'posix' or os.geteuid() != 0):
        raise ValueError('--apply requires root on the deployment server.')
    with validated_bundle(args.archive) as (cert, key):
        print('CERTIFICATE_VALIDATED', DOMAIN)
        if args.apply:
            apply_certificate(cert, key)


if __name__ == '__main__':
    main()
