"""Prepare public product dictionaries and an unpublished brand draft from source files."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re

def literal(value):
    return "CONVERT(X'" + str(value).encode('utf-8').hex() + "' USING utf8mb4)"

def schools_from_source(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    raw_file = directory / manifest['filename']
    if raw_file.resolve().parent != directory.resolve():
        raise ValueError('School source filename must remain inside its source directory')
    if hashlib.sha256(raw_file.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Official school XLS digest mismatch')
    data = (directory / 'schools.json').read_bytes()
    schools = json.loads(data)
    if len(schools) != 2952 or len({s['code'] for s in schools}) != 2952:
        raise ValueError('Expected exactly 2952 unique school codes')
    if dict(Counter(s['level'] for s in schools)) != manifest['levels']:
        raise ValueError('School level counts differ from source manifest')
    if dict(Counter(s['province'] for s in schools)) != manifest['provinces']:
        raise ValueError('School province counts differ from source manifest')
    columns = ('code', 'name', 'province', 'city', 'level', 'aliases', 'source', 'source_url', 'tenant_id', 'creator', 'updater')
    lines = ['SET NAMES utf8mb4;', 'START TRANSACTION;']
    for school in schools:
        if not re.fullmatch(r'\d{10}', school['code']):
            raise ValueError('Malformed school code')
        values = [school['code'], school['name'], school['province'], school['city'], school['level'],
                  school['aliases'], school['source'], school['sourceUrl'], '1', 'catalog-import', 'catalog-import']
        if any(any(ord(c) < 32 for c in value) for value in values):
            raise ValueError('Control character in school data')
        lines.append('INSERT INTO leanclub_school (' + ','.join(columns) + ') VALUES (' +
                     ','.join(literal(v) for v in values) + ');')
    lines.append('COMMIT;')
    return '\n'.join(lines) + '\n', {'count': len(schools), 'sourceXlsSha256': manifest['sha256'],
                                            'schoolsJsonSha256': hashlib.sha256(data).hexdigest()}

def brand_draft(path):
    raw = Path(path).read_bytes()
    content = json.loads(raw)
    if content.get('schemaVersion') != 1 or content.get('footer', {}).get('brand') != 'LEAN SCHOOL':
        raise ValueError('Expected reviewed LEAN SCHOOL v1 brand seed')
    content = copy.deepcopy(content)
    # User confirmed this domain has no website filing yet. Do not present the
    # old domain's filing as evidence for the new domain; do not invent a number.
    content['footer']['icpText'] = ''
    encoded = json.dumps(content, ensure_ascii=False, separators=(',', ':'))
    sql = ('SET NAMES utf8mb4;\nSTART TRANSACTION;\n'
           'INSERT INTO leanclub_site (site_key,version,draft_json,published_revision_id,tenant_id,creator,updater) '
           "VALUES ('main',1," + literal(encoded) + ",NULL,1,'release-seed','release-seed');\nCOMMIT;\n")
    return sql, content, {'sourceSha256': hashlib.sha256(raw).hexdigest(),
                         'mode': 'unpublished draft; publish through versioned CMS after review',
                         'change': 'new-domain draft icpText empty until its actual filing is ready'}

def prepare_product(output, permissions=None, school_dir=None, site_seed=None):
    files, report = [], {}
    if permissions:
        data = Path(permissions).read_bytes()
        # This is this repository's fixed reviewed migration, not general SQL input.
        text = data.decode('utf-8-sig')
        if re.search(r'^\s*(?:UPDATE|DELETE|DROP|TRUNCATE)\b|system_role_menu',
                     '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('--')), re.I | re.M):
            raise ValueError('Permissions migration contains unexpected mutations')
        name = '004-leanclub-permissions.sql'
        (output / name).write_text(text, encoding='utf-8')
        files.append(name)
        report['permissions'] = {'sha256': hashlib.sha256(data).hexdigest(),
                                 'definitions': len(set(re.findall(r"'leanclub:[a-z]+:[a-z]+'", text)))}
    if school_dir:
        sql, report['schools'] = schools_from_source(school_dir)
        name = '005-schools.sql'
        (output / name).write_text(sql, encoding='utf-8')
        files.append(name)
    if site_seed:
        sql, content, report['brand'] = brand_draft(site_seed)
        name = '006-site-draft.sql'
        (output / name).write_text(sql, encoding='utf-8')
        files.append(name)
        name = 'site-content.ready.json'
        (output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
        files.append(name)
    return files, report
