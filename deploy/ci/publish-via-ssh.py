#!/usr/bin/env python3
"""Send one explicit release request via pinned-host SSH; no credential logging."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SAFE_ERRORS = {
    'REQUEST_REJECTED': ('request', 'Invalid release request.'),
    'REQUEST_TIMEOUT': ('request', 'Release request was not received within 20 seconds.'),
    'RELEASE_BUSY': ('validation', 'Another release is in progress.'),
    'PLAN_REQUIRED': ('preflight', 'Run a successful plan for this build and current server tools before publishing.'),
    'GATE_REJECTED': ('validation', 'Release validation was rejected. Check the selected build and trusted server configuration.'),
    'BUILD_FETCH_FAILED': ('download', 'Build metadata or artifact download failed. Check the successful build and retry.'),
    'BUILD_FETCH_TIMEOUT': ('download', 'Build download exceeded 60 minutes. Production was not changed.'),
    'PREFLIGHT_FAILED': ('preflight', 'Release preflight failed. Check schema, dependencies and the production baseline before publishing.'),
    'PUBLISH_FAILED_ROLLED_BACK': ('publish', 'Publication failed. Previous code and configuration recovered; business data was not restored.'),
    'ROLLBACK_FAILED': ('recovery', 'Publication recovery needs server inspection. Business data was not restored.'),
    'COORDINATOR_FAILED': ('release', 'The server release operation failed without a recognized receipt. Inspect server service status.'),
    'INTERNAL_ERROR': ('internal', 'Release failed unexpectedly. Inspect the server; sensitive details withheld.'),
}


def safe_failure(stderr):
    """Allow only the fixed wire contract, never arbitrary SSH/server error text."""
    try:
        data = json.loads(stderr) if len(stderr) <= 16384 else None
    except ValueError:
        return None
    if (not isinstance(data, dict) or set(data) != {'ok', 'errorCode', 'phase', 'error'}
            or data.get('ok') is not False or not isinstance(data.get('errorCode'), str)
            or data['errorCode'] not in SAFE_ERRORS):
        return None
    phase, message = SAFE_ERRORS[data['errorCode']]
    if data['phase'] != phase or data['error'] != message:
        return None
    return {'ok': False, 'errorCode': data['errorCode'], 'phase': phase, 'error': message}


def write_summary(report):
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('## LEAN SCHOOL release\n\n')
            stream.write('```json\n' + json.dumps(report, ensure_ascii=False, indent=2) + '\n```\n')


def main():
    run = os.environ.get('BUILD_RUN_ID', '')
    mode = os.environ.get('RELEASE_MODE', '')
    if not re.fullmatch('[1-9][0-9]{0,19}', run) or mode not in ('plan', 'publish'):
        raise ValueError('Invalid build ID or mode')
    key = os.environ.pop('DEPLOY_SSH_KEY')
    known = os.environ.pop('DEPLOY_KNOWN_HOSTS')
    token = os.environ.pop('GH_TOKEN')
    with tempfile.TemporaryDirectory(prefix='leanschool-release-') as directory:
        directory = Path(directory)
        directory.chmod(0o700)
        private = directory / 'key'
        hosts = directory / 'known_hosts'
        for path, value in ((private, key), (hosts, known)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(value.rstrip() + '\n')
        del key, known
        request = json.dumps({'runId': int(run), 'mode': mode, 'token': token})
        result = subprocess.run(['ssh', '-T', '-i', str(private), '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(hosts), '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6',
            'leanschool-deploy@152.136.108.87'], input=request, text=True,
            capture_output=True, timeout=4800)
        if result.returncode:
            failure = safe_failure(result.stderr)
            if failure is None:
                print('SSH_CONNECTION_OR_PROTOCOL_FAILED: No recognized server receipt. Check SSH connectivity and server status; raw output withheld.', file=sys.stderr)
            else:
                print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
                write_summary(failure | {'buildRunId': int(run), 'mode': mode})
            return 1
        report = json.loads(result.stdout)
        if report.get('ok') is not True or report.get('buildRunId') != int(run) or report.get('mode') != mode:
            raise ValueError('Invalid server receipt')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        write_summary(report)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired:
        print('RELEASE_STATUS_UNKNOWN: SSH exceeded 80 minutes. Inspect server status before retrying; publication may still be running.', file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Release client failed; sensitive details withheld.', file=sys.stderr)
        sys.exit(1)
