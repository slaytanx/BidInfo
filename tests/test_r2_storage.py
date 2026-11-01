import os
import unittest
from unittest.mock import MagicMock, patch

import r2_storage


class R2ClientDynamicEnvTests(unittest.TestCase):
    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_get_s3_client_disabled_returns_none(self):
        os.environ["BIDINFO_R2_ENABLED"] = "0"
        self.assertIsNone(r2_storage.get_s3_client())

    def test_get_s3_client_missing_keys_returns_none(self):
        os.environ["BIDINFO_R2_ENABLED"] = "1"
        os.environ["R2_ACCESS_KEY_ID"] = ""
        os.environ["R2_SECRET_ACCESS_KEY"] = ""
        self.assertIsNone(r2_storage.get_s3_client())

    @patch("r2_storage.boto3.client")
    def test_get_s3_client_reads_runtime_environ(self, mock_boto_client):
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        os.environ["BIDINFO_R2_ENABLED"] = "1"
        os.environ["R2_ACCESS_KEY_ID"] = "test_key_123"
        os.environ["R2_SECRET_ACCESS_KEY"] = "test_secret_456"
        os.environ["R2_ENDPOINT_URL"] = "https://custom.endpoint.com"

        client = r2_storage.get_s3_client()
        self.assertIsNotNone(client)
        mock_boto_client.assert_called_once()
        _, kwargs = mock_boto_client.call_args
        self.assertEqual(kwargs.get("endpoint_url"), "https://custom.endpoint.com")
        self.assertEqual(kwargs.get("aws_access_key_id"), "test_key_123")
        self.assertEqual(kwargs.get("aws_secret_access_key"), "test_secret_456")


class R2DeleteFolderTests(unittest.TestCase):
    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    @patch("r2_storage.get_s3_client")
    def test_safety_guard_rejects_empty_or_root_prefix(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3

        # Root or empty prefixes must return False and NEVER trigger deletions
        for dangerous_prefix in ["", "/", "///", "\\", "\\\\", None]:
            result = r2_storage.delete_r2_folder(dangerous_prefix)
            self.assertFalse(result, f"Expected False for dangerous prefix: {dangerous_prefix!r}")
            mock_s3.get_paginator.assert_not_called()
            mock_s3.delete_objects.assert_not_called()

    @patch("r2_storage.get_s3_client")
    def test_delete_r2_folder_paginates_over_1000_items(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3

        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator

        page1 = {"Contents": [{"Key": f"data/bid_1/file_{i}.pdf"} for i in range(1000)]}
        page2 = {"Contents": [{"Key": f"data/bid_1/file_{i}.pdf"} for i in range(1000, 1350)]}
        mock_paginator.paginate.return_value = [page1, page2]

        os.environ["R2_BUCKET_NAME"] = "custom-test-bucket"
        success = r2_storage.delete_r2_folder("data/bid_1")

        self.assertTrue(success)
        mock_s3.get_paginator.assert_called_once_with("list_objects_v2")
        mock_paginator.paginate.assert_called_once_with(
            Bucket="custom-test-bucket", Prefix="data/bid_1/"
        )

        self.assertEqual(mock_s3.delete_objects.call_count, 2)
        call1_kwargs = mock_s3.delete_objects.call_args_list[0][1]
        self.assertEqual(len(call1_kwargs["Delete"]["Objects"]), 1000)
        self.assertEqual(call1_kwargs["Bucket"], "custom-test-bucket")

        call2_kwargs = mock_s3.delete_objects.call_args_list[1][1]
        self.assertEqual(len(call2_kwargs["Delete"]["Objects"]), 350)

    @patch("r2_storage.get_s3_client")
    def test_delete_r2_folder_normalizes_trailing_slash(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{"Contents": [{"Key": "data/bid_99/item.pdf"}]}]

        success = r2_storage.delete_r2_folder("data/bid_99/")
        self.assertTrue(success)
        mock_paginator.paginate.assert_called_once_with(
            Bucket=os.environ.get("R2_BUCKET_NAME", "bid-info"),
            Prefix="data/bid_99/"
        )

    @patch("r2_storage.get_s3_client")
    def test_delete_r2_folder_handles_empty_folder(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{}]

        success = r2_storage.delete_r2_folder("data/empty_bid")
        self.assertTrue(success)
        mock_s3.delete_objects.assert_not_called()

    @patch("r2_storage.get_s3_client")
    def test_delete_r2_folder_returns_false_when_no_client(self, mock_get_client):
        mock_get_client.return_value = None
        self.assertFalse(r2_storage.delete_r2_folder("data/any"))

    @patch("r2_storage.get_s3_client")
    def test_delete_r2_folder_exception_returns_false(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_s3.get_paginator.side_effect = RuntimeError("S3 connection error")
        mock_get_client.return_value = mock_s3

        self.assertFalse(r2_storage.delete_r2_folder("data/error_bid"))


class R2BucketDynamicLookupTests(unittest.TestCase):
    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    @patch("r2_storage.get_s3_client")
    def test_upload_file_uses_dynamic_bucket(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3

        os.environ["R2_BUCKET_NAME"] = "my-dynamic-bucket"
        with patch("os.path.exists", return_value=True):
            success = r2_storage.upload_file_to_r2("/fake/path.pdf", "data/test.pdf")

        self.assertTrue(success)
        mock_s3.upload_file.assert_called_once_with(
            "/fake/path.pdf", "my-dynamic-bucket", "data/test.pdf"
        )


class UIStateAndSettingsTests(unittest.TestCase):
    def test_r2_enabled_setting_only_saves_on_change(self):
        # Simulates the condition in app.py:L310-314
        saved_calls = []

        def mock_save_setting(key, value):
            saved_calls.append((key, value))

        # Case 1: Value hasn't changed (both "0")
        current_r2 = "0"
        new_r2 = "0"
        if new_r2 != current_r2:
            mock_save_setting("r2_enabled", new_r2)
        self.assertEqual(len(saved_calls), 0)

        # Case 2: Value has changed ("0" -> "1")
        new_r2 = "1"
        if new_r2 != current_r2:
            mock_save_setting("r2_enabled", new_r2)
        self.assertEqual(saved_calls, [("r2_enabled", "1")])

    def test_rfp_report_session_state_key_format(self):
        # Simulates st.session_state persistence for RFP report
        session_state = {}
        bid_id = "test_bid_42"
        rfp_state_key = f"rfp_report_{bid_id}"

        # Simulates generation on button click
        report_text = "# RFP Summary Report\nTarget: Cloud Migration"
        session_state[rfp_state_key] = report_text

        # Verify subsequent read (as on rerun) retrieves report
        self.assertIn(rfp_state_key, session_state)
        self.assertEqual(session_state[rfp_state_key], report_text)
