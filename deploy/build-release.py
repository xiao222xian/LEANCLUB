#!/usr/bin/env python3
"""Package fixed, clean source commits and already-verified artifacts; no runtime data."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--java-source', required=True, type=Path)
    p.add_argument('--java-jar', required=True, type=Path)
    p.add_argument('--java-manifest', required=True, type=Path)
    p.add_argument('--ui-source', required=True, type=Path)
    p.add_argument('--seed-dir', required=True, type=Path,
                   help='Reviewed clean initialization files; kept in a private archive, never Git.')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    website = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('design_build', website / 'scripts/check-design-build.py')
    design_build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(design_build)
    design_build.validate(website)
    roots = {'website': website, 'backend': a.java_source.resolve(), 'adminUi': a.ui_source.resolve()}
    commits = {}
    for name, root in roots.items():
        if git(root, 'status', '--porcelain'):
            raise ValueError(name + ' source is not a clean commit.')
        commits[name] = git(root, 'rev-parse', 'HEAD')
    jar_manifest = json.loads(a.java_manifest.read_text(encoding='utf-8'))
    if jar_manifest['sourceCommit'] != commits['backend'] or jar_manifest['artifact']['sha256'] != sha256(a.java_jar):
        raise ValueError('Java artifact does not match the supplied commit/hash manifest.')
    ui = a.ui_source.resolve() / 'dist-leanschool'
    if not (ui / 'index.html').is_file() or '/console/assets/' not in (ui / 'index.html').read_text(encoding='utf-8'):
        raise ValueError('Missing /console/ production build.')
    files = sorted(ui.rglob('*'))
    for item in files:
        if item.is_symlink() or item.name.startswith('.env') or item.suffix.lower() in {'.map', '.pem', '.key', '.pfx', '.sqlite3', '.db'}:
            raise ValueError('Unexpected sensitive/generated file in UI build: ' + item.name)
    seed = a.seed_dir.resolve()
    seed_manifest = json.loads((seed / 'schema-manifest.json').read_text(encoding='utf-8'))
    seed_names = {'001-schema.sql', '002-dictionaries-menus.sql', '003-flowable8-schema.sql',
                  '004-leanclub-permissions.sql', '005-schools.sql', '006-site-draft.sql',
                  'site-content.ready.json'}
    if (set(seed_manifest['files']) != seed_names
            or seed_manifest.get('purpose') != 'new-empty-database-only'):
        raise ValueError('Expected the reviewed clean initialization manifest.')
    for name in seed_names:
        item = seed / name
        if item.is_symlink() or not item.is_file() or sha256(item) != seed_manifest['files'][name]:
            raise ValueError('Initialization artifact failed validation: ' + name)
    output = a.output.resolve()
    if any(output == root or root in output.parents for root in roots.values()):
        raise ValueError('Release output must be outside all source checkouts.')
    output.mkdir(parents=True, exist_ok=False)
    website_archive = output / 'website.tar.gz'
    subprocess.run(['git', '-C', str(website), 'archive', '--format=tar.gz',
                    '--output=' + str(website_archive), commits['website'],
                    'backend', 'web', 'scripts', 'deploy', 'requirements.txt'], check=True)
    with tarfile.open(output / 'console.tar.gz', 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        for item in files:
            if item.is_file():
                info = archive.gettarinfo(str(item), arcname='console/' + item.relative_to(ui).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o644
                with item.open('rb') as stream:
                    archive.addfile(info, stream)
    shutil.copyfile(a.java_jar, output / 'yudao-server.jar')
    shutil.copyfile(a.java_manifest, output / 'java-release.json')
    with tarfile.open(output / 'private-init.tar.gz', 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(seed_names | {'schema-manifest.json'}):
            item = seed / name
            info = archive.gettarinfo(str(item), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mode = 0o600
            with item.open('rb') as stream:
                archive.addfile(info, stream)
    artifacts = {item.name: {'sha256': sha256(item), 'bytes': item.stat().st_size}
                 for item in sorted(output.iterdir()) if item.is_file()}
    manifest = {'format': 1, 'repository': 'https://github.com/papaoo/bojizhuye',
                'commits': commits, 'artifacts': artifacts,
                'websiteCommit': commits['website'],
                'state': 'packaged-not-deployed',
                'seedManifestSha256': sha256(seed / 'schema-manifest.json'),
                'privateSeeds': 'private-init.tar.gz is deployment-only; do not upload to public Git or public download storage. No runtime database or credentials are included.'}
    (output / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'SHA256SUMS').write_text(''.join(f"{value['sha256']}  {name}\n" for name, value in artifacts.items()), encoding='ascii')
    print(json.dumps({'output': str(output), 'commits': commits, 'artifacts': artifacts}, indent=2))


if __name__ == '__main__':
    main()
