#!/usr/bin/env python3
"""Fetch one verified code-only release from papaoo/bojizhuye Actions.

stdin: {"username": "Git credential username", "password": "access token"}.
Credentials and signed URLs stay in memory. This tool only makes GET requests.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = 'papaoo/bojizhuye'
API = 'https://api.github.com/repos/' + REPOSITORY
CODE = {'yudao-server.jar', 'java-release.json', 'console.tar.gz', 'website.tar.gz'}
FILES = CODE | {'release-manifest.json', 'SHA256SUMS'}
LIMITS = {'yudao-server.jar': 2 * 1024**3, 'console.tar.gz': 512 * 1024**2,
          'website.tar.gz': 512 * 1024**2, 'java-release.json': 1024**2,
          'release-manifest.json': 1024**2, 'SHA256SUMS': 4096}
MAX_DOWNLOAD = 3 * 1024**3
RANGE_BLOCK_BYTES = 8 * 1024**2
MAX_JSON = 2 * 1024**2
USER_AGENT = 'LEAN-SCHOOL-verified-artifact-fetch/1'
SIGNED_URL_REFRESH_SECONDS = 300
FETCH_ERROR_MESSAGES = {
    'BUILD_FETCH_FAILED': 'Artifact validation/download failed; sensitive details withheld.',
    'DOWNLOAD_AUTH_REJECTED': 'Artifact download authorization was rejected; obtain a new download location or inspect access.',
    'DOWNLOAD_RETRY_EXHAUSTED': 'Artifact download transport retries were exhausted.',
    'RANGE_PROTOCOL_INVALID': 'Artifact range response failed strict protocol validation.',
    'ARTIFACT_DIGEST_MISMATCH': 'Artifact ZIP size or SHA-256 differs from verified metadata.',
}


class FetchError(Exception):
    """Only fixed, non-sensitive messages may be reported to the operator."""

    def __init__(self, message, code='BUILD_FETCH_FAILED'):
        super().__init__(message)
        self.code = code if isinstance(code, str) and code in FETCH_ERROR_MESSAGES else 'BUILD_FETCH_FAILED'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def opener():
    # No cookie jar, HTTP auth handler or inherited proxy credentials.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def response(client, request):
    try:
        return client.open(request, timeout=45)
    except urllib.error.HTTPError as error:
        return error  # Caller checks status; never stringify URL/body-bearing exceptions.
    except (OSError, ValueError):
        raise FetchError('HTTPS request failed; response details withheld.') from None


def credential(stream):
    raw = stream.read(16385)
    if len(raw) > 16384:
        raise FetchError('Credential input exceeds the limit.')
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise FetchError('Expected credential JSON on standard input.') from None
    if (not isinstance(data, dict) or set(data) != {'username', 'password'}
            or any(not isinstance(data[key], str) or not data[key]
                   or any(ord(char) < 33 or ord(char) > 126 for char in data[key]) for key in data)):
        raise FetchError('Invalid credential JSON; values withheld.')
    return data['password']  # Git credential password is a token, not an account password.


class GitHub:
    def __init__(self, token):
        self.token = token
        self.client = opener()

    def get(self, suffix, redirect=False):
        if not re.fullmatch(r'/actions/(?:runs/[1-9][0-9]*(?:/artifacts\?per_page=100&page=[1-9][0-9]*)?|artifacts/[1-9][0-9]*(?:/zip)?)', suffix):
            raise FetchError('Unapproved API read path.')
        request = urllib.request.Request(API + suffix, method='GET', headers={
            'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': USER_AGENT,
        })
        with response(self.client, request) as result:
            if result.code != (302 if redirect else 200):
                raise FetchError('GitHub API did not return the required status (HTTP %d).' % result.code)
            if redirect:
                locations = result.headers.get_all('Location', [])
                if len(locations) != 1:
                    raise FetchError('Artifact response needs exactly one download location.')
                return locations[0]
            raw = result.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise FetchError('GitHub metadata exceeds the limit.')
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                raise FetchError('Invalid GitHub metadata JSON.') from None
            if not isinstance(value, dict):
                raise FetchError('Unexpected GitHub metadata shape.')
            return value


def artifact_for_run(api, run_id, name, expected_sha):
    run = api.get('/actions/runs/' + str(run_id))
    if (run.get('id') != run_id or run.get('head_sha') != expected_sha
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('repository', {}).get('full_name') != REPOSITORY
            or run.get('head_repository', {}).get('full_name') != REPOSITORY):
        raise FetchError('Run must be successful, completed, from the fixed repository and exact workflow commit.')
    attempt = run.get('run_attempt')
    if type(attempt) is not int or attempt < 1 or name != 'leanschool-release-%d-%d' % (run_id, attempt):
        raise FetchError('Only this successful attempt\'s final leanschool-release artifact is accepted.')
    matches, seen, expected_total = [], set(), None
    for page in range(1, 11):
        listing = api.get('/actions/runs/%d/artifacts?per_page=100&page=%d' % (run_id, page))
        items, total = listing.get('artifacts'), listing.get('total_count')
        if (not isinstance(items, list) or len(items) > 100 or type(total) is not int or not 0 <= total <= 1000
                or (expected_total is not None and total != expected_total)):
            raise FetchError('Unexpected artifact listing or too many artifacts.')
        expected_total = total
        for item in items:
            if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0 or item['id'] in seen:
                raise FetchError('Artifact listing contains invalid or duplicate IDs.')
            seen.add(item['id'])
            if item.get('name') == name:
                matches.append(item)
        if len(seen) >= total:
            break
        if not items:
            raise FetchError('Artifact pagination ended before the reported count.')
    if len(seen) != expected_total:
        raise FetchError('Artifact listing is incomplete or differs from the reported count.')
    if len(matches) != 1:
        raise FetchError('Expected exactly one artifact with the requested name.')
    item = api.get('/actions/artifacts/' + str(matches[0]['id']))
    provenance = item.get('workflow_run', {})
    if (item.get('id') != matches[0]['id'] or item.get('name') != name or item.get('expired') is not False
            or provenance.get('id') != run_id or provenance.get('head_sha') != expected_sha
            or type(item.get('size_in_bytes')) is not int or not 0 < item['size_in_bytes'] <= MAX_DOWNLOAD
            or not isinstance(item.get('digest'), str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', item['digest'])):
        raise FetchError('Artifact provenance, expiry, size or required SHA-256 digest is invalid.')
    return item, attempt


def https_download_url(url):
    if (not isinstance(url, str) or len(url) > 16384 or '\\' in url
            or any(ord(char) < 33 or ord(char) > 126 for char in url)):
        raise FetchError('Invalid signed HTTPS download URL (withheld).')
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment or '.' not in host
                or not re.fullmatch(r'[a-zA-Z0-9.-]+', host)):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return url
        raise ValueError()
    except ValueError:
        raise FetchError('Only credential-free HTTPS DNS download URLs on port 443 are accepted.') from None


def download(url, target, expected_digest):
    # A separate opener and fresh requests cannot inherit GitHub Authorization.
    client, digest, total = opener(), hashlib.sha256(), 0
    for _ in range(4):
        request = urllib.request.Request(https_download_url(url), method='GET', headers={
            'Accept': 'application/octet-stream', 'User-Agent': USER_AGENT,
        })
        with response(client, request) as result:
            if result.code in (301, 302, 303, 307, 308):
                locations = result.headers.get_all('Location', [])
                if len(locations) != 1:
                    raise FetchError('Ambiguous artifact download redirect.')
                url = locations[0]
                continue
            if result.code != 200:
                raise FetchError('Artifact download failed (HTTP %d); URL withheld.' % result.code,
                                 'DOWNLOAD_AUTH_REJECTED' if result.code in (401, 403) else 'BUILD_FETCH_FAILED')
            with target.open('xb') as output:
                target.chmod(0o600)
                for chunk in iter(lambda: result.read(1024 * 1024), b''):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise FetchError('Artifact download exceeds the byte limit.')
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                raise FetchError('Downloaded ZIP SHA-256 differs from the GitHub artifact digest.',
                                 'ARTIFACT_DIGEST_MISMATCH')
            return digest.hexdigest(), total
    raise FetchError('Artifact download exceeded the redirect limit.')


class RangeRetry(FetchError):
    """A bounded retry is safe for a transport failure, never for invalid content."""


def range_response(url, start, end, etag=None):
    client = opener()  # Each attempt owns a credential-free client.
    headers = {'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity',
               'User-Agent': USER_AGENT, 'Range': 'bytes=%d-%d' % (start, end)}
    if etag:
        headers['If-Match'] = etag
    for _ in range(4):
        request = urllib.request.Request(https_download_url(url), method='GET', headers=headers)
        try:
            result = response(client, request)
        except FetchError:
            raise RangeRetry('Range request transport failed; details withheld.') from None
        if result.code in (301, 302, 303, 307, 308):
            locations = result.headers.get_all('Location', [])
            result.close()
            if len(locations) != 1:
                raise FetchError('Ambiguous artifact range redirect.')
            url = locations[0]
            continue
        if result.code != 206:
            status = result.code
            result.close()
            if status in (408, 429, 500, 502, 503, 504):
                raise RangeRetry('Artifact range request temporarily failed.')
            raise FetchError('Range download requires HTTP 206; received HTTP %d.' % status,
                             'DOWNLOAD_AUTH_REJECTED' if status in (401, 403) else 'RANGE_PROTOCOL_INVALID')
        return result
    raise FetchError('Artifact range download exceeded the redirect limit.')


def range_headers(result, start, end, total, etag=None):
    def single(name):
        values = result.headers.get_all(name, [])
        if len(values) != 1:
            raise FetchError('Range response requires unique range, length and ETag headers.', 'RANGE_PROTOCOL_INVALID')
        return values[0]

    if single('Content-Range') != 'bytes %d-%d/%d' % (start, end, total):
        raise FetchError('Artifact Content-Range differs from the requested interval or total size.', 'RANGE_PROTOCOL_INVALID')
    if single('Content-Length') != str(end - start + 1):
        raise FetchError('Artifact range length does not match its requested interval.', 'RANGE_PROTOCOL_INVALID')
    current = single('ETag')
    if not re.fullmatch(r'"[\x21\x23-\x7e]{1,256}"', current) or (etag is not None and current != etag):
        raise FetchError('Artifact range requires an unchanged strong ETag.', 'RANGE_PROTOCOL_INVALID')
    encodings = result.headers.get_all('Content-Encoding', [])
    if encodings not in ([], ['identity']):
        raise FetchError('Artifact range must use identity content encoding.', 'RANGE_PROTOCOL_INVALID')
    return current


def download_parallel(url, target, expected_digest, expected_size, workers, refresh_url=None):
    """Strict optional range mode. Only a fully hashed ZIP reaches extraction."""
    if type(workers) is not int or not 2 <= workers <= 16 or not 0 < expected_size <= MAX_DOWNLOAD:
        raise FetchError('Parallel download requires 2-16 workers and a bounded artifact size.')
    if target.exists() or target.is_symlink():
        raise FetchError('Parallel download target already exists.')
    url = https_download_url(url)
    cancelled = threading.Event()
    location_lock = threading.Lock()
    generation, refreshed_at = 0, time.monotonic()

    def location(failed_generation=None):
        # Serialize refreshes so simultaneous failures of the same signed URL get
        # one replacement. URLs/token-bearing query values never leave memory.
        nonlocal url, generation, refreshed_at
        with location_lock:
            if cancelled.is_set():
                raise FetchError('Parallel artifact download cancelled.')
            stale = time.monotonic() - refreshed_at >= SIGNED_URL_REFRESH_SECONDS
            rejected = failed_generation is not None and failed_generation == generation
            if refresh_url is not None and (stale or rejected):
                replacement = https_download_url(refresh_url())
                url, generation, refreshed_at = replacement, generation + 1, time.monotonic()
            return url, generation

    def range_with_refresh(start, end, etag=None):
        current, version = location()
        try:
            return range_response(current, start, end, etag)
        except FetchError as error:
            if error.code != 'DOWNLOAD_AUTH_REJECTED' or refresh_url is None:
                raise
            # Exactly one authorization refresh/retry per range request. A
            # second 401/403 is terminal, rather than an unbounded refresh loop.
            current, _ = location(failed_generation=version)
            return range_response(current, start, end, etag)

    def attempt(operation):
        for index in range(3):
            if cancelled.is_set():
                raise FetchError('Parallel artifact download cancelled.')
            try:
                return operation()
            except (RangeRetry, OSError, http.client.HTTPException):
                if index == 2:
                    raise FetchError('Artifact range failed after three attempts; details withheld.',
                                     'DOWNLOAD_RETRY_EXHAUSTED') from None
                cancelled.wait(0.5 * (index + 1))

    def probe():
        with range_with_refresh(0, 0) as result:
            stamp = range_headers(result, 0, 0, expected_size)
            received = len(result.read(2))
            if received > 1:
                raise FetchError('Artifact range probe was truncated or oversized.', 'RANGE_PROTOCOL_INVALID')
            if received != 1:
                raise RangeRetry('Artifact range probe was truncated or oversized.')
            return stamp

    etag = attempt(probe)
    intervals = [(start, min(start + RANGE_BLOCK_BYTES, expected_size) - 1)
                 for start in range(0, expected_size, RANGE_BLOCK_BYTES)]
    count = len(intervals)
    with tempfile.TemporaryDirectory(prefix='.artifact-parts-', dir=target.parent) as part_dir:
        parts = [Path(part_dir) / str(i) for i in range(count)]

        def part(index):
            start, end = intervals[index]

            def receive():
                with range_with_refresh(start, end, etag) as result:
                    range_headers(result, start, end, expected_size, etag)
                    # A retried piece always replaces its own incomplete temporary file.
                    with parts[index].open('wb') as output:
                        parts[index].chmod(0o600)
                        received = 0
                        while True:
                            if cancelled.is_set():
                                raise FetchError('Parallel artifact download cancelled.')
                            chunk = result.read(min(1024 * 1024, end - start + 2 - received))
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > end - start + 1:
                                raise FetchError('Artifact range sent more bytes than requested.', 'RANGE_PROTOCOL_INVALID')
                            output.write(chunk)
                        if received != end - start + 1:
                            raise RangeRetry('Artifact range stream was truncated.')
            return attempt(receive)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='artifact-range') as pool:
            futures = [pool.submit(part, i) for i in range(count)]
            try:
                for future in as_completed(futures):
                    future.result()
            except Exception:
                cancelled.set()
                for future in futures:
                    future.cancel()
                raise
        digest, received = hashlib.sha256(), 0
        with target.open('xb') as output:
            target.chmod(0o600)
            for path in parts:
                with path.open('rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        output.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
        if received != expected_size or digest.hexdigest() != expected_digest:
            raise FetchError('Parallel ZIP size or SHA-256 differs from GitHub artifact metadata.',
                             'ARTIFACT_DIGEST_MISMATCH')
        return digest.hexdigest(), received


def extract_code_zip(archive_path, directory):
    hashes = {}
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) != len(FILES) or {item.filename for item in entries} != FILES:
            raise FetchError('Artifact ZIP must contain exactly the six approved files at its root.')
        for item in entries:
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (item.is_dir() or kind not in (0, stat.S_IFREG) or item.external_attr & 0x10
                    or item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or not 0 < item.file_size <= LIMITS[item.filename]):
                raise FetchError('Artifact ZIP contains a link, special/encrypted file or oversized member.')
        directory.mkdir(mode=0o700)
        for item in entries:
            digest, total = hashlib.sha256(), 0
            path = directory / item.filename
            with archive.open(item) as source, path.open('xb') as target:
                path.chmod(0o600)
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    total += len(chunk)
                    if total > LIMITS[item.filename] or total > item.file_size:
                        raise FetchError('Expanded ZIP member exceeds its declared limit.')
                    digest.update(chunk)
                    target.write(chunk)
            if total != item.file_size:
                raise FetchError('Expanded ZIP member size differs from its declaration.')
            hashes[item.filename] = {'sha256': digest.hexdigest(), 'bytes': total}
    return hashes


def check_tar(path, website_sha=None, console_sha=None):
    seen, regular, total = set(), set(), 0
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            if (name.is_absolute() or not name.parts or '..' in name.parts or '\\' in item.name or ':' in item.name
                    or item.name.rstrip('/') != name.as_posix() or name.as_posix() in seen
                    or any(part.lower() in {'.git', '.venv', 'node_modules', 'data', 'private-init', 'runtime', 'credentials', 'secrets'} for part in name.parts)
                    or any(part.lower().startswith('.env') and part.lower() != '.env.example' for part in name.parts)
                    or name.suffix.lower() in {'.map', '.sql', '.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx'}
                    or any(word in name.name.lower() for word in ('private-init', 'credentials.local', 'secrets.env', 'secrets.json', '.private.'))
                    or item.size < 0 or item.sparse is not None
                    or not (item.isfile() or (website_sha and item.isdir()))):
                raise FetchError('Nested code archive contains a forbidden/private member or link.')
            if not website_sha and (name.parts[0] != 'console' or len(name.parts) < 2):
                raise FetchError('Console archive must stay under console/.')
            if website_sha and name.parts[0] not in {'backend', 'web', 'scripts', 'deploy', 'requirements.txt'}:
                raise FetchError('Website archive contains an unapproved top-level directory.')
            seen.add(name.as_posix())
            if item.isfile():
                regular.add(name.as_posix())
            total += item.size
            if len(seen) > 50000 or total > 2 * 1024**3:
                raise FetchError('Expanded code archive exceeds its member/byte limit.')
        if website_sha:
            if archive.pax_headers.get('comment') != website_sha or not {'backend/server.py', 'web/creator/index.html', 'requirements.txt'} <= regular:
                raise FetchError('Website Git provenance or required files do not match.')
        elif 'console/index.html' not in seen:
            raise FetchError('Console archive is missing its production entry point.')
        elif console_sha and archive.pax_headers.get('comment') != console_sha:
            raise FetchError('Console source provenance does not match the release.')


def verify_bundle(directory, hashes, run_id, attempt, expected_sha):
    manifest = json.loads((directory / 'release-manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != 1 or manifest.get('repository') != 'https://github.com/' + REPOSITORY
            or manifest.get('workflowSha', manifest.get('toolsCommit')) != expected_sha
            or not isinstance(manifest.get('toolsCommit'), str)
            or not re.fullmatch('[a-f0-9]{40}', manifest['toolsCommit']) or manifest.get('runId') != str(run_id)
            or manifest.get('runAttempt') != str(attempt) or manifest.get('privateInitIncluded') is not False
            or manifest.get('state') != 'cloud-built-not-deployed'):
        raise FetchError('Release manifest does not match this code-only workflow run.')
    commits = manifest.get('commits', {})
    if (set(commits) != {'website', 'backend', 'adminUi'}
            or any(not re.fullmatch('[a-f0-9]{40}', value) for value in commits.values())
            or manifest.get('websiteCommit') != commits['website']
            or manifest.get('artifacts') != {name: hashes[name] for name in CODE}):
        raise FetchError('Release manifest source commits or artifact digests differ.')
    checksums = {}
    for line in (directory / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9.-]+)', line)
        if not match or match[2] in checksums:
            raise FetchError('Invalid or duplicate checksum manifest entry.')
        checksums[match[2]] = match[1]
    if checksums != {name: value['sha256'] for name, value in hashes.items() if name != 'SHA256SUMS'}:
        raise FetchError('SHA256SUMS does not match every approved artifact and release manifest.')
    java = json.loads((directory / 'java-release.json').read_text(encoding='utf-8'))
    if (java.get('sourceCommit') != commits['backend'] or java.get('artifact', {}).get('sha256') != hashes['yudao-server.jar']['sha256']
            or java.get('artifact', {}).get('bytes') != hashes['yudao-server.jar']['bytes']):
        raise FetchError('Java release stamp does not match the downloaded JAR.')
    if 'workflowSha' in manifest:
        branches = {'website': 'main', 'backend': 'platform/backend', 'adminUi': 'platform/admin-ui'}
        refs, heads = manifest.get('sourceRefs', {}), manifest.get('branchHeads', {})
        if (manifest.get('workflowEvent') not in ('push', 'workflow_dispatch')
                or manifest.get('workflowRef') not in {'refs/heads/' + b for b in branches.values()}
                or (manifest.get('workflowEvent') == 'workflow_dispatch' and manifest.get('workflowRef') != 'refs/heads/main')
                or not isinstance(refs, dict) or not isinstance(heads, dict)
                or set(refs) != set(branches) or set(heads) != set(branches)):
            raise FetchError('Daily build source resolution is invalid.')
        for key, branch in branches.items():
            if (not isinstance(heads[key], str) or not re.fullmatch('[a-f0-9]{40}', heads[key])
                    or not isinstance(refs[key], str)
                    or (refs[key] != branch and not re.fullmatch('[a-f0-9]{40}', refs[key]))
                    or commits[key] != (heads[key] if refs[key] == branch else refs[key])):
                raise FetchError('Daily build resolved commits differ.')
        if manifest['workflowEvent'] == 'push':
            key = next(k for k, b in branches.items() if manifest['workflowRef'] == 'refs/heads/' + b)
            if commits[key] != expected_sha or (key == 'website' and manifest['toolsCommit'] != expected_sha):
                raise FetchError('Push source commit drifted.')
    check_tar(directory / 'console.tar.gz', console_sha=commits['adminUi'] if 'workflowSha' in manifest else None)
    check_tar(directory / 'website.tar.gz', commits['website'])
    return commits


def private_zip_directory(path):
    """The gate, never request JSON/CLI input, supplies this root-owned cache."""
    path = Path(path)
    if not path.is_absolute():
        raise FetchError('ZIP cache must be an absolute root-managed directory.')
    for parent in (path, *path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or parent == path and stat.S_IMODE(info.st_mode) != 0o700):
            raise FetchError('ZIP cache ownership, path type or permissions are invalid.')
    return path


def private_zip_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise FetchError('ZIP cache requires a private root-owned regular file without links.')


def zip_cache_path(directory, artifact):
    directory = private_zip_directory(directory)
    artifact_id, digest = artifact.get('id'), artifact.get('digest')
    if type(artifact_id) is not int or artifact_id < 1 or not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise FetchError('ZIP cache requires a verified artifact identity.')
    return directory / ('%d-%s.zip' % (artifact_id, digest.removeprefix('sha256:')))


def copy_verified_zip(source, target, artifact):
    # Copy from one opened inode, then hash exactly the bytes sent to extraction.
    # Neither a pathname check alone nor a cache filename is proof of integrity.
    private_zip_file(Path(source).lstat())
    descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        private_zip_file(info)
        if info.st_size != artifact['size_in_bytes']:
            raise FetchError('Cached ZIP size differs from GitHub metadata.', 'ARTIFACT_DIGEST_MISMATCH')
        digest, total = hashlib.sha256(), 0
        with Path(target).open('xb') as output:
            Path(target).chmod(0o600)
            while True:
                chunk = stream.read(min(1024 * 1024, artifact['size_in_bytes'] - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > artifact['size_in_bytes']:
                    raise FetchError('Cached ZIP exceeds its verified size.', 'ARTIFACT_DIGEST_MISMATCH')
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    if total != artifact['size_in_bytes'] or 'sha256:' + digest.hexdigest() != artifact['digest']:
        raise FetchError('Cached ZIP SHA-256 differs from GitHub metadata.', 'ARTIFACT_DIGEST_MISMATCH')
    return digest.hexdigest(), total


def save_zip_cache(source, destination, artifact):
    # Publish only a complete, rehashed inode, without replacing any existing cache.
    private_zip_directory(destination.parent)
    with tempfile.TemporaryDirectory(prefix='.zip-cache-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'artifact.zip'
        copy_verified_zip(source, staged, artifact)
        try:
            os.link(staged, destination, follow_symlinks=False)
        except FileExistsError:
            raise FetchError('ZIP cache appeared concurrently; inspect it before retrying.') from None
        staged.unlink()  # The published inode now has exactly one name.


def fetch(args, token):
    output = Path(args.output).absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise FetchError('Output must not exist, and its parent must already be a directory.')
    api = GitHub(token)
    artifact, attempt = artifact_for_run(api, args.run_id, args.artifact_name, args.expected_workflow_sha)
    cache_directory = getattr(args, 'zip_cache_dir', None)
    cached = zip_cache_path(cache_directory, artifact) if cache_directory is not None else None
    with tempfile.TemporaryDirectory(prefix='.leanschool-artifact-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        target, digest = temporary / 'artifact.zip', artifact['digest'].removeprefix('sha256:')
        cache_hit = cached is not None and (cached.exists() or cached.is_symlink())
        if cache_hit:
            zip_hash, zip_bytes = copy_verified_zip(cached, target, artifact)
        else:
            url = api.get('/actions/artifacts/%d/zip' % artifact['id'], redirect=True)
            workers = getattr(args, 'download_workers', 1)
            if workers == 1:
                zip_hash, zip_bytes = download(url, target, digest)
            else:
                zip_hash, zip_bytes = download_parallel(url, target, digest, artifact['size_in_bytes'], workers,
                    refresh_url=lambda: api.get('/actions/artifacts/%d/zip' % artifact['id'], redirect=True))
        if zip_bytes != artifact['size_in_bytes']:
            raise FetchError('Downloaded ZIP size differs from the GitHub artifact metadata.', 'ARTIFACT_DIGEST_MISMATCH')
        if cached is not None and not cache_hit:
            save_zip_cache(target, cached, artifact)
        directory = temporary / 'release'
        hashes = extract_code_zip(temporary / 'artifact.zip', directory)
        commits = verify_bundle(directory, hashes, args.run_id, attempt, args.expected_workflow_sha)
        output.mkdir(mode=0o700, exist_ok=False)  # Exclusive: never replace even an empty destination.
        for name in sorted(FILES):
            os.rename(directory / name, output / name)
    return {'ok': True, 'repository': REPOSITORY, 'runId': args.run_id, 'runAttempt': attempt,
            'artifactId': artifact['id'], 'artifactName': args.artifact_name,
            'workflowSha': args.expected_workflow_sha, 'zipSha256': zip_hash, 'zipBytes': zip_bytes,
            'files': hashes, 'commits': commits, 'output': str(output), 'deployed': False,
            'zipCacheHit': cache_hit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--artifact-name', required=True)
    parser.add_argument('--expected-workflow-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--download-workers', type=int, choices=range(1, 17), default=1,
                        help='1 preserves streaming mode; 2-16 enables strictly verified parallel HTTP ranges')
    args = parser.parse_args()
    if (not 0 < args.run_id < 10**20 or not re.fullmatch(r'[a-f0-9]{40}', args.expected_workflow_sha)
            or not re.fullmatch(r'leanschool-release-[1-9][0-9]*-[1-9][0-9]*', args.artifact_name)):
        raise FetchError('Expected a positive run ID, final release artifact name and full lowercase workflow SHA.')
    print(json.dumps(fetch(args, credential(sys.stdin)), ensure_ascii=False, indent=2))


def cli():
    try:
        main()
        return 0
    except FetchError as error:
        code = error.code if isinstance(error.code, str) and error.code in FETCH_ERROR_MESSAGES else 'BUILD_FETCH_FAILED'
        print(json.dumps({'ok': False, 'errorCode': code, 'error': FETCH_ERROR_MESSAGES[code]}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'errorCode': 'BUILD_FETCH_FAILED',
                          'error': FETCH_ERROR_MESSAGES['BUILD_FETCH_FAILED']}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(cli())
