"""Synthetic isolated fixtures only; optional cryptography is not a service dependency."""
import importlib.util
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "database-backup.py"
SPEC = importlib.util.spec_from_file_location("database_backup", SCRIPT)
backup_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_tool)
POSTER = "a" * 32 + ".webp"
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None


@unittest.skipIf(AESGCM is None or not hasattr(sqlite3.Connection, "serialize"), "Backup tests require Python 3.11+ and: pip install -r requirements-backup.txt")
class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="leanclub-backup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "source.sqlite3"
        with closing(sqlite3.connect(self.db)) as database:
            database.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, value TEXT)")
            database.execute("INSERT INTO fixture(value) VALUES (?)", ("synthetic-private-fixture",))
            database.commit()
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / POSTER).write_bytes(b"synthetic-media-content")
        self.output = self.root / "snapshot.enc"
        self.key = self.root / "private" / "snapshot.key"

    def create(self):
        backup_tool.backup(self.db, self.media, self.output, self.key)

    def fixture_values(self, path):
        with closing(sqlite3.connect(path)) as database:
            return database.execute("SELECT value FROM fixture ORDER BY id").fetchall()

    def write_authenticated_payload(self, payload):
        key = AESGCM.generate_key(bit_length=256)
        self.key.parent.mkdir(exist_ok=True)
        self.key.write_bytes(key)
        nonce = os.urandom(12)
        self.output.write_bytes(backup_tool.MAGIC + nonce + AESGCM(key).encrypt(nonce, payload, backup_tool.MAGIC))

    def changed_archive(self, change):
        payload = backup_tool.collect_payload(self.db, self.media)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = [(info, archive.read(info.filename)) for info in archive.infolist()]
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as archive:
            for info, value in files:
                archive.writestr(info, value)
            change(archive)
        return target.getvalue()

    def test_round_trip_authenticated_checksums_sqlite_and_media(self):
        original = self.db.read_bytes()
        self.create()
        manifest, files = backup_tool.decrypt_backup(self.output, self.key)
        self.assertEqual(manifest["format"], 1)
        self.assertNotIn(b"synthetic-private-fixture", self.output.read_bytes())
        self.assertNotIn(b"synthetic-media-content", self.output.read_bytes())
        self.assertEqual(len(self.key.read_bytes()), 32)
        target = self.root / "new-restore"
        backup_tool.restore(self.output, self.key, target)
        self.assertEqual(self.fixture_values(target / "database" / "clubs.sqlite3"), [("synthetic-private-fixture",)])
        self.assertEqual((target / "media" / POSTER).read_bytes(), b"synthetic-media-content")
        self.assertEqual(original, self.db.read_bytes())
        self.assertFalse(Path(str(self.db) + "-wal").exists())
        self.assertFalse(Path(str(self.db) + "-shm").exists())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)

    def test_wal_snapshot_includes_committed_pages_without_changing_source(self):
        writer = sqlite3.connect(self.db)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO fixture(value) VALUES ('committed-in-wal')")
            writer.commit()
            main_before = self.db.read_bytes()
            wal = Path(str(self.db) + "-wal")
            wal_before = wal.read_bytes()
            self.create()
            self.assertEqual(self.db.read_bytes(), main_before)
            self.assertEqual(wal.read_bytes(), wal_before)
            target = self.root / "wal-restore"
            backup_tool.restore(self.output, self.key, target)
            self.assertEqual(self.fixture_values(target / "database" / "clubs.sqlite3"), [("synthetic-private-fixture",), ("committed-in-wal",)])
        finally:
            writer.close()

    def test_fresh_key_nonce_and_ciphertext_every_backup(self):
        self.create()
        second_output, second_key = self.root / "second.enc", self.root / "second.key"
        backup_tool.backup(self.db, self.media, second_output, second_key)
        offset = len(backup_tool.MAGIC)
        self.assertNotEqual(self.key.read_bytes(), second_key.read_bytes())
        self.assertNotEqual(self.output.read_bytes()[offset:offset + 12], second_output.read_bytes()[offset:offset + 12])

    def test_wrong_key_and_tamper_fail_before_restore_creates_directory(self):
        self.create()
        wrong_key = self.root / "wrong.key"
        wrong_key.write_bytes(AESGCM.generate_key(bit_length=256))
        target = self.root / "must-not-exist"
        with self.assertRaisesRegex(backup_tool.BackupError, "Authentication failed"):
            backup_tool.restore(self.output, wrong_key, target)
        self.assertFalse(target.exists())
        encrypted = bytearray(self.output.read_bytes())
        encrypted[-1] ^= 1
        self.output.write_bytes(encrypted)
        with self.assertRaisesRegex(backup_tool.BackupError, "Authentication failed"):
            backup_tool.restore(self.output, self.key, target)
        self.assertFalse(target.exists())

    def test_existing_key_output_and_restore_target_are_never_overwritten(self):
        self.create()
        key_bytes, encrypted = self.key.read_bytes(), self.output.read_bytes()
        with self.assertRaises(backup_tool.BackupError):
            self.create()
        self.assertEqual(self.key.read_bytes(), key_bytes)
        self.assertEqual(self.output.read_bytes(), encrypted)
        target = self.root / "existing"
        target.mkdir()
        with self.assertRaisesRegex(backup_tool.BackupError, "new directory"):
            backup_tool.restore(self.output, self.key, target)
        self.assertEqual(list(target.iterdir()), [])

    def test_key_and_restore_plaintext_refused_in_tracked_source_tree(self):
        with self.assertRaisesRegex(backup_tool.BackupError, "data/"):
            backup_tool.private_destination(backup_tool.REPO / "docs" / "unsafe.key")
        self.create()
        with self.assertRaisesRegex(backup_tool.BackupError, "data/"):
            backup_tool.restore(self.output, self.key, backup_tool.REPO / "unsafe-restore")

    def test_authenticated_path_traversal_and_archive_symlink_are_rejected(self):
        for bad_name in ("media/../escaped", "media/../../escaped", "media/C:/escaped", "media\\..\\escaped", "/media/escaped"):
            with self.subTest(bad_name=bad_name):
                payload = self.changed_archive(lambda archive: archive.writestr(bad_name, b"bad"))
                with self.assertRaises(backup_tool.BackupError):
                    backup_tool.unpack_payload(payload)
        link = zipfile.ZipInfo("media/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        payload = self.changed_archive(lambda archive: archive.writestr(link, b"../../outside"))
        with self.assertRaisesRegex(backup_tool.BackupError, "links"):
            backup_tool.unpack_payload(payload)

    def test_filesystem_media_symlink_is_rejected(self):
        link = self.media / "link.webp"
        try:
            link.symlink_to(self.media / POSTER)
        except OSError:
            self.skipTest("OS account cannot create symlinks")
        with self.assertRaisesRegex(backup_tool.BackupError, "links"):
            self.create()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.key.exists())

    def test_bad_manifest_checksum_and_oversize_limit(self):
        payload = backup_tool.collect_payload(self.db, self.media)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = {info.filename: archive.read(info.filename) for info in archive.infolist()}
        manifest = json.loads(files[backup_tool.MANIFEST_NAME])
        manifest["files"][backup_tool.DATABASE_NAME]["sha256"] = "0" * 64
        files[backup_tool.MANIFEST_NAME] = json.dumps(manifest).encode()
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        self.write_authenticated_payload(target.getvalue())
        with self.assertRaisesRegex(backup_tool.BackupError, "checksum"):
            backup_tool.decrypt_backup(self.output, self.key)
        original_limit = backup_tool.LIMIT
        try:
            backup_tool.LIMIT = 1
            with self.assertRaises(backup_tool.BackupError):
                backup_tool.collect_payload(self.db, self.media)
        finally:
            backup_tool.LIMIT = original_limit

    def test_snapshot_current_and_historical_media_must_be_present(self):
        missing = "b" * 32 + ".webp"
        with closing(sqlite3.connect(self.db)) as database:
            database.execute("CREATE TABLE campaign_posters (desktop TEXT, mobile TEXT)")
            database.execute("CREATE TABLE campaign_poster_history (desktop TEXT, mobile TEXT)")
            database.execute("INSERT INTO campaign_posters VALUES (?,?)", (POSTER, ""))
            database.execute("INSERT INTO campaign_poster_history VALUES (?,?)", (missing, ""))
            database.commit()
        with self.assertRaisesRegex(backup_tool.BackupError, "referenced"):
            self.create()
        self.assertFalse(self.key.exists())
        (self.media / missing).write_bytes(b"synthetic-retained-version")
        self.create()
        backup_tool.decrypt_backup(self.output, self.key)

    def test_unrelated_media_file_is_not_packaged(self):
        (self.media / "private-notes.txt").write_text("synthetic-unrelated-secret")
        with self.assertRaisesRegex(backup_tool.BackupError, "unrelated"):
            self.create()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
