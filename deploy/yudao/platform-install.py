"""Full-platform Linux installer with explicit plan/prepare/init-db/activate phases.

No apt, no global MySQL/Redis edits, no web gateway or website changes.
No credentials, SQL bodies or subprocess error output are printed.
Linux execution is required for mutations; this tool must pass target-host staging.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from preflight import inspect_jar, sha256, validate_environment

HERE = Path(__file__).resolve().parent
OPT = Path('/opt/leanschool')
ETC = Path('/etc/leanschool')
DATA = Path('/var/lib/leanschool')
LOG = Path('/var/log/leanschool')
STATE = ETC / 'platform-state.json'

def run(argv, data=None, allow_failure=False):
    result = subprocess.run([str(a) for a in argv], input=data, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode and not allow_failure:
        raise RuntimeError(f'{Path(str(argv[0])).name} failed with exit {result.returncode}; output withheld')
    return result

def private_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular private file: ' + str(path))
    if os.name == 'posix':
        mode = path.stat()
        if mode.st_uid != 0 or stat.S_IMODE(mode.st_mode) & 0o077:
            raise ValueError('Private input must be root-owned and mode 0600: ' + str(path))

def require_root():
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise RuntimeError('Mutations require root on Linux; no changes applied')

def write_atomic(path, content, mode=0o600):
    if path.is_symlink():
        raise ValueError('Refusing symlink destination: ' + str(path))
    temp = path.with_name(path.name + '.new-' + secrets.token_hex(5))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    os.chmod(temp, mode)
    os.replace(temp, path)

def write_json(path, value):
    write_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def archive_entries(path, prefix=''):
    prefix_parts = PurePosixPath(prefix.strip('/')).parts if prefix.strip('/') else ()
    result = []
    archive = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else tarfile.open(path, 'r:*')
    try:
        entries = archive.infolist() if isinstance(archive, zipfile.ZipFile) else archive.getmembers()
        total = 0
        for entry in entries:
            name = entry.filename if isinstance(archive, zipfile.ZipFile) else entry.name
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe UI archive member path')
            directory = entry.is_dir() if isinstance(archive, zipfile.ZipFile) else entry.isdir()
            special = (stat.S_ISLNK(entry.external_attr >> 16) if isinstance(archive, zipfile.ZipFile)
                       else not (entry.isfile() or entry.isdir()))
            if special:
                raise ValueError('UI archive cannot contain symlinks, hardlinks or devices')
            if directory:
                continue
            if p.parts[:len(prefix_parts)] != prefix_parts:
                raise ValueError('UI archive contains files outside the explicit --ui-prefix')
            relative = PurePosixPath(*p.parts[len(prefix_parts):])
            if not relative.parts or any(x in ('.git', 'node_modules') for x in relative.parts):
                raise ValueError('UI archive contains development/repository files')
            if relative.name.startswith('.env') or relative.suffix.lower() in ('.pem', '.key', '.sqlite', '.sqlite3'):
                raise ValueError('UI archive contains forbidden private/configuration files')
            size = entry.file_size if isinstance(archive, zipfile.ZipFile) else entry.size
            total += size
            if total > 2 * 1024**3 or len(result) >= 50000:
                raise ValueError('UI archive exceeds reviewed extraction limits')
            result.append((relative.as_posix(), entry, size))
        paths = [r[0] for r in result]
        if len(paths) != len(set(paths)) or 'index.html' not in paths:
            raise ValueError('UI archive needs one unambiguous index.html at its selected root')
        item = next(e for rel, e, _ in result if rel == 'index.html')
        raw = archive.read(item) if isinstance(archive, zipfile.ZipFile) else archive.extractfile(item).read()
        html = raw.decode('utf-8')
        if '/console/' not in html or 'localhost:48080' in html or '/@vite/client' in html:
            raise ValueError('UI must be a production build with /console/ base, not a Vite dev page')
        return archive, result, total
    except Exception:
        archive.close()
        raise

def artifacts(args):
    report = inspect_jar(args.jar)
    if report['javaClassMajor'] != 69:
        raise ValueError('Expected the reviewed Java 25 artifact')
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if report['sha256'] != manifest['artifact']['sha256'] or not re.fullmatch(r'[a-f0-9]{40}', manifest.get('sourceCommit', '')):
        raise ValueError('Java artifact does not match its immutable source manifest')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', args.ui_sha256) or sha256(args.ui_archive) != args.ui_sha256.lower():
        raise ValueError('UI artifact SHA-256 mismatch')
    archive, entries, size = archive_entries(args.ui_archive, args.ui_prefix)
    archive.close()
    seed_path = args.seed_dir / 'schema-manifest.json'
    seed = json.loads(seed_path.read_text(encoding='utf-8'))
    required = {'001-schema.sql', '002-dictionaries-menus.sql', '003-flowable8-schema.sql',
                '004-leanclub-permissions.sql', '005-schools.sql', '006-site-draft.sql', 'site-content.ready.json'}
    if not required.issubset(seed['files']):
        raise ValueError('Incomplete full product schema pack')
    for name, digest in seed['files'].items():
        if PurePosixPath(name).name != name or (args.seed_dir / name).is_symlink():
            raise ValueError('Seed manifest may reference only regular files in its directory')
        if sha256(args.seed_dir / name) != digest:
            raise ValueError('Seed artifact digest mismatch: ' + name)
    if seed.get('productSeed', {}).get('schools', {}).get('count') != 2952:
        raise ValueError('Expected verified 2952-school product seed')
    return {'sourceCommit': manifest['sourceCommit'], 'jar': report,
            'uiSha256': args.ui_sha256.lower(), 'uiFiles': len(entries), 'uiExtractedBytes': size,
            'schemaSha256': sha256(seed_path), 'schemaTableCount': seed['tableCount']}

def mysql_args(admin_file=None):
    command = ['/usr/bin/mysql']
    if admin_file:
        private_file(Path(admin_file)); command.append('--defaults-extra-file=' + str(admin_file))
    else:
        command.extend(['--no-defaults', '--user=root', '--protocol=socket'])
    return command + ['--batch', '--skip-column-names', '--default-character-set=utf8mb4']

def mysql(sql, admin_file=None, database=None):
    args = mysql_args(admin_file)
    if database:
        args.append(database)
    return run(args, sql).stdout.strip()

def resources(args):
    result = {'platform': platform.system(), 'processors': os.cpu_count(),
              'pythonBcryptAvailable': importlib.util.find_spec('bcrypt') is not None}
    if platform.system() != 'Linux':
        result['runtimeChecked'] = False
        return result
    mem = Path('/proc/meminfo').read_text()
    result['availableMemoryMiB'] = int(re.search(r'MemAvailable:\s+(\d+)', mem)[1]) // 1024
    result['freeDiskMiB'] = shutil.disk_usage('/opt').free // 1024**2
    java = run([args.java, '-version'])
    if not re.search(r'version "25(?:\.|\")', java.stdout + java.stderr):
        raise ValueError('Installed runtime must be the reviewed Java 25')
    for program in ('/usr/bin/mysql', '/usr/bin/redis-server', '/usr/bin/systemctl'):
        if not Path(program).is_file(): raise ValueError('Required installed program missing: ' + program)
    version = mysql('SELECT VERSION();', args.mysql_admin_file)
    mysql_version = re.match(r'^(8)\.(\d+)\.(\d+)', version)
    if not mysql_version or tuple(map(int, mysql_version.groups())) < (8, 0, 16) or 'mariadb' in version.lower():
        raise ValueError('This release requires installed MySQL 8.0.16+ with enforced CHECK constraints')
    redis = run(['/usr/bin/redis-server', '--version']).stdout
    match = re.search(r'v=(\d+)\.(\d+)\.', redis)
    if not match or tuple(map(int, match.groups())) < (6, 2):
        raise ValueError('Dedicated Redis requires installed Redis 6.2+ channel ACL support')
    result['mysqlVersion'] = version
    result['mysqlPort'] = int(mysql('SELECT @@port;', args.mysql_admin_file))
    with socket.create_connection(('127.0.0.1', result['mysqlPort']), timeout=3): pass
    result['redisMajor'] = int(match[1])
    result['services'] = {name: run(['/usr/bin/systemctl', 'is-active', name], allow_failure=True).stdout.strip()
                          for name in ('mysql', 'redis-server', 'leanschool-java', 'leanschool-redis')}
    result['runtimeChecked'] = True
    return result

def owned_dirs():
    for path in (OPT, ETC, DATA, LOG):
        if path.is_symlink(): raise ValueError('Unexpected symlink at platform root: ' + str(path))
    if not STATE.exists():
        for path in (ETC / 'java.env', ETC / 'platform-secrets.json', ETC / 'redis.conf', OPT / 'java/current', OPT / 'ui/current'):
            if path.exists() or path.is_symlink():
                raise ValueError('Unregistered existing installation needs manual review: ' + str(path))
    for path in (OPT, ETC, DATA, LOG, OPT/'java', OPT/'ui', OPT/'java/releases', OPT/'ui/releases', DATA/'install', DATA/'redis', DATA/'java'):
        if path.is_symlink(): raise ValueError('Unexpected symlink in owned installation directories')
        path.mkdir(parents=True, exist_ok=True)
    for path in (OPT, OPT/'java', OPT/'ui', OPT/'java/releases', OPT/'ui/releases'):
        os.chmod(path, 0o755)
    os.chmod(DATA, 0o711)
    for path in (DATA/'install', DATA/'redis', DATA/'java'):
        os.chmod(path, 0o700)
    os.chmod(LOG, 0o750)
    os.chmod(ETC, 0o700)

def env_from_secrets(values, state):
    return {'LS_DB_NAME': state['database'], 'LS_DB_PORT': str(state['dbPort']), 'LS_DB_USER': state['dbUser'],
            'LS_DB_PASSWORD': values['dbPassword'], 'LS_REDIS_PORT': str(state['redisPort']),
            'LS_REDIS_DB': '0', 'LS_REDIS_PASSWORD': values['redisPassword'],
            'LS_FIELD_ENCRYPTION_KEY': values['fieldEncryptionKey'],
            'LS_PUBLIC_ORIGIN': 'https://www.leanschool.net', 'LS_ADMIN_URL': 'https://www.leanschool.net/console/',
            'LS_PUBLIC_TENANT_ID': '1', 'LS_OPERATIONS_ENABLED': 'false',
            'LS_OPERATIONS_SECRET': values['operationsSecret'], 'LS_MEMBER_ORDERS_ENABLED': 'false',
            'LS_MEMBER_ORDERS_SECRET': values['memberOrdersSecret'],
            'JAVA_OPTS': f'-Xms256m -Xmx{state["javaXmxMiB"]}m -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8 -Djava.awt.headless=true'}

def prepare(args, report, host):
    require_root()
    if not host['pythonBcryptAvailable']: raise ValueError('Install a reviewed Python bcrypt package before preparation')
    if host['availableMemoryMiB'] < args.java_xmx_mib + 1024:
        raise ValueError('Insufficient current available RAM for selected Java heap plus 1 GiB headroom; review sizing first')
    needed = (args.jar.stat().st_size * 3 + report['uiExtractedBytes'] * 2) // 1024**2 + 1024
    if host['freeDiskMiB'] < needed: raise ValueError('Insufficient free disk for release and rollback copy')
    owned_dirs()
    if STATE.exists(): private_file(STATE)
    previous_state = json.loads(STATE.read_text()) if STATE.exists() else None
    for name in ('leanschool-java.service', 'leanschool-redis.service'):
        if Path('/etc/systemd/system', name).exists() and not previous_state:
            raise ValueError('Existing unregistered platform service needs manual review')
    for label in (args.database, args.db_user):
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,31}', label): raise ValueError('Database/user identifier is invalid')
    state = {**report, 'database': args.database, 'dbUser': args.db_user, 'dbPort': host['mysqlPort'], 'redisPort': args.redis_port,
             'javaPath': str(args.java.resolve()), 'javaXmxMiB': args.java_xmx_mib,
             'preparedAtUtc': datetime.now(timezone.utc).isoformat()}
    if previous_state:
        for key in ('database', 'dbUser', 'dbPort', 'redisPort'):
            if state[key] != previous_state[key]: raise ValueError('Cannot change existing owned identity: ' + key)
    else:
        for port in (args.redis_port, 48080, 48081):
            with socket.socket() as sock:
                try: sock.bind(('127.0.0.1', port))
                except OSError: raise ValueError(f'Port {port} is already owned by another process') from None
    account = run(['/usr/bin/id', '-u', 'leanschool'], allow_failure=True)
    if account.returncode:
        run(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir', str(DATA/'java'), '--shell', '/usr/sbin/nologin', 'leanschool'])
    elif not previous_state:
        raise ValueError('Existing leanschool OS account is not registered to this installer')
    run(['/usr/bin/chown', 'leanschool:leanschool', str(LOG), str(DATA/'redis'), str(DATA/'java')])
    secret_file = ETC/'platform-secrets.json'
    if secret_file.exists():
        private_file(secret_file); values = json.loads(secret_file.read_text())
    else:
        alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        values = {'dbPassword': secrets.token_hex(32), 'redisPassword': secrets.token_hex(32),
                  'fieldEncryptionKey': ''.join(secrets.choice(alphabet) for _ in range(32)),
                  'operationsSecret': secrets.token_hex(32), 'memberOrdersSecret': secrets.token_hex(32)}
        write_json(secret_file, values)
    env = env_from_secrets(values, state); validate_environment(env)
    # First prepare creates config. Subsequent releases preserve the live bridge switches.
    if not previous_state:
        write_atomic(ETC/'java.env', ''.join(k+'='+v+'\n' for k,v in env.items()))
        write_atomic(ETC/'bridge-secrets.env', 'LS_OPERATIONS_SECRET='+values['operationsSecret']+'\nLS_MEMBER_ORDERS_SECRET='+values['memberOrdersSecret']+'\n')
        write_atomic(ETC/'application-prod.yaml', (HERE/'application-prod.yaml').read_text(encoding='utf-8'), 0o644)
        acl_hash = hashlib.sha256(values['redisPassword'].encode()).hexdigest()
        # Redis config contains only a one-way ACL hash; the plaintext secret stays root-only.
        write_atomic(ETC/'redis.conf', f'bind 127.0.0.1\nport {args.redis_port}\nprotected-mode yes\ndaemonize no\ndir {DATA}/redis\nappendonly yes\nappendfsync everysec\nmaxmemory 256mb\nmaxmemory-policy noeviction\nuser default on #{acl_hash} ~* &* +@all -config -shutdown -module -acl\n', 0o640)
        run(['/usr/bin/chown', 'root:leanschool', str(ETC/'redis.conf')])
    # Permit the service to traverse the directory but not read root-only secret files.
    os.chmod(ETC, 0o711)
    release = OPT/'java/releases'/(report['sourceCommit']+'-'+report['jar']['sha256'][:12])
    if release.is_symlink(): raise ValueError('Java release cannot be a symlink')
    release.mkdir(exist_ok=True)
    os.chmod(release, 0o755)
    for src, name in ((args.jar, 'yudao-server.jar'), (args.manifest, 'release.json')):
        target = release/name
        if target.is_symlink(): raise ValueError('Java artifact cannot be a symlink')
        if target.exists() and sha256(target) != sha256(src): raise ValueError('Existing immutable release differs')
        if not target.exists(): shutil.copyfile(src, target); os.chmod(target, 0o644)
    ui_release = OPT/'ui/releases'/report['uiSha256'][:16]
    if ui_release.is_symlink(): raise ValueError('UI release cannot be a symlink')
    if not ui_release.exists():
        stage = ui_release.with_name(ui_release.name+'.staging-'+secrets.token_hex(4)); (stage/'console').mkdir(parents=True)
        archive, entries, _ = archive_entries(args.ui_archive, args.ui_prefix)
        try:
            for name, entry, _ in entries:
                target = stage/'console'/name; target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.open(entry) if isinstance(archive, zipfile.ZipFile) else archive.extractfile(entry)
                with stream, target.open('xb') as output: shutil.copyfileobj(stream, output)
                os.chmod(target, 0o644)
        finally: archive.close()
        write_atomic(stage/'artifact.sha256', report['uiSha256']+'\n', 0o644)
        for directory, _, _ in os.walk(stage): os.chmod(directory, 0o755)
        os.replace(stage, ui_release)
    elif (ui_release/'artifact.sha256').read_text().strip() != report['uiSha256']:
        raise ValueError('Existing UI release digest marker differs')
    seed_target = DATA/'install'/report['schemaSha256']
    if seed_target.is_symlink(): raise ValueError('Seed directory cannot be a symlink')
    if not seed_target.exists():
        seed_target.mkdir(mode=0o700)
        approved = json.loads((args.seed_dir/'schema-manifest.json').read_text())['files']
        for name in [*approved, 'schema-manifest.json']:
            shutil.copyfile(args.seed_dir/name, seed_target/name); os.chmod(seed_target/name, 0o600)
    deploy = OPT/'deploy'
    if deploy.is_symlink(): raise ValueError('Deploy tool directory cannot be a symlink')
    deploy.mkdir(exist_ok=True); os.chmod(deploy, 0o755)
    for name in ('preflight.py', 'prepare-bootstrap.py', 'platform-install.py'):
        shutil.copyfile(HERE/name, deploy/name); os.chmod(deploy/name, 0o644)
    for name in ('leanschool-java.service', 'leanschool-redis.service'):
        unit = (HERE/name).read_text(encoding='utf-8').replace('/opt/leanschool/jdk/bin/java', state['javaPath'])
        write_atomic(Path('/etc/systemd/system')/name, unit, 0o644)
    run(['/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/leanschool-java.service', '/etc/systemd/system/leanschool-redis.service'])
    state.update(javaRelease=str(release), uiRelease=str(ui_release), seedDir=str(seed_target))
    write_json(STATE, state)
    run(['/usr/bin/systemctl', 'daemon-reload'])
    return {'prepared': True, 'javaRelease': str(release), 'uiRelease': str(ui_release),
            'databaseInitialized': False, 'servicesStarted': False, 'secretValues': 'withheld'}

def load_state():
    require_root(); private_file(STATE)
    state = json.loads(STATE.read_text())
    if any(not re.fullmatch(r'[a-z][a-z0-9_]{2,31}',state[key]) for key in ('database','dbUser')):
        raise ValueError('Invalid owned database identity')
    for key, root in (('javaRelease', OPT/'java/releases'), ('uiRelease', OPT/'ui/releases'), ('seedDir', DATA/'install')):
        path = Path(state[key])
        if path.is_symlink() or path.resolve().parent != root.resolve(): raise ValueError('Unexpected owned release path')
    return state

def init_db(args):
    state = load_state(); seed = Path(state['seedDir']); db=state['database']; user=state['dbUser']
    manifest = json.loads((seed/'schema-manifest.json').read_text())
    for name,digest in manifest['files'].items():
        if sha256(seed/name) != digest: raise ValueError('Private seed changed after preparation')
    marker = ETC/'schema-ready.json'
    if marker.exists():
        ready=json.loads(marker.read_text())
        if ready.get('schemaSha256') != state['schemaSha256'] or ready.get('database') != db:
            raise ValueError('Existing schema needs an explicit upgrade migration, not reinitialization')
        actual={name.lower() for name in mysql(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{db}';",args.mysql_admin_file).splitlines()}
        if not {name.lower() for name in manifest['tables']}.issubset(actual): raise ValueError('Previously initialized database is missing required tables')
        return {'initialized': True, 'alreadyInitialized': True, 'database': db}
    count=int(mysql(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{db}';",args.mysql_admin_file))
    if count: raise ValueError('Target schema is not empty; no data was overwritten')
    exists=int(mysql(f"SELECT COUNT(*) FROM mysql.user WHERE User='{user}';",args.mysql_admin_file))
    if exists: raise ValueError('Production application database user already exists; credentials will not be overwritten')
    private_file(args.admin_password_file)
    private_file(ETC/'platform-secrets.json')
    values=json.loads((ETC/'platform-secrets.json').read_text()); password=values['dbPassword']
    if not re.fullmatch(r'[a-f0-9]{64}',password): raise ValueError('Unexpected generated database secret format')
    bootstrap=ETC/'bootstrap.private.sql'
    if bootstrap.exists(): raise ValueError('Existing private bootstrap SQL needs explicit review')
    run([sys.executable, HERE/'prepare-bootstrap.py','--username',args.admin_username,
         '--tenant-expiry',args.tenant_expiry,'--password-file',args.admin_password_file,'--output',bootstrap])
    sql=f'CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n'
    for host in ('localhost','127.0.0.1'):
        sql += f"CREATE USER '{user}'@'{host}' IDENTIFIED BY '{password}';\nGRANT SELECT,INSERT,UPDATE,DELETE ON `{db}`.* TO '{user}'@'{host}';\n"
    mysql(sql,args.mysql_admin_file)
    for name in sorted(n for n in manifest['files'] if n.endswith('.sql')):
        mysql((seed/name).read_text(encoding='utf-8'),args.mysql_admin_file,db)
    mysql(bootstrap.read_text(encoding='utf-8'),args.mysql_admin_file,db)
    checks={'tables':int(mysql(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{db}';",args.mysql_admin_file)),
            'schools':int(mysql('SELECT COUNT(*) FROM leanclub_school;',args.mysql_admin_file,db)),
            'admins':int(mysql('SELECT COUNT(*) FROM system_users;',args.mysql_admin_file,db)),
            'channels':int(mysql('SELECT (SELECT COUNT(*) FROM pay_channel)+(SELECT COUNT(*) FROM system_sms_channel)+(SELECT COUNT(*) FROM system_mail_account);',args.mysql_admin_file,db))}
    if checks != {'tables':manifest['tableCount'],'schools':2952,'admins':1,'channels':0}:
        raise ValueError('Fresh database counts did not match the reviewed initialization; inspect privately')
    write_json(marker, {'database':db,'schemaSha256':state['schemaSha256'],'checks':checks,'initializedAtUtc':datetime.now(timezone.utc).isoformat()})
    return {'initialized':True,'database':db,'checks':checks,'servicesStarted':False}

def swap_link(link,target):
    if link.exists() and not link.is_symlink(): raise ValueError('Current path is not an installer symlink')
    temp=link.with_name(link.name+'.new-'+secrets.token_hex(4)); temp.symlink_to(target,target_is_directory=True);os.replace(temp,link)

def activate(args):
    state=load_state()
    if not (ETC/'schema-ready.json').exists(): raise ValueError('Initialize and verify the clean schema before activation')
    ready=json.loads((ETC/'schema-ready.json').read_text())
    if ready.get('schemaSha256')!=state['schemaSha256'] or ready.get('database')!=state['database']:
        raise ValueError('Candidate schema differs from initialized schema; explicit migration review required')
    links={'java':OPT/'java/current','ui':OPT/'ui/current'}
    previous={name:str(link.resolve()) if link.is_symlink() else None for name,link in links.items()}
    for name,value in previous.items():
        if value and Path(value).parent != OPT/name/'releases': raise ValueError('Current symlink points outside owned releases')
    swap_link(links['java'],state['javaRelease'])
    try:
        run(['/usr/bin/systemctl','enable','--now','leanschool-redis.service'])
        run(['/usr/bin/systemctl','restart','leanschool-java.service'])
        deadline=time.monotonic()+300; healthy=False
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic()<deadline:
            try:
                with opener.open('http://127.0.0.1:48081/actuator/health',timeout=3) as response:
                    healthy=response.status==200 and json.load(response).get('status')=='UP'
                if healthy: break
            except (OSError,ValueError): pass
            time.sleep(3)
        if not healthy: raise RuntimeError('Java did not become healthy within 300 seconds')
        swap_link(links['ui'],state['uiRelease'])
        run(['/usr/bin/systemctl','enable','leanschool-java.service'])
        write_json(ETC/'previous-release.json',previous)
        return {'activated':True,'javaRelease':state['javaRelease'],'uiRelease':state['uiRelease'],
                'health':'UP','businessAcceptance':'still required; gateway untouched'}
    except Exception:
        if previous['java']:
            swap_link(links['java'],previous['java']);run(['/usr/bin/systemctl','restart','leanschool-java.service'],allow_failure=True)
        else:
            run(['/usr/bin/systemctl','stop','leanschool-java.service'],allow_failure=True)
            if links['java'].is_symlink(): links['java'].unlink()
        if previous['ui']: swap_link(links['ui'],previous['ui'])
        elif links['ui'].is_symlink(): links['ui'].unlink()
        raise

def rollback(args):
    state=load_state();record=ETC/'previous-release.json';private_file(record)
    previous=json.loads(record.read_text());targets={}
    for name in ('java','ui'):
        if not previous.get(name): raise ValueError('No complete previous release was recorded')
        target=Path(previous[name]);root=OPT/name/'releases'
        if target.is_symlink() or target.resolve().parent!=root.resolve() or not target.is_dir():
            raise ValueError('Previous release is outside the owned release directory')
        targets[name]=target
    for name,target in targets.items():swap_link(OPT/name/'current',target)
    run(['/usr/bin/systemctl','restart','leanschool-java.service'])
    return {'codeRollbackRequested':True,'databaseRestored':False,
            'requiredNextCheck':'Verify health and business paths; database contents are preserved'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    for name in ('plan','prepare'):
        p=commands.add_parser(name)
        for option in ('jar','manifest','ui-archive','seed-dir'):
            p.add_argument('--'+option,type=Path,required=True)
        p.add_argument('--ui-sha256',required=True);p.add_argument('--ui-prefix',default='')
        p.add_argument('--java',type=Path,default=Path('/opt/leanschool/jdk/bin/java'))
        p.add_argument('--mysql-admin-file',type=Path)
        p.add_argument('--database',default='leanschool');p.add_argument('--db-user',default='leanschool_app')
        p.add_argument('--redis-port',type=int,default=6382);p.add_argument('--java-xmx-mib',type=int,default=2304)
    p=commands.add_parser('init-db');p.add_argument('--mysql-admin-file',type=Path)
    p.add_argument('--admin-username',required=True);p.add_argument('--tenant-expiry',required=True)
    p.add_argument('--admin-password-file',type=Path,required=True)
    commands.add_parser('activate')
    commands.add_parser('rollback')
    args=parser.parse_args()
    if args.command in ('plan','prepare'):
        if not 1024<=args.redis_port<=65535 or args.redis_port in (48080,48081,5381,3306): raise ValueError('Invalid dedicated Redis port')
        if not 1024<=args.java_xmx_mib<=16384: raise ValueError('Review Java heap between 1024 and 16384 MiB')
        report=artifacts(args);host=resources(args)
        result={'artifacts':report,'host':host,'changesApplied':False} if args.command=='plan' else prepare(args,report,host)
    elif args.command=='init-db':result=init_db(args)
    elif args.command=='activate':result=activate(args)
    else:result=rollback(args)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,KeyError,zipfile.BadZipFile,tarfile.TarError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
