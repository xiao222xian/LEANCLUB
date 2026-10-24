#!/usr/bin/env python3
"""Resolve allowed source refs once, before a build; never resolve a different repository."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORY = 'papaoo/bojizhuye'
BRANCHES = {'website': 'main', 'backend': 'platform/backend', 'adminUi': 'platform/admin-ui'}
SHA = re.compile(r'[0-9a-f]{40}')


def require_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError('Expected a complete lowercase 40-character commit SHA')
    return value


def source_ref(component, value):
    branch = BRANCHES[component]
    if value in (branch, 'refs/heads/' + branch):
        return branch
    return require_sha(value)


def github_api(token):
    def get(path):
        request = Request('https://api.github.com/repos/' + REPOSITORY + ('/' + path if path else ''),
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28',
                                   'User-Agent': 'leanschool-release-resolver'})
        with urlopen(request, timeout=30) as response:
            if response.url.split('?', 1)[0] != request.full_url:
                raise ValueError('Unexpected GitHub API redirect')
            return json.load(response)
    return get


def resolve(repository, event, workflow_ref, workflow_sha, tools_commit, requested, api):
    if repository != REPOSITORY:
        raise ValueError('Release builds are restricted to the private application repository')
    if event not in {'push', 'workflow_dispatch'}:
        raise ValueError('Unsupported build event')
    if workflow_ref not in {'refs/heads/' + b for b in BRANCHES.values()}:
        raise ValueError('Unsupported workflow branch')
    if event == 'workflow_dispatch' and workflow_ref != 'refs/heads/main':
        raise ValueError('Manual builds must use the workflow on main')
    require_sha(workflow_sha)
    require_sha(tools_commit)
    if event == 'push' and workflow_ref == 'refs/heads/main' and tools_commit != workflow_sha:
        raise ValueError('Main push tools must match the event commit')
    if set(requested) != set(BRANCHES):
        raise ValueError('Expected exactly three component refs')
    selected = {key: source_ref(key, value or BRANCHES[key]) for key, value in requested.items()}
    pushed_component = next(key for key, branch in BRANCHES.items() if workflow_ref == 'refs/heads/' + branch)
    if event == 'push':
        selected[pushed_component] = workflow_sha
    metadata = api('')
    if metadata.get('full_name') != REPOSITORY or metadata.get('private') is not True:
        raise ValueError('GitHub did not confirm the expected private repository')
    heads = {}
    for component, branch in BRANCHES.items():
        data = api('git/ref/heads/' + quote(branch, safe='/'))
        if data.get('ref') != 'refs/heads/' + branch or data.get('object', {}).get('type') != 'commit':
            raise ValueError('GitHub branch response mismatch')
        heads[component] = require_sha(data['object']['sha'])

    def ancestor(commit, head):
        if commit == head:
            return
        data = api('compare/' + commit + '...' + head)
        if (data.get('status') != 'ahead' or data.get('merge_base_commit', {}).get('sha') != commit
                or data.get('base_commit', {}).get('sha') != commit):
            raise ValueError('Requested commit is not on the expected component branch')

    ancestor(tools_commit, heads['website'])
    commits = {}
    for component, value in selected.items():
        commits[component] = heads[component] if value == BRANCHES[component] else value
        ancestor(commits[component], heads[component])
    return {'format': 1, 'repository': 'https://github.com/' + REPOSITORY,
            'workflowEvent': event, 'workflowRef': workflow_ref, 'workflowSha': workflow_sha,
            'toolsCommit': tools_commit, 'commits': commits,
            'sourceRefs': selected, 'branchHeads': heads}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'event', 'workflow-ref', 'workflow-sha', 'tools-commit'):
        parser.add_argument('--' + name, required=True)
    for name, default in (('website-ref', 'main'), ('backend-ref', 'platform/backend'), ('admin-ui-ref', 'platform/admin-ui')):
        parser.add_argument('--' + name, default=default)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    token = os.environ.get('GH_TOKEN')
    if not token:
        raise ValueError('A read-only GitHub token is required')
    result = resolve(args.repository, args.event, args.workflow_ref, args.workflow_sha, args.tools_commit,
                     {'website': args.website_ref, 'backend': args.backend_ref, 'adminUi': args.admin_ui_ref},
                     github_api(token))
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as stream:
            for key, value in {**result['commits'], 'toolsCommit': result['toolsCommit']}.items():
                stream.write(key + '=' + value + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Release refs could not be safely resolved; credentials and API details withheld.', file=sys.stderr)
        raise SystemExit(1)
