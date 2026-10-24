#!/usr/bin/env python3
"""Fixed SSH entry point. Accept one explicit successful build, never a shell command."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile

CONFIG = Path('/etc/leanschool/release-gate.json')
ROOT = Path('/var/lib/leanschool/releases/cloud')
REPO = 'papaoo/bojizhuye'
BRANCHES = {'main', 'platform/backend', 'platform/admin-ui'}
WORKFLOW = '.github/workflows/leanschool-release.yml'
SAFE_ERRORS = {
    'REQUEST_REJECTED': ('request', 'Invalid release request.'),
    'REQUEST_TIMEOUT': ('request', 'Release request was not received within 20 seconds.'),
    'RELEASE_BUSY': ('validation', 'Another release is in progress.'),
    'PLAN_REQUIRED': ('preflight', 'Run a successful plan for this build and current server tools before publishing.'),
    'GATE_REJECTED': ('validation', 'Release validation was rejected. Check the selected build and trusted server configuration.'),
    'BUILD_FETCH_FAILED': ('download', 'Build metadata or artifact download failed. Check the successful build and retry.'),
    'DOWNLOAD_AUTH_REJECTED': ('download', 'GitHub or artifact access was rejected with HTTP 401 or 403. Check access and download availability.'),
    'DOWNLOAD_RETRY_EXHAUSTED': ('download', 'Artifact transfer failed after bounded retries. Check server network access and retry.'),
    'RANGE_PROTOCOL_INVALID': ('download', 'Artifact range, length, encoding or ETag validation failed. Check the download service before retrying.'),
    'ARTIFACT_DIGEST_MISMATCH': ('download', 'Artifact size or SHA-256 differs from GitHub metadata. Retry from a verified build before publishing.'),
    'BUILD_FETCH_TIMEOUT': ('download', 'Build download exceeded 60 minutes. Production was not changed.'),
    'PREFLIGHT_FAILED': ('preflight', 'Release preflight failed. Check schema, dependencies and the production baseline before publishing.'),
    'PUBLISH_FAILED_ROLLED_BACK': ('publish', 'Publication failed. Previous code and configuration recovered; business data was not restored.'),
    'ROLLBACK_FAILED': ('recovery', 'Publication recovery needs server inspection. Business data was not restored.'),
    'COORDINATOR_FAILED': ('release', 'The server release operation failed without a recognized receipt. Inspect server service status.'),
    'INTERNAL_ERROR': ('internal', 'Release failed unexpectedly. Inspect the server; sensitive details withheld.'),
}
COORDINATOR_CODES = {'PREFLIGHT_FAILED', 'PUBLISH_FAILED_ROLLED_BACK', 'ROLLBACK_FAILED', 'INTERNAL_ERROR'}
FETCH_ERROR_CODES = {'BUILD_FETCH_FAILED', 'DOWNLOAD_AUTH_REJECTED', 'DOWNLOAD_RETRY_EXHAUSTED',
                     'RANGE_PROTOCOL_INVALID', 'ARTIFACT_DIGEST_MISMATCH'}


class GateError(Exception):
    def __init__(self, message, code='GATE_REJECTED'):
        super().__init__(message)
        self.code = code


def error_report(code):
    if code not in SAFE_ERRORS:
        code = 'INTERNAL_ERROR'
    phase, message = SAFE_ERRORS[code]
    return {'ok': False, 'errorCode': code, 'phase': phase, 'error': message}


@contextmanager
def deadline(seconds, code):
    """Only for request/download work; never kill an activating deployment."""
    def expired(_signum, _frame):
        raise GateError(SAFE_ERRORS[code][1], code)
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def bounded_request(stream):
    with deadline(20, 'REQUEST_TIMEOUT'):
        try:
            return read_request(stream)
        except GateError as error:
            if error.code == 'REQUEST_TIMEOUT':
                raise
            raise GateError('Invalid release request.', 'REQUEST_REJECTED') from None


def read_request(stream):
    raw = stream.read(16385)
    if len(raw) > 16384:
        raise GateError('Request too large.')
    try:
        data = json.loads(raw)
    except ValueError:
        raise GateError('Invalid request JSON.') from None
    if not isinstance(data, dict) or set(data) != {'runId', 'mode', 'token'}:
        raise GateError('Expected only runId, mode and token.')
    if type(data['runId']) is not int or not 0 < data['runId'] < 10**20:
        raise GateError('Invalid build run ID.')
    if data['mode'] not in ('plan', 'publish'):
        raise GateError('Invalid release mode.')
    token = data['token']
    if (not isinstance(token, str) or not 20 <= len(token) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise GateError('Invalid authentication input; values withheld.')
    return data


def protected(path, directory=False, private=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise GateError('Unsafe trusted path.')
    mode = path.stat()
    if (mode.st_uid != 0 or mode.st_mode & 0o022
            or (private and stat.S_IMODE(mode.st_mode) & 0o077)
            or (directory and not path.is_dir()) or (not directory and not path.is_file())):
        raise GateError('Trusted path ownership or permissions differ.')
    for parent in path.parents:
        if parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022:
            raise GateError('Untrusted parent directory.')
    return path


def validate_run(run, config, run_id):
    if (run.get('id') != run_id or run.get('workflow_id') != config['workflowId']
            or run.get('path') != WORKFLOW or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('event') not in ('push', 'workflow_dispatch')
            or run.get('head_branch') not in BRANCHES
            or (run.get('event') == 'workflow_dispatch' and run.get('head_branch') != 'main')
            or not isinstance(run.get('head_sha'), str)
            or not re.fullmatch('[a-f0-9]{40}', run['head_sha'])
            or type(run.get('run_attempt')) is not int or run['run_attempt'] < 1):
        raise GateError('Only a successful approved build workflow can be published.')
    for field in ('repository', 'head_repository'):
        repo = run.get(field, {})
        if repo.get('id') != config['repositoryId'] or repo.get('full_name') != REPO or repo.get('private') is not True:
            raise GateError('Build repository identity differs.')
    return run['head_sha'], run['run_attempt']


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def load_fetcher(tools):
    script = protected(tools / 'deploy/ubuntu/fetch-github-artifact.py')
    spec = importlib.util.spec_from_file_location('verified_release_fetch', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def successful_plan(path, identity):
    try:
        protected(path, private=True)
        if json.loads(path.read_text()) != identity:
            raise ValueError('Plan identity differs.')
    except (OSError, ValueError, GateError):
        raise GateError(SAFE_ERRORS['PLAN_REQUIRED'][1], 'PLAN_REQUIRED') from None


def save_plan(path, identity):
    if path.exists() or path.is_symlink():
        protected(path, private=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(identity, output)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch_release(request, config, fetcher, root):
    api = fetcher.GitHub(request['token'])
    run = api.get('/actions/runs/' + str(request['runId']))
    workflow_sha, attempt = validate_run(run, config, request['runId'])
    name = 'leanschool-release-%d-%d' % (request['runId'], attempt)
    # Even cached packages require the current successful attempt and API provenance.
    artifact, _ = fetcher.artifact_for_run(api, request['runId'], name, workflow_sha)
    provenance = artifact.get('workflow_run', {})
    if (provenance.get('repository_id') != config['repositoryId']
            or provenance.get('head_repository_id') != config['repositoryId']
            or provenance.get('head_branch') != run['head_branch']):
        raise GateError('Artifact repository or branch identity differs.')
    folder = root / ('%d-%d-%s' % (request['runId'], attempt, workflow_sha[:12]))
    receipt_path = root / (folder.name + '.verified.json')
    identity = {'artifactId': artifact['id'], 'digest': artifact['digest'], 'workflowSha': workflow_sha}
    if folder.exists():
        if request['mode'] == 'publish' and not receipt_path.exists():
            raise GateError(SAFE_ERRORS['PLAN_REQUIRED'][1], 'PLAN_REQUIRED')
        protected(folder, directory=True, private=True)
        protected(receipt_path, private=True)
        if json.loads(receipt_path.read_text()) != identity or {p.name for p in folder.iterdir()} != fetcher.FILES:
            raise GateError('Cached release receipt or file set differs.')
        hashes = {}
        for name in fetcher.FILES:
            item = protected(folder / name, private=True)
            hashes[name] = {'bytes': item.stat().st_size, 'sha256': sha(item)}
        fetcher.verify_bundle(folder, hashes, request['runId'], attempt, workflow_sha)
    else:
        if request['mode'] == 'publish':
            raise GateError(SAFE_ERRORS['PLAN_REQUIRED'][1], 'PLAN_REQUIRED')
        if receipt_path.exists() or receipt_path.is_symlink():
            raise GateError('Unpaired release receipt exists.')
        fetcher.fetch(argparse.Namespace(run_id=request['runId'], artifact_name=name,
                      expected_workflow_sha=workflow_sha, output=folder, download_workers=8), request['token'])
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(identity, output)
    manifest = json.loads((folder / 'release-manifest.json').read_text())
    if ('workflowSha' in manifest and (manifest.get('workflowEvent') != run['event']
            or manifest.get('workflowRef') != 'refs/heads/' + run['head_branch'])):
        raise GateError('Bundle event/ref differ from GitHub provenance.')
    return folder, manifest, attempt, identity


def execute(request, config, fetcher, root=ROOT):
    # This timer ends before any production mutation starts. Range workers also
    # have their own bounded HTTP retries and cancel pending pieces on failure.
    with deadline(3600, 'BUILD_FETCH_TIMEOUT'):
        try:
            folder, manifest, attempt, identity = fetch_release(request, config, fetcher, root)
        except fetcher.FetchError as error:
            code = getattr(error, 'code', None)
            if not isinstance(code, str) or code not in FETCH_ERROR_CODES:
                code = 'BUILD_FETCH_FAILED'
            # Do not forward exception text or accept unrelated gate/publication codes.
            raise GateError(SAFE_ERRORS[code][1], code) from None
    tools = protected(Path(config['toolsRoot']), directory=True)
    coordinator = protected(tools / 'deploy/ubuntu/deploy-daily-release.py')
    planned_path = root / (folder.name + '.planned.json')
    planned_identity = {'format': 1, 'runId': request['runId'], 'attempt': attempt,
                        **identity, 'toolsRoot': str(tools)}
    if request['mode'] == 'publish':
        successful_plan(planned_path, planned_identity)
    # Clear the short-lived token before executing any release operation.
    request.pop('token', None)
    # The coordinator owns bounded steps and recovery. An outer timeout must not
    # SIGKILL it midway through activation and orphan a still-running installer.
    result = subprocess.run(['/usr/bin/python3', str(coordinator), request['mode'],
                             '--release-dir', str(folder), '--tools-root', str(tools)],
                            capture_output=True, text=True,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
    if result.returncode:
        code = 'COORDINATOR_FAILED'
        try:
            failure = json.loads(result.stderr) if len(result.stderr) <= 16384 else None
            if (isinstance(failure, dict) and failure.get('ok') is False
                    and failure.get('databaseRestored') is False
                    and isinstance(failure.get('errorCode'), str)
                    and failure.get('errorCode') in COORDINATOR_CODES):
                code = failure['errorCode']
        except ValueError:
            pass
        # Discard the coordinator's error text, even when its code is recognized.
        raise GateError(SAFE_ERRORS[code][1], code)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise GateError('Release operation returned an invalid receipt.') from None
    if not isinstance(report, dict) or report.get('ok') is not True or report.get('mode') != request['mode']:
        raise GateError('Release operation returned an unsuccessful receipt.', 'COORDINATOR_FAILED')
    if request['mode'] == 'plan':
        if report.get('deployed') is not False or report.get('schemaCompatible') is not True:
            raise GateError('Release plan returned an incomplete receipt.', 'COORDINATOR_FAILED')
        save_plan(planned_path, planned_identity)
    return {'ok': True, 'buildRunId': request['runId'], 'attempt': attempt,
            'mode': request['mode'], 'commits': manifest['commits'], 'result': report}


def main():
    import fcntl
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'leanschool-deploy':
        raise GateError('Use the restricted deployment identity.')
    protected(CONFIG, private=True)
    config = json.loads(CONFIG.read_text())
    if (set(config) != {'format', 'repositoryId', 'workflowId', 'toolsRoot'} or config['format'] != 1
            or any(type(config[k]) is not int or config[k] < 1 for k in ('repositoryId', 'workflowId'))):
        raise GateError('Invalid release gate configuration.')
    tools = protected(Path(config['toolsRoot']), directory=True)
    protected(ROOT, directory=True, private=True)
    # Waiting for stdin/EOF cannot hold the global publication lock indefinitely.
    request = bounded_request(sys.stdin)
    fd = os.open('/run/leanschool-release-gate.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute(request, config, load_fetcher(tools)), ensure_ascii=False))
    except BlockingIOError:
        raise GateError('Another release is in progress.', 'RELEASE_BUSY') from None
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except GateError as error:
        print(json.dumps(error_report(error.code)), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print(json.dumps(error_report('INTERNAL_ERROR')), file=sys.stderr)
        sys.exit(1)
