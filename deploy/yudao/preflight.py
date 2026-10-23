"""Read-only release/runtime checks. Never prints credentials or queries business rows."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import zipfile

REQUIRED_MODULES = {
    'leanclub', 'system', 'infra', 'member', 'report', 'bpm', 'pay', 'mp',
    'product', 'promotion', 'trade', 'statistics', 'crm', 'erp', 'ai',
    'mes', 'wms', 'hrm', 'fms', 'pms', 'oa', 'im',
}

def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def inspect_jar(path):
    with zipfile.ZipFile(path) as jar:
        names = jar.namelist()
        libraries = [n.rsplit('/', 1)[-1] for n in names if n.startswith('BOOT-INF/lib/')]
        modules = sorted({m.group(1) for n in libraries
                          if (m := re.match(r'yudao-module-([a-z]+)-', n))})
        entry = 'BOOT-INF/classes/cn/iocoder/yudao/server/YudaoServerApplication.class'
        if entry not in names:
            raise ValueError('Expected Spring Boot application class is absent')
        header = jar.read(entry)[:8]
        if header[:4] != b'\xca\xfe\xba\xbe':
            raise ValueError('Invalid Java class header')
        major = int.from_bytes(header[6:8], 'big')
        missing = sorted(REQUIRED_MODULES - set(modules))
        if missing:
            raise ValueError('Full-module artifact missing: ' + ', '.join(missing))
        return {'sha256': sha256(path), 'bytes': Path(path).stat().st_size,
                'javaClassMajor': major, 'minimumJava': major - 44,
                'modules': modules, 'libraryCount': len(libraries)}

def load_environment(path=None):
    values = dict(os.environ)
    if path:
        for number, raw in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if not re.match(r'^[A-Z][A-Z0-9_]*=', line):
                raise ValueError(f'Invalid environment entry at line {number}')
            key, value = line.split('=', 1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'':
                value = value[1:-1]
            values[key] = value
    return values

def validate_environment(env):
    failures = []
    for key in ('LS_DB_PASSWORD', 'LS_REDIS_PASSWORD', 'LS_FIELD_ENCRYPTION_KEY'):
        value = env.get(key, '')
        if len(value) < 24 or re.search(r'REPLACE|CHANGEME|password|123456', value, re.I):
            failures.append(f'{key} must be an independent generated secret')
    if len({env.get(k, '') for k in ('LS_DB_PASSWORD', 'LS_REDIS_PASSWORD', 'LS_FIELD_ENCRYPTION_KEY')}) != 3:
        failures.append('Database, Redis and field-encryption secrets must differ')
    if not re.fullmatch(r'[A-Za-z0-9]{32}', env.get('LS_FIELD_ENCRYPTION_KEY', '')):
        failures.append('LS_FIELD_ENCRYPTION_KEY must be 32 random ASCII letters/digits for AES')
    for key in ('LS_DB_NAME', 'LS_DB_USER'):
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,63}', env.get(key, '')):
            failures.append(f'{key} must be a plain lowercase identifier')
    if env.get('LS_DB_USER') == 'root':
        failures.append('Application cannot use MySQL root')
    if env.get('LS_PUBLIC_ORIGIN') != 'https://www.leanschool.net':
        failures.append('LS_PUBLIC_ORIGIN must match the reviewed public origin')
    if env.get('LS_ADMIN_URL') != 'https://www.leanschool.net/console/':
        failures.append('LS_ADMIN_URL must match the reviewed /console/ path')
    if env.get('LS_PUBLIC_TENANT_ID', '1') != '1':
        failures.append('LS_PUBLIC_TENANT_ID must match the reviewed tenant 1 bootstrap')
    for prefix in ('LS_OPERATIONS', 'LS_MEMBER_ORDERS'):
        enabled = env.get(prefix + '_ENABLED', 'false')
        if enabled not in ('true', 'false'):
            failures.append(prefix + '_ENABLED must be true or false')
        if enabled == 'true' and not re.fullmatch(r'[a-f0-9]{64}', env.get(prefix + '_SECRET', '')):
            failures.append(prefix + '_SECRET must be 64 lowercase hex characters')
    if env.get('LS_OPERATIONS_ENABLED') == env.get('LS_MEMBER_ORDERS_ENABLED') == 'true':
        if env.get('LS_OPERATIONS_SECRET') == env.get('LS_MEMBER_ORDERS_SECRET'):
            failures.append('Operation and member-order signing domains must use separate secrets')
    # Avoid environment precedence silently undoing the reviewed YAML/profile.
    banned = [k for k in env if k.startswith(('SPRING_', 'YUDAO_', 'LEANCLUB_'))]
    if banned:
        failures.append('Unexpected Spring/Yudao/Leanclub override names: ' + ', '.join(sorted(banned)))
    if any(k in env for k in ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS')):
        failures.append('Remove unreviewed JVM injection environment variables')
    opts = env.get('JAVA_OPTS', '')
    if '--' in opts or re.search(r'-D(?:spring|yudao|leanclub)\.', opts):
        failures.append('JAVA_OPTS cannot override Spring application properties')
    if failures:
        raise ValueError('; '.join(failures))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--java', type=Path)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--check-local-services', action='store_true')
    args = parser.parse_args()
    report = inspect_jar(args.jar)
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        if manifest['artifact']['sha256'] != report['sha256']:
            raise ValueError('JAR SHA-256 differs from the release manifest')
        if not re.fullmatch(r'[0-9a-f]{40}', manifest.get('sourceCommit', '')):
            raise ValueError('Manifest has no immutable source commit')
        report['sourceCommit'] = manifest['sourceCommit']
    elif args.production:
        raise ValueError('Production requires a release manifest')
    if args.java:
        result = subprocess.run([str(args.java), '-version'], capture_output=True, text=True, check=True)
        match = re.search(r'version "(\d+)', result.stdout + result.stderr)
        if not match or int(match.group(1)) != 25:
            raise ValueError('This release requires the reviewed Java 25 runtime')
        report['runtimeJava'] = int(match.group(1))
    elif args.production:
        raise ValueError('Production requires an explicit Java runtime path')
    env = load_environment(args.env_file)
    if args.production or args.env_file:
        validate_environment(env)
        report['environment'] = 'validated; values withheld'
    if args.check_local_services:
        for label, key, default in [('mysql', 'LS_DB_PORT', 3306), ('redis', 'LS_REDIS_PORT', 6382)]:
            with socket.create_connection(('127.0.0.1', int(env.get(key, default))), timeout=3):
                pass
        report['localPorts'] = 'reachable; authentication/schema not checked'
    print(json.dumps({'ok': True, **report}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        # No database response, command output or secret values are included.
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
