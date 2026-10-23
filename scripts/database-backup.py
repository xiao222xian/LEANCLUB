#!/usr/bin/env python3
"""Create, verify and restore encrypted SQLite/media backups. Never starts the app."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
import zipfile

REPO = Path(__file__).resolve().parents[1]
MAGIC = b"LEANCLUB-BACKUP\x00\x01"
LIMIT = 256 * 1024 * 1024
MAX_FILES = 5000
DATABASE_NAME = "database/clubs.sqlite3"
MANIFEST_NAME = "manifest.json"
MEDIA_NAME = re.compile(r"[a-f0-9]{32}(?:-640)?\.webp")


class BackupError(Exception):
    """An intentionally non-sensitive user-facing failure."""


def crypto():
    if sys.version_info < (3, 11) or not hasattr(sqlite3.Connection, "serialize") or not hasattr(sqlite3.Connection, "deserialize"):
        raise BackupError("Backup tooling requires Python 3.11+ with SQLite serialize/deserialize support.")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise BackupError("Install optional backup dependencies: python -m pip install -r requirements-backup.txt") from None
    return AESGCM


def absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


def reject_links(path):
    """Refuse symlinks and Windows junction/reparse points, including ancestors."""
    path = absolute(path)
    for node in [*reversed(path.parents), path]:
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise BackupError("Symbolic links and reparse points are not supported.")
    return path


def private_destination(path):
    """Plaintext and keys may only be in ignored data/ or outside this checkout."""
    path = reject_links(path)
    if path.is_relative_to(REPO) and not path.is_relative_to(REPO / "data"):
        raise BackupError("Keep keys and restored plaintext in this checkout's data/ directory or outside the checkout.")
    existing_parent = path.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    try:
        top = subprocess.run(["git", "-C", str(existing_parent), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
        if top.returncode == 0:
            git_root = Path(top.stdout.strip()).resolve()
            relative = path.relative_to(git_root).as_posix()
            tracked = subprocess.run(["git", "-C", str(git_root), "ls-files", "--", relative], capture_output=True, text=True, check=False)
            if tracked.stdout.strip():
                raise BackupError("Key or restore destination is tracked by Git; choose a private location.")
    except FileNotFoundError:
        raise BackupError("Git is required to verify private destinations are not tracked.") from None
    return path


def new_file(path, data):
    path = reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def integrity(database):
    if not database.startswith(b"SQLite format 3\x00"):
        raise BackupError("Backup does not contain a SQLite database.")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(database)
        connection.execute("PRAGMA query_only=ON")
        # The snapshot contains committed WAL pages; it is a standalone database.
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("SQLite integrity verification failed.")
    except sqlite3.Error:
        raise BackupError("SQLite integrity verification failed.") from None
    finally:
        connection.close()


def snapshot_database(path):
    path = reject_links(path)
    if not path.is_file():
        raise BackupError("Source database must be an existing regular file.")
    # Opening a WAL DB without its SHM can create a sidecar even with mode=ro.
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    reject_links(wal)
    reject_links(shm)
    if wal.exists() and wal.stat().st_size and not shm.exists():
        raise BackupError("WAL has no shared-memory file. Close the writer normally before taking this read-only snapshot.")
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    destination = sqlite3.connect(":memory:")
    try:
        source.execute("PRAGMA query_only=ON")
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        started = time.monotonic()

        def progress(_status, _remaining, total):
            if total * page_size > LIMIT or time.monotonic() - started > 60:
                raise BackupError("Database snapshot exceeds the 256 MiB or 60-second limit.")

        source.backup(destination, pages=256, progress=progress, sleep=0.05)
        # Normalize the private in-memory copy, never the live source.
        destination.execute("PRAGMA journal_mode=DELETE")
        # In-memory journal_mode stays MEMORY; VACUUM rebuilds WAL-format pages
        # as a standalone rollback-journal database using SQLite itself.
        destination.execute("VACUUM")
        result = destination.serialize()
        if len(result) > LIMIT:
            raise BackupError("Database exceeds the 256 MiB limit.")
        integrity(result)
        return result
    except sqlite3.Error:
        raise BackupError("Could not take a consistent read-only SQLite snapshot.") from None
    finally:
        destination.close()
        source.close()


def safe_member(name):
    path = PurePosixPath(name)
    if not name or "\\" in name or "\x00" in name or ":" in name or path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise BackupError("Unsafe archive member path.")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(p.endswith((".", " ")) or p.split(".", 1)[0].upper() in reserved for p in path.parts):
        raise BackupError("Unsafe cross-platform archive member path.")
    if name != DATABASE_NAME and not (name.startswith("media/") and len(path.parts) > 1):
        raise BackupError("Unexpected archive member.")
    return path


def verify_media_references(files):
    """Check both current and retained poster versions in the exact DB snapshot."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(files[DATABASE_NAME])
        connection.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("campaign_posters", "campaign_poster_history"):
            if table not in tables:
                continue
            for desktop, mobile in connection.execute(f'SELECT desktop,mobile FROM "{table}"'):
                for name in (desktop, mobile):
                    if name and (not isinstance(name, str) or not MEDIA_NAME.fullmatch(name) or "media/" + name not in files):
                        raise BackupError("A poster referenced by the SQLite snapshot is missing or unsafe. Pause uploads and retry with its media directory.")
    except sqlite3.Error:
        raise BackupError("Could not verify snapshot poster references.") from None
    finally:
        connection.close()


def collect_payload(db, media_dir=None):
    files = {DATABASE_NAME: snapshot_database(db)}
    total = len(files[DATABASE_NAME])
    if media_dir is not None:
        root = reject_links(media_dir)
        if not root.is_dir():
            raise BackupError("Media directory must exist.")
        for directory, folders, names in os.walk(root, followlinks=False):
            for name in folders:
                reject_links(Path(directory) / name)
            for name in sorted(names):
                path = reject_links(Path(directory) / name)
                before = path.stat()
                if not stat.S_ISREG(before.st_mode):
                    raise BackupError("Media may only contain regular files and directories.")
                total += before.st_size
                if total > LIMIT or len(files) >= MAX_FILES:
                    raise BackupError("Backup exceeds the 256 MiB or 5000-file limit.")
                archive_name = "media/" + path.relative_to(root).as_posix()
                if not MEDIA_NAME.fullmatch(path.relative_to(root).as_posix()):
                    raise BackupError("Media may contain only website-generated UUID WebP poster files; unrelated files are not packaged.")
                safe_member(archive_name)
                with path.open("rb") as stream:
                    content = stream.read(before.st_size + 1)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or len(content) != before.st_size:
                    raise BackupError("Media changed during backup; retry when uploads are paused.")
                files[archive_name] = content
    verify_media_references(files)
    manifest = {
        "format": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "database": DATABASE_NAME,
        "files": {name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()} for name, value in sorted(files.items())},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=True, separators=(",", ":")))
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    payload = output.getvalue()
    if len(payload) > LIMIT:
        raise BackupError("Compressed backup exceeds the 256 MiB limit.")
    return payload


def unpack_payload(payload):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES + 1 or sum(i.file_size for i in infos) > LIMIT + 1024 * 1024:
                raise BackupError("Archive exceeds the extraction limits.")
            if len({i.filename.casefold() for i in infos}) != len(infos):
                raise BackupError("Duplicate archive member.")
            for info in infos:
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise BackupError("Archive links and non-regular files are not supported.")
                if info.filename != MANIFEST_NAME:
                    safe_member(info.filename)
                elif info.file_size > 1024 * 1024:
                    raise BackupError("Manifest exceeds its size limit.")
            manifest = json.loads(archive.read(MANIFEST_NAME))
            if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("database") != DATABASE_NAME or not isinstance(manifest.get("files"), dict):
                raise BackupError("Invalid backup manifest.")
            if set(manifest["files"]) != {i.filename for i in infos if i.filename != MANIFEST_NAME} or DATABASE_NAME not in manifest["files"]:
                raise BackupError("Manifest and archive members differ.")
            files = {}
            for name, expected in manifest["files"].items():
                safe_member(name)
                value = archive.read(name)
                if not isinstance(expected, dict) or expected.get("bytes") != len(value) or expected.get("sha256") != hashlib.sha256(value).hexdigest():
                    raise BackupError("Backup content checksum verification failed.")
                files[name] = value
            integrity(files[DATABASE_NAME])
            verify_media_references(files)
            return manifest, files
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, UnicodeError, RuntimeError, NotImplementedError):
        raise BackupError("Invalid backup archive.") from None


def read_key(path):
    path = private_destination(path)
    if not path.is_file() or path.stat().st_size != 32:
        raise BackupError("Key file must contain exactly 32 binary bytes.")
    return path.read_bytes()


def decrypt_backup(input_path, key_file):
    AESGCM = crypto()
    path = reject_links(input_path)
    if not path.is_file() or path.stat().st_size > LIMIT + len(MAGIC) + 12 + 16:
        raise BackupError("Invalid encrypted backup size.")
    envelope = path.read_bytes()
    if not envelope.startswith(MAGIC) or len(envelope) < len(MAGIC) + 12 + 16:
        raise BackupError("Unsupported or truncated encrypted backup.")
    offset = len(MAGIC)
    try:
        payload = AESGCM(read_key(key_file)).decrypt(envelope[offset:offset + 12], envelope[offset + 12:], MAGIC)
    except BackupError:
        raise
    except Exception:
        raise BackupError("Authentication failed: wrong key or modified backup.") from None
    return unpack_payload(payload)


def backup(db, media_dir, output, key_file):
    AESGCM = crypto()
    output = reject_links(output)
    key_file = private_destination(key_file)
    if output == key_file or output.exists() or key_file.exists():
        raise BackupError("Backup and key must use separate new file paths; overwrite is forbidden.")
    if media_dir is not None and (output.is_relative_to(absolute(media_dir)) or key_file.is_relative_to(absolute(media_dir))):
        raise BackupError("Backup and key destinations must be outside the media source.")
    payload = collect_payload(db, media_dir)
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    envelope = MAGIC + nonce + AESGCM(key).encrypt(nonce, payload, MAGIC)
    unpack_payload(payload)
    created_key = False
    created_output = False
    try:
        new_file(key_file, key)
        created_key = True
        new_file(output, envelope)
        created_output = True
        decrypt_backup(output, key_file)
    except Exception:
        if created_output:
            output.unlink()
        if created_key:
            key_file.unlink()
        raise
    return output


def restore(input_path, key_file, target):
    target = private_destination(target)
    if target.exists():
        raise BackupError("Restore target must be a new directory; existing paths are never overwritten.")
    _manifest, files = decrypt_backup(input_path, key_file)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(mode=0o700)
    # No extractall: validate every path and create every destination exclusively.
    for name, value in files.items():
        relative = safe_member(name)
        destination = target.joinpath(*relative.parts)
        if not destination.is_relative_to(target):
            raise BackupError("Unsafe restore path.")
        new_file(destination, value)
    (target / "media").mkdir(exist_ok=True, mode=0o700)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup", help="Read-only SQLite snapshot + optional media; new random key per backup")
    create.add_argument("--db", required=True, type=Path)
    create.add_argument("--media-dir", type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--key-file", required=True, type=Path)
    for command in ("verify", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument("--input", required=True, type=Path)
        sub.add_argument("--key-file", required=True, type=Path)
        if command == "restore":
            sub.add_argument("--target", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            backup(args.db, args.media_dir, args.output, args.key_file)
            print("Encrypted backup created and verified. Keep the separate key private; it is required for recovery.")
        elif args.command == "verify":
            decrypt_backup(args.input, args.key_file)
            print("Verified: AES-GCM authentication, manifest, file SHA-256 and SQLite integrity.")
        else:
            restore(args.input, args.key_file, args.target)
            print("Restored to the new directory. No application was started and no notification was sent.")
        return 0
    except (BackupError, OSError, sqlite3.Error):
        # Paths and low-level error details can contain private source names.
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, BackupError) else "Backup operation failed; check private paths and file permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
