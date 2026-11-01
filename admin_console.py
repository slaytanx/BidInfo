import os
import shutil

import streamlit as st

from taxonomy_ui import show_taxonomy_editor
from llm_manager import get_secret, save_secrets
from db import load_all_bids, delete_bids, reset_all_bids, save_setting, DB_PATH
from r2_storage import delete_r2_folder, upload_db_to_r2

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def login_allowed(expected, typed):
    """비밀번호가 설정되어 있고 입력이 일치할 때만 로그인을 허용한다."""
    return bool(expected) and typed == expected


# 💡 시크릿 톱니바퀴 클릭 시 뜨는 🔒 시스템 어드민 관리자 팝업 모달창
@st.dialog("🔒 시스템 관리자 센터 (Admin Console)", width="large")
def show_admin_console_modal(expected_password):
    st.caption("모델 연결과 공고·첨부파일 관리를 제공합니다.")

    if not st.session_state["admin_logged_in"] and not expected_password:
        st.warning("⚠️ 관리자 비밀번호가 설정되지 않아 로그인할 수 없습니다. Streamlit Secrets에 ADMIN_PASSWORD를 설정하세요.")
    elif not st.session_state["admin_logged_in"]:
        st.info("🔐 **어드민 인증이 필요합니다.**")

        # 💡 st.form으로 감싸서 엔터키(Enter) 제출 및 로그인 버튼 지원
        with st.form("admin_login_form", clear_on_submit=False):
            col_l1, col_l2 = st.columns([3, 1])
            with col_l1:
                input_pass = st.text_input("🔑 관리자 비밀번호 입력", type="password", placeholder="비밀번호를 입력하고 엔터를 누르세요")
            with col_l2:
                st.write("")
                st.write("")
                login_submit = st.form_submit_button("🔓 로그인", type="primary", width="stretch")

            if login_submit:
                if login_allowed(expected_password, input_pass):
                    st.session_state["admin_logged_in"] = True
                    st.success("✅ 어드민 인증 성공!")
                    st.rerun()
                else:
                    st.error("❌ 비밀번호가 올바르지 않습니다.")
    else:
        col_hdr1, col_hdr2 = st.columns([4, 1])
        with col_hdr1:
            st.success("🔓 **어드민 관리자로 로그인되었습니다.**")
        with col_hdr2:
            if st.button("🔒 로그아웃", width="stretch"):
                st.session_state["admin_logged_in"] = False
                st.rerun()

        st.markdown("---")

        show_taxonomy_editor()

        st.markdown("### 모델 연결")
        st.caption("API 키는 로컬 비밀 파일에 저장하며 공고 DB와 R2에는 포함하지 않습니다.")
        with st.form("model_credentials"):
            openrouter_key = st.text_input("OpenRouter API 키", type="password", value=get_secret("OPENROUTER_API_KEY"))
            new_admin_pass = st.text_input("관리자 비밀번호 변경", type="password")
            if st.form_submit_button("연결 설정 저장"):
                save_secrets({"OPENROUTER_API_KEY": openrouter_key})
                if new_admin_pass.strip():
                    save_setting("admin_password", new_admin_pass.strip())
                st.success("로컬 연결 설정을 저장했습니다.")
        st.markdown("---")

        # 2. 불필요한 데이터 & 첨부파일 통합 삭제 센터
        st.markdown("### 🗑️ 불필요 데이터 & 첨부파일 통합 삭제 센터")
        st.caption("선택한 공고를 DB에서 제거하고, 컴퓨터 로컬 폴더 및 Cloudflare R2 스토리지 내부 파일까지 한 번에 깨끗이 지웁니다.")

        all_bids_for_admin = load_all_bids()
        if not all_bids_for_admin:
            st.info("현재 저장된 공고 데이터가 없습니다.")
        else:
            bid_map = {f"[{b['num']}] [{b['org']}] {b['title']} ({b['reg_date']})": b for b in all_bids_for_admin}
            selected_del_labels = st.multiselect("🗑️ 삭제할 공고 목록 선택 (다중 선택 가능)", options=list(bid_map.keys()))

            col_del1, col_del2 = st.columns([2, 1])
            with col_del1:
                if st.button("🚨 선택한 공고 및 첨부파일/R2 동기화 완결 삭제", type="primary", width="stretch"):
                    if not selected_del_labels:
                        st.warning("삭제할 공고를 선택해 주세요.")
                    else:
                        del_ids = []
                        for label in selected_del_labels:
                            bid_item = bid_map[label]
                            del_ids.append(bid_item["bid_id"])

                            folder_p = bid_item.get("folder_path", "")
                            if folder_p and os.path.exists(folder_p):
                                try:
                                    shutil.rmtree(folder_p)
                                except Exception as ex:
                                    print(f"⚠️ 로컬 폴더 삭제 실패: {ex}")

                            if folder_p:
                                folder_bname = os.path.basename(folder_p)
                                delete_r2_folder(f"data/{folder_bname}")

                        delete_bids(del_ids)
                        upload_db_to_r2(DB_PATH)

                        st.success(f"🎉 선택한 {len(del_ids)}건의 공고 및 로컬/R2 첨부파일이 완전히 삭제되었습니다!")
                        st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)
            with st.expander("💣 [주의] 전체 공고 및 파일 클린 포맷"):
                st.warning("⚠️ 저장된 모든 공고 데이터와 로컬 data/ 폴더 및 Cloudflare R2 파일이 전부 삭제됩니다.")
                if st.button("💥 전체 공고 데이터 & 파일 클린 포맷 실행"):
                    reset_all_bids()
                    if os.path.exists(DATA_DIR):
                        for item in os.listdir(DATA_DIR):
                            item_p = os.path.join(DATA_DIR, item)
                            if item != "bids_history.db" and os.path.isdir(item_p):
                                shutil.rmtree(item_p)
                    delete_r2_folder("data")
                    upload_db_to_r2(DB_PATH)
                    st.success("💥 전체 공고 및 첨부파일이 초기화 포맷 되었습니다!")
                    st.rerun()

    if st.button("닫기", type="primary", width="stretch"):
        st.session_state["show_admin_modal"] = False
        st.rerun()
