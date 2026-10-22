"""Synthetic administrator identifiers; no real addresses or credentials."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from campaign import CampaignStore, valid_admin_username, validate_credentials
from store import Problem


class AdminIdentityValidationTests(unittest.TestCase):
    def test_existing_username_rules_remain_compatible(self):
        for value in ('abc', 'A_1', 'admin.editor-01', '9' * 40):
            with self.subTest(value=value):
                self.assertTrue(valid_admin_username(value))
        for value in ('ab', 'a' * 41, '_admin', '.admin', '-admin', '管理账号'):
            with self.subTest(value=value):
                self.assertFalse(valid_admin_username(value))

    def test_ascii_email_supports_common_addresses_and_length_boundaries(self):
        longest = 'a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 61
        self.assertEqual(len(longest), 254)
        for value in ('editor@example.test', 'First.Last+campaign@sub.example.test',
                      '123456789@example.test', "o'connor@example.test", longest):
            with self.subTest(value=value):
                self.assertTrue(valid_admin_username(value))
        self.assertFalse(valid_admin_username(longest + 'd'))
        self.assertFalse(valid_admin_username('a' * 65 + '@example.test'))
        self.assertFalse(valid_admin_username('editor@' + 'b' * 64 + '.test'))

    def test_malformed_or_injected_identifiers_are_rejected(self):
        for value in (None, 123, '', 'editor@@example.test', 'editor@example@test.test',
                      'editor@example', 'editor@.example.test', 'editor@example..test',
                      '.editor@example.test', 'editor.@example.test', 'ed..itor@example.test',
                      'editor@-example.test', 'editor@example-.test', 'editor@ex_ample.test',
                      ' editor@example.test', 'editor@example.test ', 'ed itor@example.test',
                      'editor@example.test\n', 'editor@example.test\r\nX-Header: bad',
                      'editor@example.test\x00', '编辑@example.test', 'editor@例子.test',
                      "' OR 1=1 --", 'editor@example.test;DELETE FROM campaign_admins'):
            with self.subTest(value=value):
                self.assertFalse(valid_admin_username(value))
                with self.assertRaises(Problem) as raised:
                    validate_credentials(value, 'Synthetic-password-621')
                self.assertEqual(raised.exception.status, 400)


class AdminEmailStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CampaignStore(Path(self.tmp.name) / 'identity.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_email_create_login_case_insensitivity_and_password_reset(self):
        account = 'Campaign.Editor+long-identifier@example.test'
        self.assertGreater(len(account), 40)
        self.store.create_admin(account, 'Synthetic-password-621', first_only=True)
        token, session = self.store.login(account.upper(), 'Synthetic-password-621', 'synthetic-email-ip')
        self.assertEqual(session['username'], account)
        self.assertTrue(self.store.session(token)['authenticated'])
        with self.assertRaises(Problem) as raised:
            self.store.create_admin(account.lower(), 'Synthetic-password-999')
        self.assertEqual(raised.exception.status, 409)
        self.store.reset_password(account.lower(), 'Synthetic-reset-password-854')
        self.assertIsNone(self.store.session(token))
        with self.assertRaises(Problem) as raised:
            self.store.login(account, 'Synthetic-password-621', 'synthetic-old-password-ip')
        self.assertEqual(raised.exception.status, 401)
        _, replacement = self.store.login(account, 'Synthetic-reset-password-854', 'synthetic-reset-ip')
        self.assertEqual(replacement['username'], account)

    def test_login_accepts_254_character_email_and_rejects_oversized_input(self):
        account = 'a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 61
        self.store.create_admin(account, 'Synthetic-password-621')
        _, session = self.store.login(account, 'Synthetic-password-621', 'synthetic-max-ip')
        self.assertEqual(session['username'], account)
        for value in (account + 'd', 'editor@example.test\n', "' OR 1=1 --"):
            with self.subTest(value=value), self.assertRaises(Problem) as raised:
                self.store.login(value, 'Synthetic-password-621', 'synthetic-invalid-ip')
            self.assertEqual(raised.exception.status, 401)


if __name__ == '__main__':
    unittest.main()
