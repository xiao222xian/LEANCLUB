import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_gate', ROOT / 'deploy/ubuntu/install-release-gate.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class GateInstallationTests(unittest.TestCase):
    def key(self):
        blob = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + bytes(range(32))
        return 'ssh-ed25519 ' + base64.b64encode(blob).decode()

    def test_plain_public_key_only_and_forced_command(self):
        key = installer.public_key(self.key() + ' synthetic@example\n')
        self.assertEqual(key, self.key())
        entry = installer.key_line(key).decode()
        self.assertTrue(entry.startswith('restrict,command="/usr/bin/sudo -n /usr/local/sbin/leanschool-release-gate" '))
        for value in ('command="sh" ' + self.key(), self.key() + '\n' + self.key(),
                      '-----BEGIN OPENSSH PRIVATE KEY-----', 'ssh-ed25519 eA=='):
            with self.subTest(value=value[:24]), self.assertRaises(installer.InstallError):
                installer.public_key(value)

    def test_sudo_is_one_fixed_command_with_no_arguments(self):
        line = installer.sudoers().decode()
        self.assertIn('NOPASSWD: NOSETENV:', line)
        self.assertTrue(line.endswith('/usr/local/sbin/leanschool-release-gate ""\n'))
        self.assertNotIn('*', line)
        shell = installer.launcher(Path('/opt/leanschool/release-tools/reviewed')).decode()
        self.assertIn('[ "$#" -eq 0 ] || exit 64', shell)
        self.assertIn('exec /usr/bin/env -i ', shell)
        self.assertNotIn('"$@"', shell)

    def test_unregistered_existing_user_is_never_adopted(self):
        existing = SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_dir=str(installer.HOME),
                                   pw_shell='/bin/sh', pw_gecos=installer.ACCOUNT_COMMENT)
        with patch.object(installer, 'command') as execute:
            with self.assertRaises(installer.InstallError):
                installer.validate_account(existing, None)
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
