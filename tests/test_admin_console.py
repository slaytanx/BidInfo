import unittest

from admin_console import login_allowed


class AdminLoginTests(unittest.TestCase):
    def test_empty_expected_password_never_logs_in(self):
        self.assertFalse(login_allowed('', ''))
        self.assertFalse(login_allowed('', 'anything'))

    def test_only_exact_password_logs_in(self):
        self.assertTrue(login_allowed('pw1', 'pw1'))
        self.assertFalse(login_allowed('pw1', 'pw2'))
        self.assertFalse(login_allowed('pw1', ''))
