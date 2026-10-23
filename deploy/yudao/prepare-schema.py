"""Prepare a NEW-database schema pack, never connect to or dump a running database.

Inputs are explicitly chosen SQL source files. Output is a private deployment
artifact, not permission to redistribute upstream paid/companion SQL materials.
Only CREATE TABLE and narrowly allowed dictionary/menu VALUES inserts survive.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile
from product_seed import prepare_product

SEED_TABLES = frozenset({'system_menu', 'system_dict_type', 'system_dict_data'})

def input_sql(path):
    data = path.read_bytes()
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [n for n in archive.namelist() if n.lower().endswith('.sql') and not n.startswith('__MACOSX/')]
            if len(entries) != 1:
                raise ValueError('Expected exactly one SQL file in ' + path.name)
            data = archive.read(entries[0])
    return data

def statements(text):
    """SQL splitter for MySQL dumps; quoted semicolons and escapes are preserved."""
    out, chunk, quote, index = [], [], None, 0
    while index < len(text):
        char = text[index]
        if quote:
            chunk.append(char)
            if char == '\\' and quote != '`' and index + 1 < len(text):
                index += 1
                chunk.append(text[index])
            elif char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                    chunk.append(text[index])
                else:
                    quote = None
            index += 1
            continue
        if char in "'\"`":
            quote = char
            chunk.append(char)
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            if end < 0:
                raise ValueError('Unclosed SQL block comment')
            # This also removes MySQL executable /*! ... */ dump wrappers.
            chunk.append(' ')
            index = end + 2
            continue
        elif char == '#' or (text.startswith('--', index) and
                              (index + 2 == len(text) or text[index + 2].isspace())):
            end = text.find('\n', index)
            index = len(text) if end < 0 else end + 1
            chunk.append('\n')
            continue
        elif char == ';':
            statement = ''.join(chunk).strip()
            if statement:
                out.append(statement)
            chunk = []
        else:
            chunk.append(char)
        index += 1
    if quote:
        raise ValueError('Unclosed SQL string')
    if ''.join(chunk).strip():
        out.append(''.join(chunk).strip())
    return out

def collect(paths):
    tables, seeds, sources, skipped = {}, [], [], Counter()
    for path in paths:
        data = input_sql(path)
        sources.append({'filename': path.name, 'sha256': hashlib.sha256(data).hexdigest()})
        for sql in statements(data.decode('utf-8-sig')):
            create = re.match(r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z0-9_]+)`?\s*\(', sql, re.I)
            insert = re.match(r'^INSERT\s+INTO\s+`?([A-Za-z0-9_]+)`?\s*(?:\([^)]*\)\s*)?VALUES\s*\(', sql, re.I | re.S)
            if create:
                name = create.group(1)
                normalized = re.sub(r'\bAUTO_INCREMENT\s*=\s*\d+', '', sql, flags=re.I)
                if name in tables and tables[name] != normalized:
                    raise ValueError('Conflicting CREATE TABLE definitions for ' + name)
                tables[name] = normalized
            elif insert and insert.group(1) in SEED_TABLES:
                if sql not in seeds:
                    seeds.append(sql)
            else:
                kind = re.match(r'[A-Za-z]+', sql)
                skipped[kind.group().upper() if kind else 'OTHER'] += 1
    if not tables:
        raise ValueError('No CREATE TABLE statements found')
    for table in SEED_TABLES:
        if any(re.match(r'^INSERT\s+INTO\s+`?' + table + r'`?\b', sql, re.I) for sql in seeds) and table not in tables:
            raise ValueError('Seed table has no reviewed definition: ' + table)
    return tables, seeds, sources, skipped

def ordered_tables(tables):
    """Create referenced parents first without disabling FK checks for the session."""
    lookup = {name.lower(): name for name in tables}
    dependencies = {}
    for name, sql in tables.items():
        parents = set()
        for reference in re.findall(r'\bREFERENCES\s+`?([A-Za-z0-9_]+)`?\s*\(', sql, re.I):
            if reference.lower() not in lookup:
                raise ValueError('Missing referenced table for ' + name + ': ' + reference)
            parent = lookup[reference.lower()]
            if parent != name: parents.add(parent)
        dependencies[name] = parents
    ordered, complete = [], set()
    while len(ordered) < len(tables):
        ready = [name for name in tables if name not in complete and dependencies[name] <= complete]
        if not ready:
            raise ValueError('Circular table foreign keys require an explicit reviewed migration')
        for name in ready:
            ordered.append(tables[name]); complete.add(name)
    return ordered

def flowable_schema(jar_path):
    specs = [
        ('flowable-engine-common', 'org/flowable/common/db/create/flowable.mysql.create.common.sql'),
        ('flowable-engine', 'org/flowable/db/create/flowable.mysql.create.engine.sql'),
        ('flowable-engine', 'org/flowable/db/create/flowable.mysql.create.history.sql'),
        ('flowable-event-registry', 'org/flowable/eventregistry/db/create/flowable.mysql.create.eventregistry.sql'),
        ('flowable-idm-engine', 'org/flowable/idm/db/create/flowable.mysql.create.identity.sql'),
    ]
    statements_out, tables, sources = [], [], []
    with zipfile.ZipFile(jar_path) as jar:
        for library, entry in specs:
            name = f'BOOT-INF/lib/{library}-8.0.0.jar'
            with zipfile.ZipFile(io.BytesIO(jar.read(name))) as inner:
                data = inner.read(entry)
            sources.append({'library': name.rsplit('/', 1)[-1], 'entry': entry,
                            'sha256': hashlib.sha256(data).hexdigest()})
            for sql in statements(data.decode('utf-8')):
                create = re.match(r'^CREATE\s+TABLE\s+((?:ACT|FLW)_[A-Z0-9_]+)\s*\(', sql, re.I)
                if create:
                    tables.append(create.group(1))
                elif not (re.match(r'^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:ACT|FLW)_[A-Z0-9_]+\s+ON\s+(?:ACT|FLW)_', sql, re.I)
                          or re.match(r'^ALTER\s+TABLE\s+(?:ACT|FLW)_[A-Z0-9_]+\s+ADD\s+', sql, re.I)
                          or re.match(r'^INSERT\s+INTO\s+ACT_(?:GE|ID)_PROPERTY\s+', sql, re.I)):
                    raise ValueError('Unreviewed Flowable SQL statement kind in ' + entry)
                statements_out.append(sql)
    return statements_out, tables, sources

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, action='append')
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--flowable-jar', type=Path, help='Extract matching Flowable8 schema from reviewed full JAR')
    parser.add_argument('--leanclub-permissions', type=Path)
    parser.add_argument('--school-dir', type=Path)
    parser.add_argument('--site-seed', type=Path)
    args = parser.parse_args()
    tables, seeds, sources, skipped = collect(args.input)
    flowable, flowable_tables, flowable_sources = flowable_schema(args.flowable_jar) if args.flowable_jar else ([], [], [])
    if set(tables) & set(flowable_tables):
        raise ValueError('Flowable tables already exist in input schema; review duplicate definitions')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    schema = 'SET NAMES utf8mb4;\n\n' + ';\n\n'.join(ordered_tables(tables)) + ';\n'
    seed = 'SET NAMES utf8mb4;\n\n' + (';\n\n'.join(seeds) + ';\n' if seeds else '')
    (args.output_dir / '001-schema.sql').write_text(schema, encoding='utf-8')
    (args.output_dir / '002-dictionaries-menus.sql').write_text(seed, encoding='utf-8')
    files = ['001-schema.sql', '002-dictionaries-menus.sql']
    if flowable:
        files.append('003-flowable8-schema.sql')
        (args.output_dir / files[-1]).write_text('SET NAMES utf8mb4;\n\n' + ';\n\n'.join(flowable) + ';\n', encoding='utf-8')
    product_files, product_report = prepare_product(args.output_dir, args.leanclub_permissions, args.school_dir, args.site_seed)
    files.extend(product_files)
    manifest = {'format': 1, 'purpose': 'new-empty-database-only', 'tables': sorted(list(tables) + flowable_tables),
                'schemaOrdering': 'foreign-key parents first; checks remain enabled',
                'tableCount': len(tables) + len(flowable_tables), 'flowableTables': flowable_tables,
                'flowableSources': flowable_sources, 'seedAllowlist': sorted(SEED_TABLES),
                'productSeed': product_report,
                'seedStatementCount': len(seeds), 'skippedStatements': dict(skipped),
                'sources': sources,
                'files': {name: hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
                          for name in files},
                'excluded': ['accounts/passwords', 'tokens/sessions', 'channels/secrets',
                             'tenants/roles/assignments', 'business/demo records', 'DROP/USE/ALTER/SET source commands'],
                'requires': ['new tenant/admin bootstrap', 'Flowable schema application and cold-start validation',
                             'reviewed leanclub permission migration', 'verified school dictionary import',
                             'fresh-schema startup and role acceptance']}
    (args.output_dir / 'schema-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'tableCount': len(tables) + len(flowable_tables), 'flowableTableCount': len(flowable_tables), 'seedStatementCount': len(seeds),
                      'output': str(args.output_dir)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
