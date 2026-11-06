import unittest
from pathlib import Path
from unittest.mock import patch

import crawler
import db
import pipeline
from admin_console import get_bid_r2_folder_key, login_allowed


class AdminLoginTests(unittest.TestCase):
    def test_empty_expected_password_never_logs_in(self):
        self.assertFalse(login_allowed('', ''))
        self.assertFalse(login_allowed('', 'anything'))

    def test_only_exact_password_logs_in(self):
        self.assertTrue(login_allowed('pw1', 'pw1'))
        self.assertFalse(login_allowed('pw1', 'pw2'))
        self.assertFalse(login_allowed('pw1', ''))

    def test_login_allowed_int_vs_str(self):
        """Regression: Verify integer password in secrets matches string input from text_input."""
        self.assertTrue(login_allowed(1234, "1234"))
        self.assertTrue(login_allowed("1234", 1234))
        self.assertTrue(login_allowed(1234, 1234))
        self.assertFalse(login_allowed(1234, "5678"))
        self.assertFalse(login_allowed(1234, 5678))

    def test_login_allowed_none_and_empty(self):
        """Regression: Verify None, empty string, and whitespace never allow login."""
        self.assertFalse(login_allowed(None, "1234"))
        self.assertFalse(login_allowed("1234", None))
        self.assertFalse(login_allowed(None, None))
        self.assertFalse(login_allowed(None, ""))
        self.assertFalse(login_allowed("", None))
        self.assertFalse(login_allowed("", ""))
        self.assertFalse(login_allowed("   ", "   "))
        self.assertFalse(login_allowed(0, ""))


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


class SidebarWorkflowTests(unittest.TestCase):
    """Verify sidebar 3-step workflow layout and absence of removed admin widgets."""

    def test_sidebar_three_step_workflow_and_absence_of_admin_widgets(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
        self.assertFalse(app.exception)

        # 1. Verify 3-step subheaders in sidebar
        subheaders = [w.value for w in app.sidebar.subheader]
        self.assertIn("1단계: 수집 대상 선택", subheaders)
        self.assertIn("2단계: 수집 분야 & 모델", subheaders)
        self.assertIn("3단계: 실행", subheaders)

        # 2. Verify absence of admin headers in sidebar
        self.assertNotIn("적합도 가중치 키워드 설정", subheaders)

        # 3. Verify step 1 widgets
        sidebar_selectboxes = [w.label for w in app.sidebar.selectbox]
        sidebar_text_inputs = [w.label for w in app.sidebar.text_input]
        sidebar_dates = [w.label for w in app.sidebar.date_input]

        self.assertIn("수집 대상 사이트 선택", sidebar_selectboxes)
        self.assertNotIn("수집 대상 URL", sidebar_text_inputs)
        self.assertIn("시작일", sidebar_dates)
        self.assertIn("종료일", sidebar_dates)
        self.assertIn("검색어 필터 (선택)", sidebar_text_inputs)

        # 4. Verify step 2 widgets
        sidebar_multiselects = [w.label for w in app.sidebar.multiselect]
        sidebar_checkboxes = [w.label for w in app.sidebar.checkbox]

        self.assertIn("수집 분야 (복수 선택)", sidebar_multiselects)
        self.assertIn("수집 세부 항목 (비우면 분야 전체)", sidebar_multiselects)
        self.assertIn("수집 업무 (비우면 업무 전체)", sidebar_multiselects)
        self.assertIn("처리 방식", sidebar_selectboxes)
        self.assertIn("애매한 공고도 첨부를 확인", sidebar_checkboxes)
        self.assertIn("기존 공고의 첨부도 새로 확인", sidebar_checkboxes)

        # 5. Verify step 3 widgets
        sidebar_buttons = [w.label for w in app.sidebar.button]
        self.assertIn("수집 시작", sidebar_buttons)

        # 6. Verify absence of the 6 removed admin widgets
        self.assertNotIn("1차 핵심 분야 (가중치 높음)", sidebar_text_inputs)
        self.assertNotIn("2차 관련 분야 (가중치 보통)", sidebar_text_inputs)
        sidebar_numbers = [w.label for w in app.sidebar.number_input]
        self.assertNotIn("실행당 클라우드 예산 (USD)", sidebar_numbers)
        self.assertNotIn("R2 동기화 사용", sidebar_checkboxes)
        self.assertNotIn("로컬 DB·첨부를 R2에 동기화", sidebar_buttons)
        self.assertNotIn("기존 공고 문서 재추출·분류", sidebar_buttons)
        self.assertNotIn("선택 분야 기존 공고 재분석", sidebar_buttons)
        self.assertNotIn("선택 모델로 비교 실행", sidebar_buttons)
        self.assertNotIn("사이트 이름", sidebar_text_inputs)
        self.assertNotIn("사이트 URL", sidebar_text_inputs)
        self.assertNotIn("사이트 저장", sidebar_buttons)
        self.assertNotIn("사이트 삭제", sidebar_buttons)


class AdminConsoleTabsAndPersistenceTests(unittest.TestCase):
    """Verify admin console 4 tabs structure and settings persistence."""

    def test_settings_persistence(self):
        db.save_setting("pri_kw", "핵심인공지능")
        db.save_setting("sec_kw", "클라우드인프라")
        db.save_setting("run_budget", "3.5")
        db.save_setting("r2_enabled", "1")

        self.assertEqual(db.get_setting("pri_kw"), "핵심인공지능")
        self.assertEqual(db.get_setting("sec_kw"), "클라우드인프라")
        self.assertEqual(db.get_setting("run_budget"), "3.5")
        self.assertEqual(db.get_setting("r2_enabled"), "1")

    def test_delete_bid_items_reports_local_failure(self):
        import admin_console

        items = [{"bid_id": "a", "folder_path": "/x/a"}, {"bid_id": "b", "folder_path": ""}]
        with patch.object(admin_console.os.path, "exists", return_value=True), \
             patch.object(admin_console.shutil, "rmtree", side_effect=OSError("busy")), \
             patch.object(admin_console, "get_bid_r2_folder_key", return_value=None), \
             patch.object(admin_console, "delete_bids") as delete, \
             patch.object(admin_console, "upload_db_to_r2"):
            ids, failed = admin_console.delete_bid_items(items)
        self.assertEqual(ids, ["a", "b"])
        self.assertEqual(failed, ["a"])
        delete.assert_called_once_with(["a", "b"])

    def test_admin_console_locked_without_login(self):
        from streamlit.testing.v1 import AppTest

        script = """
import streamlit as st
from admin_console import show_admin_console_modal

show_admin_console_modal("pw1")
"""
        app = AppTest.from_string(script).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.tabs), 0)

        app.text_input[0].set_value("wrong")
        app.button[0].click().run()
        self.assertEqual(len(app.tabs), 0)
        self.assertTrue(app.error)

        app.text_input[0].set_value("pw1")
        app.button[0].click().run()
        self.assertGreater(len(app.tabs), 0)

    def test_admin_console_closed_when_password_unset(self):
        from streamlit.testing.v1 import AppTest

        script = """
import streamlit as st
from admin_console import show_admin_console_modal

show_admin_console_modal("")
"""
        app = AppTest.from_string(script).run(timeout=30)
        self.assertEqual(len(app.tabs), 0)
        self.assertEqual(len(app.text_input), 0)
        self.assertTrue(app.error)

    def test_admin_console_tabs_structure(self):
        from streamlit.testing.v1 import AppTest

        script = """
import streamlit as st
from admin_console import show_admin_console_modal

st.session_state["admin_logged_in"] = True
show_admin_console_modal("dummy_pw")
"""
        app = AppTest.from_string(script).run(timeout=30)
        self.assertFalse(app.exception)

        tab_labels = [t.label for t in app.tabs]
        self.assertIn("AI 모델 연결", tab_labels)
        self.assertIn("수집 사이트 & Cloudflare R2 백업", tab_labels)
        self.assertIn("데이터 재처리 & 벤치마크", tab_labels)
        self.assertIn("분류 사전 & 공고 삭제 센터", tab_labels)


class RunCrawlSettingsRetrievalTests(unittest.TestCase):
    """Verify run_crawl retrieves pri_kw and sec_kw from get_setting."""

    def test_run_crawl_retrieves_settings_from_db_and_does_not_overwrite(self):
        from streamlit.testing.v1 import AppTest

        # Set specific settings in DB
        db.save_setting("pri_kw", "차세대네트워크")
        db.save_setting("sec_kw", "정보보호체계")

        with patch.object(crawler, "crawl_and_download_bids_by_date") as mock_crawl, \
             patch.object(pipeline, "process_bids") as mock_process:

            mock_crawl.return_value = [{"게시물ID": "test_1", "제목": "테스트 공고"}]
            mock_process.return_value = ([], {"analyzed": 1})

            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
            self.assertFalse(app.exception)

            # Trigger crawl
            crawl_btn = next(b for b in app.sidebar.button if b.label == "수집 시작")
            crawl_btn.click()
            app.run(timeout=30)
            self.assertFalse(app.exception)

            # Assert crawl and process were called
            self.assertTrue(mock_crawl.called)
            self.assertTrue(mock_process.called)

            args, _kwargs = mock_process.call_args
            # process_bids(bids, selected_categories, engine_configs, pri_kw, sec_kw, on_progress)
            passed_pri_kw = args[3]
            passed_sec_kw = args[4]
            self.assertEqual(passed_pri_kw, "차세대네트워크")
            self.assertEqual(passed_sec_kw, "정보보호체계")

            # Verify settings in DB were NOT overwritten during run_crawl
            self.assertEqual(db.get_setting("pri_kw"), "차세대네트워크")
            self.assertEqual(db.get_setting("sec_kw"), "정보보호체계")


if __name__ == "__main__":
    unittest.main()
