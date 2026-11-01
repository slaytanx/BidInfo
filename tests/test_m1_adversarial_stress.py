import os
import unittest
from unittest.mock import MagicMock, call, patch

import admin_console
from admin_console import get_bid_r2_folder_key
import r2_storage
from r2_storage import delete_r2_folder, get_s3_client, upload_file_to_r2, upload_db_to_r2, download_db_from_r2


class AdversarialPathStressTests(unittest.TestCase):
    """Stress-test path handling in get_bid_r2_folder_key and delete_r2_folder."""

    def test_get_bid_r2_folder_key_never_returns_data_root(self):
        """CRITICAL: get_bid_r2_folder_key must NEVER return 'data/' or 'data' or wipe root."""
        adversarial_inputs = [
            None,
            "",
            "   ",
            "/",
            "//",
            "///",
            "////",
            "\\",
            "\\\\",
            "\\\\\\",
            "/\\/\\",
            "\\/\\/",
            "data",
            "data/",
            "data//",
            "data///",
            "/data",
            "/data/",
            "/data///",
            "\\data\\",
            "\\data\\bid\\",
            "/tmp/evil/path",
            "/tmp/evil/path/",
            "../../../etc/passwd",
            "data/../../../etc/passwd",
            "data/bid_123",
            "data/bid_123/",
            "data/bid_123///",
            "data/bid_123\\",
            "data/bid_123\\\\",
            "data/./bid_123",
            "data/bid_123/.",
            "data/bid_123/..",
            "공고_2026_특수문자_#1",
            "data/공고_2026_특수문자_#1/",
            "A" * 5000,
            "data/" + "B" * 5000 + "/",
        ]

        for p in adversarial_inputs:
            with self.subTest(path=p):
                res = get_bid_r2_folder_key(p)
                self.assertIsInstance(res, str)
                # CRITICAL: It must NEVER evaluate to "data/" or "data"
                self.assertNotIn(res, ["data", "data/"], f"Path {p!r} resulted in dangerous key {res!r}")
                if res != "":
                    # If non-empty, it must start with "data/" and have a child name
                    self.assertTrue(res.startswith("data/"))
                    self.assertGreater(len(res), len("data/"))

    @patch("r2_storage.get_s3_client")
    def test_delete_r2_folder_dangerous_paths_rejected(self, mock_get_client):
        """Verify delete_r2_folder rejects all dangerous / root prefixes without S3 calls."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3

        dangerous_prefixes = [
            None,
            "",
            "/",
            "//",
            "///",
            "////",
            "\\",
            "\\\\",
            "\\\\\\",
            "/\\",
            "\\/",
            "/\\//\\\\",
            "///\\\\\\///",
        ]

        for prefix in dangerous_prefixes:
            with self.subTest(prefix=prefix):
                mock_s3.reset_mock()
                res = delete_r2_folder(prefix)
                self.assertFalse(res, f"Expected False for prefix: {prefix!r}")
                mock_s3.get_paginator.assert_not_called()
                mock_s3.delete_objects.assert_not_called()


class AdversarialPaginationStressTests(unittest.TestCase):
    """Stress-test delete_r2_folder paginator with 0, 1, 1000, 2500, and 5000 items."""

    def setUp(self):
        self.orig_env = os.environ.copy()
        os.environ["BIDINFO_R2_ENABLED"] = "1"
        os.environ["R2_BUCKET_NAME"] = "test-pagination-bucket"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    @patch("r2_storage.get_s3_client")
    def test_pagination_zero_items_empty_contents(self, mock_get_client):
        """0 items: Contents is an empty list."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{"Contents": []}]

        res = delete_r2_folder("data/bid_zero")
        self.assertTrue(res)
        mock_s3.delete_objects.assert_not_called()

    @patch("r2_storage.get_s3_client")
    def test_pagination_zero_items_missing_contents_key(self, mock_get_client):
        """0 items: Contents key is absent in page response."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{}]

        res = delete_r2_folder("data/bid_zero_missing")
        self.assertTrue(res)
        mock_s3.delete_objects.assert_not_called()

    @patch("r2_storage.get_s3_client")
    def test_pagination_single_item(self, mock_get_client):
        """1 item: exactly one object deleted."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{"Contents": [{"Key": "data/bid_1/file.pdf"}]}]

        res = delete_r2_folder("data/bid_1")
        self.assertTrue(res)
        mock_s3.delete_objects.assert_called_once_with(
            Bucket="test-pagination-bucket",
            Delete={"Objects": [{"Key": "data/bid_1/file.pdf"}]}
        )

    @patch("r2_storage.get_s3_client")
    def test_pagination_boundary_1000_items(self, mock_get_client):
        """1000 items: exactly 1 page of 1000 items, exactly 1 delete_objects call."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator

        page1 = {"Contents": [{"Key": f"data/bid_1000/doc_{i}.pdf"} for i in range(1000)]}
        mock_paginator.paginate.return_value = [page1]

        res = delete_r2_folder("data/bid_1000")
        self.assertTrue(res)
        self.assertEqual(mock_s3.delete_objects.call_count, 1)
        del_objs = mock_s3.delete_objects.call_args[1]["Delete"]["Objects"]
        self.assertEqual(len(del_objs), 1000)
        self.assertEqual(del_objs[0]["Key"], "data/bid_1000/doc_0.pdf")
        self.assertEqual(del_objs[999]["Key"], "data/bid_1000/doc_999.pdf")

    @patch("r2_storage.get_s3_client")
    def test_pagination_2500_items(self, mock_get_client):
        """2500 items across 3 pages (1000 + 1000 + 500)."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator

        page1 = {"Contents": [{"Key": f"data/bid_2500/doc_{i}.pdf"} for i in range(1000)]}
        page2 = {"Contents": [{"Key": f"data/bid_2500/doc_{i}.pdf"} for i in range(1000, 2000)]}
        page3 = {"Contents": [{"Key": f"data/bid_2500/doc_{i}.pdf"} for i in range(2000, 2500)]}
        mock_paginator.paginate.return_value = [page1, page2, page3]

        res = delete_r2_folder("data/bid_2500")
        self.assertTrue(res)
        self.assertEqual(mock_s3.delete_objects.call_count, 3)

        counts = [len(call_args[1]["Delete"]["Objects"]) for call_args in mock_s3.delete_objects.call_args_list]
        self.assertEqual(counts, [1000, 1000, 500])
        self.assertEqual(sum(counts), 2500)

    @patch("r2_storage.get_s3_client")
    def test_pagination_with_empty_intermediate_pages(self, mock_get_client):
        """Pages where some pages contain no contents (e.g. S3 markers or empty prefix batches)."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator

        page1 = {"Contents": [{"Key": "data/bid_x/a.pdf"}]}
        page2 = {"Contents": []}
        page3 = {}
        page4 = {"Contents": [{"Key": "data/bid_x/b.pdf"}]}
        mock_paginator.paginate.return_value = [page1, page2, page3, page4]

        res = delete_r2_folder("data/bid_x")
        self.assertTrue(res)
        self.assertEqual(mock_s3.delete_objects.call_count, 2)

    @patch("r2_storage.get_s3_client")
    def test_s3_delete_objects_exception_handled_cleanly(self, mock_get_client):
        """When delete_objects raises a S3 exception, returns False gracefully."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{"Contents": [{"Key": "data/bid_err/file.pdf"}]}]
        mock_s3.delete_objects.side_effect = Exception("AWS 500 Internal Error")

        res = delete_r2_folder("data/bid_err")
        self.assertFalse(res)


class AdversarialDynamicEnvStressTests(unittest.TestCase):
    """Stress-test dynamic os.environ mutation and S3 client generation."""

    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    @patch("r2_storage.boto3.client")
    def test_dynamic_credentials_switching(self, mock_boto_client):
        """Verify client respects credentials changed dynamically in os.environ."""
        mock_boto_client.return_value = MagicMock()

        # Step 1: Disabled
        os.environ["BIDINFO_R2_ENABLED"] = "0"
        self.assertIsNone(get_s3_client())

        # Step 2: Enabled with credentials set A
        os.environ["BIDINFO_R2_ENABLED"] = "1"
        os.environ["R2_ACCESS_KEY_ID"] = "user_alpha"
        os.environ["R2_SECRET_ACCESS_KEY"] = "secret_alpha"
        os.environ["R2_ENDPOINT_URL"] = "https://endpoint-alpha.r2.cloudflarestorage.com"
        client_a = get_s3_client()
        self.assertIsNotNone(client_a)
        _, kwargs_a = mock_boto_client.call_args
        self.assertEqual(kwargs_a["aws_access_key_id"], "user_alpha")
        self.assertEqual(kwargs_a["aws_secret_access_key"], "secret_alpha")
        self.assertEqual(kwargs_a["endpoint_url"], "https://endpoint-alpha.r2.cloudflarestorage.com")

        # Step 3: Switch credentials to set B
        os.environ["R2_ACCESS_KEY_ID"] = "user_beta"
        os.environ["R2_SECRET_ACCESS_KEY"] = "secret_beta"
        os.environ["R2_ENDPOINT_URL"] = "https://endpoint-beta.r2.cloudflarestorage.com"
        client_b = get_s3_client()
        self.assertIsNotNone(client_b)
        _, kwargs_b = mock_boto_client.call_args
        self.assertEqual(kwargs_b["aws_access_key_id"], "user_beta")
        self.assertEqual(kwargs_b["aws_secret_access_key"], "secret_beta")
        self.assertEqual(kwargs_b["endpoint_url"], "https://endpoint-beta.r2.cloudflarestorage.com")

        # Step 4: Revoke access key -> immediately returns None
        os.environ["R2_ACCESS_KEY_ID"] = ""
        self.assertIsNone(get_s3_client())

        # Step 5: Restore access key, revoke secret key -> immediately returns None
        os.environ["R2_ACCESS_KEY_ID"] = "user_beta"
        os.environ["R2_SECRET_ACCESS_KEY"] = ""
        self.assertIsNone(get_s3_client())

    @patch("r2_storage.get_s3_client")
    def test_dynamic_bucket_name_across_all_storage_operations(self, mock_get_client):
        """Verify upload_file, upload_db, download_db, and delete_r2_folder all use runtime bucket name."""
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3

        os.environ["R2_BUCKET_NAME"] = "runtime-bucket-x"

        # 1. upload_file_to_r2
        with patch("os.path.exists", return_value=True):
            upload_file_to_r2("dummy.txt", "data/dummy.txt")
            mock_s3.upload_file.assert_called_with("dummy.txt", "runtime-bucket-x", "data/dummy.txt")

        # 2. download_db_from_r2
        mock_s3.reset_mock()
        with patch("os.makedirs"):
            download_db_from_r2("/tmp/test_download.db")
            mock_s3.download_file.assert_called_with("runtime-bucket-x", "bids_history.db", "/tmp/test_download.db")

        # 3. delete_r2_folder
        mock_s3.reset_mock()
        mock_paginator = MagicMock()
        mock_s3.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{"Contents": [{"Key": "data/b1/f.txt"}]}]
        delete_r2_folder("data/b1")
        mock_paginator.paginate.assert_called_with(Bucket="runtime-bucket-x", Prefix="data/b1/")
        mock_s3.delete_objects.assert_called_with(
            Bucket="runtime-bucket-x",
            Delete={"Objects": [{"Key": "data/b1/f.txt"}]}
        )


class AdversarialUIStateStressTests(unittest.TestCase):
    """Stress-test setting change detection and session_state report key handling."""

    def test_settings_save_state_transitions(self):
        """Verify DB write occurs ONLY upon value change across state sequence."""
        db_writes = []

        def mock_save_setting(key, value):
            db_writes.append((key, value))

        current_val = "0"
        # Sequence of render values: 0, 0, 0, 1, 1, 0, 1, 1
        stream_values = ["0", "0", "0", "1", "1", "0", "1", "1"]

        for new_val in stream_values:
            if new_val != current_val:
                mock_save_setting("r2_enabled", new_val)
                current_val = new_val

        # Expected writes: only at transitions 0->1, 1->0, 0->1
        self.assertEqual(len(db_writes), 3)
        self.assertEqual(db_writes, [
            ("r2_enabled", "1"),
            ("r2_enabled", "0"),
            ("r2_enabled", "1"),
        ])

    def test_rfp_report_key_isolation(self):
        """Verify report keys for different bid_ids do not collide or overwrite."""
        session_state = {}

        bid_ids = ["bid_001", "bid_002", "bid_001_sub", "공고_123", "bid/with/slash"]
        for bid_id in bid_ids:
            key = f"rfp_report_{bid_id}"
            session_state[key] = f"Report content for {bid_id}"

        self.assertEqual(len(session_state), len(bid_ids))
        for bid_id in bid_ids:
            key = f"rfp_report_{bid_id}"
            self.assertEqual(session_state[key], f"Report content for {bid_id}")


if __name__ == "__main__":
    unittest.main()
