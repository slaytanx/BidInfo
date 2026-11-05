import unittest

import requests

from ui_errors import friendly_error


class FriendlyErrorTests(unittest.TestCase):
    def test_known_errors_get_actionable_message_with_detail(self):
        for error in (requests.ConnectionError("x"), requests.Timeout("x"), PermissionError("x")):
            message, has_detail = friendly_error(error)
            self.assertTrue(has_detail)
            self.assertNotIn("x", message)

    def test_intentional_errors_pass_through_without_detail(self):
        self.assertEqual(friendly_error(ValueError("분야를 선택하세요")), ("분야를 선택하세요", False))

    def test_unexpected_error_hides_raw_text_but_names_type(self):
        message, has_detail = friendly_error(KeyError("secret-token"))
        self.assertTrue(has_detail)
        self.assertIn("KeyError", message)
        self.assertNotIn("secret-token", message)
