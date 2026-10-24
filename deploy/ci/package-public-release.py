#!/usr/bin/env python3
"""Package reviewed code artifacts only; never accepts a seed or runtime directory."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

BRANCHES = {'website': 'main', 'backend': 'platform/backend', 'adminUi': 'platform/admin-ui'}
TOOLS = {'java': 'Temurin 25.0.5+8', 'maven': '3.9.7', 'node': '24.20.0', 'pnpm': '10.30.0',
         'publicPnpm': '10.29.2'}
ARTIFACTS = {'yudao-server.jar', 'java-release.json', 'console.tar.gz', 'website.tar.gz'}
spec = importlib.util.spec_from_file_location('release_preflight', Path(__file__).resolve().parents[1] / 'yudao/preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
design_spec = importlib.util.spec_from_file_location('design_build', Path(__file__).resolve().parents[2] / 'scripts/check-design-build.py')
design_build = importlib.util.module_from_spec(design_spec)
design_spec.loader.exec_module(design_build)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def clean_source(root, expected):
    if git(root, 'rev-parse', 'HEAD') != expected or git(root, 'status', '--porcelain'):
        raise ValueError('Source must be the exact clean reviewed commit')


def safe_member(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
            or any(p in {'.git', 'node_modules', 'private-init', 'runtime', 'credentials'} for p in path.parts)
            or any(p.startswith('.env') and p != '.env.example' for p in path.parts)
            or path.suffix.lower() in {'.map', '.pem', '.key', '.pfx', '.p12', '.db', '.sqlite', '.sqlite3', '.sql'}
            or 'private-init' in path.name or 'credentials.local' in path.name):
        raise ValueError('Forbidden artifact member')
    return path


def new_output(path, roots):
    output = path.resolve()
    if any(output == r.resolve() or r.resolve() in output.parents for r in roots):
        raise ValueError('Output must be outside source checkouts')
    output.mkdir(parents=True, exist_ok=False)
    return output


def validate_console(archive_path, expected_commit=None):
    with tarfile.open(archive_path, 'r:gz') as archive:
        if expected_commit is not None and archive.pax_headers.get('comment') != expected_commit:
            raise ValueError('Console archive source commit mismatch')
        seen, total = set(), 0
        for item in archive.getmembers():
            path = safe_member(item.name)
            if (not item.isfile() or path.parts[0] != 'console' or len(path.parts) < 2
                    or item.name in seen):
                raise ValueError('Console archive must contain unique regular files under console/')
            seen.add(item.name)
            total += item.size
            if len(seen) > 50000 or total > 2 * 1024 ** 3:
                raise ValueError('Console archive exceeds installer limits')
        if 'console/index.html' not in seen:
            raise ValueError('Missing console/index.html')
        html = archive.extractfile('console/index.html').read().decode('utf-8')
        if '/console/assets/' not in html or '/@vite/client' in html:
            raise ValueError('Expected /console/ production HTML')
        for url in re.findall(r'(?:src|href)="(/[^"?#]+)"', html):
            if not url.startswith('/console/') or url.lstrip('/') not in seen:
                raise ValueError('Console HTML references a missing or out-of-prefix file')


def package_console(source, output, expected_commit):
    source = source.resolve()
    if not isinstance(expected_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_commit):
        raise ValueError('Console source needs an immutable commit')
    clean_source(source, expected_commit)
    dist = source / 'dist-leanschool'
    if dist.is_symlink() or not (dist / 'index.html').is_file():
        raise ValueError('Missing regular UI build directory')
    items = sorted(dist.rglob('*'))
    for item in items:
        safe_member('console/' + item.relative_to(dist).as_posix())
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError('UI build contains a link or special file')
    output = new_output(output, [source])
    commit_time = int(git(source, 'show', '-s', '--format=%ct', 'HEAD'))
    with tarfile.open(output / 'console.tar.gz', 'w:gz', format=tarfile.PAX_FORMAT,
                      pax_headers={'comment': expected_commit}) as archive:
        for item in items:
            if item.is_file():
                info = archive.gettarinfo(str(item), arcname='console/' + item.relative_to(dist).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o644
                info.mtime = commit_time
                with item.open('rb') as stream:
                    archive.addfile(info, stream)
    validate_console(output / 'console.tar.gz', expected_commit)


def exact_files(directory, names):
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != names:
        raise ValueError('Unexpected files in downloaded job artifact')
    if any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):
        raise ValueError('Downloaded artifacts must be regular files')


def validate_resolution(resolution):
    if (resolution.get('format') != 1 or resolution.get('repository') != 'https://github.com/papaoo/bojizhuye'
            or resolution.get('workflowEvent') not in {'push', 'workflow_dispatch'}
            or resolution.get('workflowRef') not in {'refs/heads/' + value for value in BRANCHES.values()}):
        raise ValueError('Invalid build resolution origin')
    if resolution['workflowEvent'] == 'workflow_dispatch' and resolution['workflowRef'] != 'refs/heads/main':
        raise ValueError('Manual release workflow must run on main')
    for value in (resolution.get('toolsCommit'), resolution.get('workflowSha')):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
            raise ValueError('Workflow versions need immutable commits')
    for name in ('commits', 'branchHeads', 'sourceRefs'):
        values = resolution.get(name, {})
        if not isinstance(values, dict) or set(values) != set(BRANCHES):
            raise ValueError('Expected exactly three component versions')
        for component, value in values.items():
            if not isinstance(value, str) or not (re.fullmatch(r'[0-9a-f]{40}', value)
                                                  or name == 'sourceRefs' and value == BRANCHES[component]):
                raise ValueError('Invalid component version')
    if resolution['workflowEvent'] == 'push':
        component = next(key for key, branch in BRANCHES.items() if resolution['workflowRef'] == 'refs/heads/' + branch)
        if resolution['commits'][component] != resolution['workflowSha']:
            raise ValueError('Push event source version drifted')
        if component == 'website' and resolution['toolsCommit'] != resolution['workflowSha']:
            raise ValueError('Main push tools version drifted')
    for component, ref in resolution['sourceRefs'].items():
        expected = resolution['branchHeads'][component] if ref == BRANCHES[component] else ref
        if resolution['commits'][component] != expected:
            raise ValueError('Resolved component version drifted')
    return resolution


def package_bundle(website, backend, console, output, resolution, run_id, run_attempt):
    resolution = validate_resolution(resolution)
    commits = resolution['commits']
    tools_commit = resolution['toolsCommit']
    if not re.fullmatch(r'[0-9]+', run_id) or not re.fullmatch(r'[0-9]+', run_attempt):
        raise ValueError('Expected a GitHub Actions run identity')
    clean_source(website, commits['website'])
    design_build.validate(website)
    exact_files(backend, {'yudao-server.jar', 'java-release.json'})
    exact_files(console, {'console.tar.gz'})
    report = preflight.inspect_jar(backend / 'yudao-server.jar')
    stamp = json.loads((backend / 'java-release.json').read_text(encoding='utf-8'))
    if (stamp.get('sourceCommit') != commits['backend'] or stamp.get('artifact') != report
            or report['javaClassMajor'] != 69):
        raise ValueError('Java manifest/Java 25/full-module artifact mismatch')
    validate_console(console / 'console.tar.gz', commits['adminUi'])
    output = new_output(output, [website, backend, console])
    for name in ('yudao-server.jar', 'java-release.json'):
        shutil.copyfile(backend / name, output / name)
    shutil.copyfile(console / 'console.tar.gz', output / 'console.tar.gz')
    subprocess.run(['git', '-C', str(website), 'archive', '--format=tar.gz',
                    '--output=' + str(output / 'website.tar.gz'), commits['website'],
                    'backend', 'web', 'scripts', 'deploy', 'requirements.txt'], check=True)
    with tarfile.open(output / 'website.tar.gz', 'r:gz') as archive:
        if archive.pax_headers.get('comment') != commits['website']:
            raise ValueError('Website archive lost its Git commit provenance')
        for member in archive.getmembers():
            safe_member(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError('Website archive contains a link or special file')
    artifacts = {name: {'sha256': preflight.sha256(output / name), 'bytes': (output / name).stat().st_size}
                 for name in sorted(ARTIFACTS)}
    manifest = {'format': 1, 'repository': 'https://github.com/papaoo/bojizhuye',
                'commits': commits, 'websiteCommit': commits['website'], 'toolsCommit': tools_commit,
                'workflowSha': resolution['workflowSha'], 'workflowEvent': resolution['workflowEvent'],
                'workflowRef': resolution['workflowRef'], 'sourceRefs': resolution['sourceRefs'],
                'branchHeads': resolution['branchHeads'],
                'buildTools': TOOLS, 'runner': 'ubuntu-24.04 / x64',
                'runId': run_id, 'runAttempt': run_attempt,
                'runUrl': f'https://github.com/papaoo/bojizhuye/actions/runs/{run_id}',
                'artifacts': artifacts, 'state': 'cloud-built-not-deployed',
                'privateInitIncluded': False,
                'validation': 'Website Python/Node checks and leanclub Java unit/H2 tests; full Java package skips other upstream tests; UI type and deployment checks. No production or business acceptance.'}
    (output / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    hashes = {**artifacts, 'release-manifest.json': {'sha256': preflight.sha256(output / 'release-manifest.json')}}
    (output / 'SHA256SUMS').write_text(''.join(f"{value['sha256']}  {name}\n" for name, value in sorted(hashes.items())), encoding='ascii')
    print(json.dumps({'commits': commits, 'toolsCommit': tools_commit, 'artifacts': artifacts}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    ui = commands.add_parser('console')
    ui.add_argument('--source', type=Path, required=True)
    ui.add_argument('--output', type=Path, required=True)
    ui.add_argument('--source-commit', required=True)
    bundle = commands.add_parser('bundle')
    for name in ('website', 'backend', 'console', 'output'):
        bundle.add_argument('--' + name, type=Path, required=True)
    bundle.add_argument('--resolution', type=Path, required=True)
    for name in ('run-id', 'run-attempt'):
        bundle.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.command == 'console':
        package_console(args.source, args.output, args.source_commit)
    else:
        package_bundle(args.website, args.backend, args.console, args.output,
                       json.loads(args.resolution.read_text(encoding='utf-8')), args.run_id, args.run_attempt)


if __name__ == '__main__':
    main()
