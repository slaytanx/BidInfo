import unittest

from admin_console import login_allowed, get_bid_r2_folder_key


class AdminLoginTests(unittest.TestCase):
    def test_empty_expected_password_never_logs_in(self):
        self.assertFalse(login_allowed('', ''))
        self.assertFalse(login_allowed('', 'anything'))

    def test_only_exact_password_logs_in(self):
        self.assertTrue(login_allowed('pw1', 'pw1'))
        self.assertFalse(login_allowed('pw1', 'pw2'))
        self.assertFalse(login_allowed('pw1', ''))


class AdminR2DeletionPathTests(unittest.TestCase):
    def test_normal_folder_path(self):
        self.assertEqual(get_bid_r2_folder_key("data/bid_123"), "data/bid_123")

    def test_trailing_slash_does_not_wipe_data_root(self):
        # Trailing slashes should be stripped before basename so basename != ""
        self.assertEqual(get_bid_r2_folder_key("data/bid_123/"), "data/bid_123")
        self.assertEqual(get_bid_r2_folder_key("data/bid_123\\"), "data/bid_123")
        self.assertEqual(get_bid_r2_folder_key("data/bid_123///"), "data/bid_123")
        self.assertNotEqual(get_bid_r2_folder_key("data/bid_123/"), "data/")

    def test_empty_or_root_path_returns_empty(self):
        self.assertEqual(get_bid_r2_folder_key(""), "")
        self.assertEqual(get_bid_r2_folder_key("/"), "")
        self.assertEqual(get_bid_r2_folder_key("///"), "")
        self.assertEqual(get_bid_r2_folder_key("\\"), "")
        self.assertEqual(get_bid_r2_folder_key(None), "")

