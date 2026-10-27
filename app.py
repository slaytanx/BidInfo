import os
import json
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, date
import streamlit as st
import pandas as pd

from crawler import crawl_and_download_bids_by_date
from classification import CATEGORIES, DEFAULT_CATEGORIES
from taxonomy import taxonomy_options, DEFAULT_DOMAINS
from taxonomy_ui import show_taxonomy_editor, show_review_queue
from pipeline import process_bids, backfill_existing, reanalyze_existing
from llm_manager import engine_configs, RunBudget, CLOUD_MODELS, LOCAL_MODELS, get_secret, save_secrets
from hwp_parser import process_folder_documents
from ollama_summary import analyze_bid_with_ollama, answer_bid_question, generate_rfp_one_pager
from db import (
    init_db, save_bid, load_all_bids, save_site, load_all_sites,
    delete_site, save_setting, get_setting, update_bid_status,
    delete_bids, reset_all_bids, DB_PATH
)
from r2_storage import download_db_from_r2, upload_db_to_r2, upload_file_to_r2, delete_r2_folder
from date_utils import calculate_d_day


st.set_page_config(page_title="입찰 통합 분석 & 제안 파이프라인", layout="wide")

# 💡 Streamlit Secrets 예외 무풍지대 안전 로드
admin_pass_secret = None
try:
    sec = getattr(st, "secrets", None)
    if sec is not None:
        if "R2_ACCESS_KEY_ID" in sec:
            os.environ["R2_ACCESS_KEY_ID"] = sec["R2_ACCESS_KEY_ID"]
        if "R2_SECRET_ACCESS_KEY" in sec:
            os.environ["R2_SECRET_ACCESS_KEY"] = sec["R2_SECRET_ACCESS_KEY"]
        if "R2_ENDPOINT_URL" in sec:
            os.environ["R2_ENDPOINT_URL"] = sec["R2_ENDPOINT_URL"]
        if "R2_BUCKET_NAME" in sec:
            os.environ["R2_BUCKET_NAME"] = sec["R2_BUCKET_NAME"]
        if "OPENROUTER_API_KEY" in sec:
            os.environ["OPENROUTER_API_KEY"] = sec["OPENROUTER_API_KEY"]
        admin_pass_secret = sec.get("ADMIN_PASSWORD", None)
except BaseException:
    admin_pass_secret = None

def get_admin_password():
    return admin_pass_secret or get_setting("admin_password", "admin123!")

# 💡 R2 스토리지에서 최신 bids_history.db 영구 데이터 다운로드 동기화
init_db()
os.environ["BIDINFO_R2_ENABLED"] = get_setting("r2_enabled", "0")

# --- 세션 상태 초기화 ---
if "modal_target_bid" not in st.session_state:
    st.session_state["modal_target_bid"] = None
if "qa_preset_text" not in st.session_state:
    st.session_state["qa_preset_text"] = ""
if "admin_logged_in" not in st.session_state:
    st.session_state["admin_logged_in"] = False
if "show_admin_modal" not in st.session_state:
    st.session_state["show_admin_modal"] = False

# --- CSS 가독성 및 수평 수직 정밀 픽셀 정렬 스타일 정의 ---
st.markdown("""
<style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");

    html, body, .stApp, h1, h2, h3, h4, h5, h6, p, input, select, textarea, label, div.stMarkdown, div.stButton > button, div[data-testid="stTable"] {
        font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, system-ui, Roboto, "Helvetica Neue", "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif !important;
    }

    /* 💡 Streamlit Material Symbols 아이콘 폰트 정상 복원 */
    [data-testid="stIcon"], [class*="material-symbols"], i, span[aria-hidden="true"], [data-testid="stSidebarCollapseButton"] *, [data-testid="stExpanderToggleIcon"] * {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Source Sans Pro', sans-serif !important;
    }

    .stApp, .main, [data-testid="stHeader"] {
        background-color: transparent !important;
    }
    .block-container {
        padding-top: 2.5rem !important;
        padding-bottom: 1rem !important;
    }
    h1 {
        margin-top: 0px !important;
        padding-top: 0px !important;
        background: transparent !important;
    }
    hr {
        margin-top: 0.3rem !important;
        margin-bottom: 8px !important;
    }
    h3 {
        margin-top: 12px !important;
        padding-top: 0px !important;
        margin-bottom: 0.4rem !important;
    }
    .stButton button, .stButton button p {
        white-space: nowrap !important;
        word-break: keep-all !important;
        font-size: 14px !important;
    }
    [data-testid="stDataFrame"] [role="columnheader"],
    [data-testid="stDataFrame"] [role="columnheader"] div,
    [data-testid="stDataFrame"] [role="columnheader"] span,
    div[data-testid="stTable"] th,
    .stDataFrame table th {
        text-align: center !important;
        justify-content: center !important;
        align-items: center !important;
        display: flex !important;
        margin: 0 auto !important;
    }
    div[data-testid="stDataFrame"] {
        width: 100% !important;
    }
    div[data-testid="stDataFrame"] > div {
        overflow-x: auto !important;
    }

    /* 💖 LG U+ 마젠타 브랜드 Primary 버튼 & 수집 시작 버튼 강조 스타일 💖 */
    [data-testid="stSidebar"] button[kind="primary"],
    [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"],
    [data-testid="stSidebar"] div[data-testid="stBaseButton-primary"] button,
    button[kind="primary"],
    button[data-testid="stBaseButton-primary"],
    div[data-testid="stBaseButton-primary"] button,
    .stButton > button[data-testid="stBaseButton-primary"] {
        background: linear-gradient(135deg, #FF0084 0%, #C4006B 100%) !important;
        background-color: #E6007E !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 700 !important;
        box-shadow: 0 4px 14px rgba(230, 0, 126, 0.4) !important;
        transition: all 0.2s ease-in-out !important;
    }
    [data-testid="stSidebar"] button[kind="primary"] p,
    [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] p,
    [data-testid="stSidebar"] div[data-testid="stBaseButton-primary"] button p,
    button[kind="primary"] p,
    button[data-testid="stBaseButton-primary"] p,
    div[data-testid="stBaseButton-primary"] button p,
    .stButton > button[data-testid="stBaseButton-primary"] p {
        color: #ffffff !important;
        font-weight: 700 !important;
        font-size: 15px !important;
    }
    [data-testid="stSidebar"] button[kind="primary"]:hover,
    [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover,
    [data-testid="stSidebar"] div[data-testid="stBaseButton-primary"] button:hover,
    button[kind="primary"]:hover,
    button[data-testid="stBaseButton-primary"]:hover,
    div[data-testid="stBaseButton-primary"] button:hover {
        background: linear-gradient(135deg, #D40073 0%, #A80055 100%) !important;
        background-color: #C4006B !important;
        box-shadow: 0 6px 18px rgba(230, 0, 126, 0.6) !important;
        transform: translateY(-1px) !important;
    }

    /* 💥 ⚙️ 어드민 톱니바퀴 버튼 회색 테두리 및 배경 완벽 투명화 & 하향 정렬 💥 */
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button[data-testid="stBaseButton-secondary"],
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button[kind="secondary"] {
        background: transparent !important;
        background-color: transparent !important;
        border: none !important;
        border-style: none !important;
        outline: none !important;
        box-shadow: none !important;
        padding: 0px !important;
        margin: 0px !important;
        width: auto !important;
        min-width: 0px !important;
        height: auto !important;
        min-height: 0px !important;
        transform: translateY(11px) !important; /* ⚙️ 아이콘 추가 하향 정렬 */
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button[data-testid="stBaseButton-secondary"] p,
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button[kind="secondary"] p {
        font-size: 1.25rem !important;
        line-height: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button[data-testid="stBaseButton-secondary"]:hover,
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button[kind="secondary"]:hover {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        opacity: 0.7;
    }

    /* 어드민 로그인 폼 외곽선 깔끔 제거 */
    [data-testid="stForm"] {
        border: none !important;
        padding: 0 !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("입찰 공고 수집·분석")
st.caption("분야별 입찰 수집, 문서 인식, 로컬·OpenRouter 분석과 제안 진행 관리를 제공합니다.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# 💡 공고 상세 리포트 팝업 모달창
@st.dialog("📄 입찰 공고 상세 리포트", width="large")
def show_bid_detail_modal(row):
    star_mark = "⭐ 관심공고 찜" if row["starred"] == 1 else "☆ 일반공고"
    st.markdown(f"### {row['title']}")
    st.caption(f"수집 사이트: {row.get('site_name', '미지정')} | 계열사: {row['org']} | 등록일: {row['reg_date']} | {star_mark}")

    origin_link_url = row.get("origin_url", "") or row.get("url", "")
    if origin_link_url:
        st.link_button("🌐 실제 원본 공고 게시글로 이동 (새 창)", origin_link_url, width="stretch")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("진행 상태", row["status"])
    with col2:
        st.metric("D-Day 마감", row["D-Day"])
    with col3:
        st.metric("AI 적합도 점수", f"{row['fit_score']}점")

    st.markdown("---")
    st.markdown("#### 🎯 AI 적합 사유")
    st.info(row["fit_reason"])

    st.caption("분야: " + ", ".join(json.loads(row.get("tags", "[]") or "[]")))
    st.caption("세부 항목: " + ", ".join(json.loads(row.get("solution_tags", "[]") or "[]")))
    st.caption("업무: " + ", ".join(json.loads(row.get("work_tags", "[]") or "[]")))
    st.caption("분류 근거: " + row.get("classification_reason", ""))
    st.caption(f"문서: {row.get('extraction_status', '미확인')} / 분석: {row.get('analysis_status', '미확인')}")
    with st.expander("문서 추출 상세·분석 근거"):
        st.json(json.loads(row.get("extraction_details", "{}") or "{}"))
        st.json(json.loads(row.get("evidence", "[]") or "[]"))
    st.markdown("#### 💡 AI 사업 요약")
    st.write(row["summary"])

    st.markdown("---")
    st.markdown("#### 📋 제출 및 서류 체크리스트")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"⏰ **제출 마감일시**: `{row.get('deadline', '상세참조')}`")
        st.markdown(f"📨 **제출 방식**: `{row.get('submit_type', '상세참조')}`")
    with c2:
        st.markdown(f"📜 **필수 제출 서류**: `{row.get('required_docs', '상세참조')}`")
        st.markdown(f"✅ **참가 자격 요건**: `{row.get('qualifications', '상세참조')}`")

    if st.button("닫기", type="primary", width="stretch"):
        st.session_state["modal_target_bid"] = None
        st.rerun()

# 💡 시크릿 톱니바퀴 클릭 시 뜨는 🔒 시스템 어드민 관리자 팝업 모달창
@st.dialog("🔒 시스템 관리자 센터 (Admin Console)", width="large")
def show_admin_console_modal():
    st.caption("모델 연결과 공고·첨부파일 관리를 제공합니다.")

    if not st.session_state["admin_logged_in"]:
        st.info("🔐 **어드민 인증이 필요합니다.** (기본 비밀번호: `admin123!`)")

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
                if input_pass == get_admin_password():
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

# --- 1차/2차/3차 엔진 구성 함수 ---
def get_configured_engine_list():
    return engine_configs(globals().get("analysis_mode", get_setting("analysis_mode", "혼합")),
                          globals().get("cloud_model", get_setting("cloud_model", CLOUD_MODELS[0])),
                          globals().get("local_model", get_setting("local_model", LOCAL_MODELS[0])),
                          RunBudget(float(globals().get("run_budget", get_setting("run_budget", "1")))))

# --- 사이드바 설정 영역 ---
with st.sidebar:
    # 💡 ⚙️ 아이콘과 텍스트 수평 센터(baseline) 100% 정밀 맞춤
    col_ic, col_txt = st.columns([0.45, 5.55])
    with col_ic:
        if st.button("⚙️", help="🔒 관리자 어드민 콘솔 열기"):
            st.session_state["show_admin_modal"] = True
            st.rerun()
    with col_txt:
        st.markdown("### 수집 & 필터 설정")

    st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
    saved_pri_kw = get_setting("pri_kw", "통신")
    saved_sec_kw = get_setting("sec_kw", "네트워크, 보안")

    st.subheader("🔑 적합도 가중치 키워드 설정")
    pri_kw = st.text_input("1차 핵심 분야 (가중치 높음)", value=saved_pri_kw)
    sec_kw = st.text_input("2차 관련 분야 (가중치 보통)", value=saved_sec_kw)

    st.markdown("---")
    st.header("🚀 수집 & 데이터 내보내기")
    st.subheader("분야와 분석 모델")
    selected_categories = st.multiselect("수집 분야 (복수 선택)", CATEGORIES,
        default=json.loads(get_setting("selected_categories", json.dumps(DEFAULT_CATEGORIES))))
    solution_options = taxonomy_options("세부 항목", selected_categories)
    selected_solutions = st.multiselect("수집 세부 항목 (비우면 분야 전체)", solution_options,
        default=[x for x in json.loads(get_setting("selected_solutions", "[]")) if x in solution_options])
    work_options = taxonomy_options("업무")
    selected_work = st.multiselect("수집 업무 (비우면 업무 전체)", work_options,
        default=[x for x in json.loads(get_setting("selected_work", "[]")) if x in work_options])
    include_uncertain = st.checkbox("애매한 공고도 첨부를 확인", value=False)
    st.caption("같은 축에서는 하나라도 일치, 분야·세부 항목·업무 사이에서는 모두 충족해야 수집합니다. 기본값은 관련 확인된 공고만 수집하며 애매한 공고는 판단 보류에 저장합니다.")

    mode_options = ["혼합", "로컬", "OpenRouter"]
    saved_mode = get_setting("analysis_mode", "혼합")
    analysis_mode = st.selectbox("처리 방식", mode_options, index=mode_options.index(saved_mode) if saved_mode in mode_options else 0)
    saved_cloud = get_setting("cloud_model", CLOUD_MODELS[0])
    cloud_model = st.selectbox("OpenRouter 분석 모델", CLOUD_MODELS, index=CLOUD_MODELS.index(saved_cloud) if saved_cloud in CLOUD_MODELS else 0)
    saved_local = get_setting("local_model", LOCAL_MODELS[0])
    local_model = st.selectbox("로컬 분석 모델", LOCAL_MODELS, index=LOCAL_MODELS.index(saved_local) if saved_local in LOCAL_MODELS else 0)
    run_budget = st.number_input("실행당 클라우드 예산 (USD)", min_value=0.0, max_value=100.0,
                                 value=float(get_setting("run_budget", "1")), step=0.5)
    if analysis_mode != "로컬" and not get_secret("OPENROUTER_API_KEY"):
        st.caption("OpenRouter 키가 없어 로컬 모델로 분석합니다. 관리자 설정에서 연결할 수 있습니다.")
    st.caption("애매한 공고는 상세 확인 후 판단합니다. 본문이 부족하면 원본 확인 상태로 표시합니다.")
    force_refresh = st.checkbox("기존 공고의 첨부도 새로 확인", value=False)
    r2_enabled = st.checkbox("R2 동기화 사용", value=get_setting("r2_enabled", "0") == "1")
    save_setting("r2_enabled", "1" if r2_enabled else "0")
    os.environ["BIDINFO_R2_ENABLED"] = "1" if r2_enabled else "0"
    if r2_enabled and st.button("로컬 DB·첨부를 R2에 동기화"):
        from pathlib import Path
        from r2_storage import sync_local_to_r2
        with st.spinner("로컬 자료를 R2에 동기화 중입니다..."):
            synced = sync_local_to_r2(DATA_DIR)
        st.info(f"동기화 결과: 성공 {synced['success']}개 / 실패 {synced['failed']}개")
    if st.button("기존 공고 문서 재추출·분류"):
        with st.spinner("기존 요약을 보존하고 문서·분류를 보강합니다..."):
            st.session_state["last_run_metrics"] = backfill_existing()
        st.rerun()
    if st.button("선택 분야 기존 공고 재분석"):
        budget = RunBudget(run_budget)
        with st.spinner("선택 분야의 기존 첨부를 분석합니다..."):
            _, stats = reanalyze_existing(engine_configs(analysis_mode, cloud_model, local_model, budget), selected_categories)
        st.session_state["last_run_metrics"] = dict(stats, **budget.snapshot())
        st.rerun()
    with st.expander("모델 비교 결과"):
        from pathlib import Path
        report = Path(DATA_DIR) / "model_benchmark.json"
        if report.exists():
            benchmark_result = json.loads(report.read_text())
            st.dataframe(benchmark_result.get("results", []), width="stretch")
            from ollama_summary import PROMPT_VERSION
            if benchmark_result.get("prompt_version") != PROMPT_VERSION:
                st.caption("분류 체계 변경 이전의 비교 결과입니다. 선택 모델로 다시 비교할 수 있습니다.")
        else:
            st.caption("아직 모델 실측 결과가 없습니다.")
        st.caption("동일한 한글 RFP 12건으로 속도·요건 정확도·JSON 성공률·비용을 비교합니다.")
        if st.button("선택 모델로 비교 실행"):
            from benchmark import run_benchmark
            models = [local_model] + ([cloud_model] if analysis_mode != "로컬" else [])
            with st.spinner("한글 합성 RFP 12건으로 모델을 비교합니다..."):
                run_benchmark(models, run_budget, output=report)
            st.rerun()



    sites = load_all_sites()
    if "KB국민은행" not in sites:
        save_site("KB국민은행", "https://omoney.kbstar.com/quics?page=C018592")
        sites = load_all_sites()

    selected_site_name = st.selectbox("🌐 수집 대상 사이트 선택", list(sites.keys()))
    default_url = sites.get(selected_site_name, "https://www.nonghyup.com/ecenter/bid/bidList.do")
    target_url = st.text_input("수집 대상 URL", value=default_url)

    with st.expander("➕ 수집 사이트 신규 저장 / 삭제"):
        new_site_name = st.text_input("사이트 이름", placeholder="예: KB국민은행")
        new_site_url = st.text_input("사이트 URL", placeholder="https://omoney.kbstar.com/quics?page=C018592")

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            if st.button("💾 사이트 저장", width="stretch"):
                if new_site_name.strip() and new_site_url.strip():
                    save_site(new_site_name, new_site_url)
                    upload_db_to_r2(DB_PATH)
                    st.success(f"[{new_site_name}] 사이트 저장 완료!")
                    st.rerun()
        with col_s2:
            if st.button("🗑️ 사이트 삭제", width="stretch"):
                if selected_site_name:
                    delete_site(selected_site_name)
                    upload_db_to_r2(DB_PATH)
                    st.success(f"[{selected_site_name}] 삭제 완료!")
                    st.rerun()

    today = date.today()
    default_start = today - timedelta(days=30)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_d = st.date_input("시작일", default_start)
    with col_d2:
        end_d = st.date_input("종료일", today)

    search_kw = st.text_input("검색어 필터 (선택)", value="")
    st.markdown("<br>", unsafe_allow_html=True)

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        run_crawl = st.button("🚀 수집 시작", type="primary", width="stretch")
    with col_b2:
        raw_bids_for_csv = load_all_bids()
        if raw_bids_for_csv:
            csv_df = pd.DataFrame(raw_bids_for_csv)
            csv_export = csv_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 CSV 다운",
                data=csv_export,
                file_name=f"bid_export_{today}.csv",
                mime="text/csv",
                width="stretch"
            )
        else:
            st.button("📥 CSV 다운", disabled=True, width="stretch")

# 💡 세션 상태에 따라 시크릿 어드민 모달 호출
if st.session_state["show_admin_modal"]:
    show_admin_console_modal()

# --- 크롤링 및 AI 분석 실행 ---
if run_crawl:
    for name, value in {"pri_kw": pri_kw.strip(), "sec_kw": sec_kw.strip(),
                        "selected_categories": json.dumps(selected_categories, ensure_ascii=False),
                        "selected_solutions": json.dumps(selected_solutions, ensure_ascii=False),
                        "selected_work": json.dumps(selected_work, ensure_ascii=False),
                        "analysis_mode": analysis_mode, "cloud_model": cloud_model,
                        "local_model": local_model, "run_budget": str(run_budget)}.items():
        save_setting(name, value)
    if not selected_categories:
        st.warning("수집 분야를 하나 이상 선택해 주세요.")
    else:
        budget = RunBudget(run_budget)
        stats = {}
        try:
            with st.spinner(f"[{selected_site_name}] 목록 확인·분류·첨부 수집 중입니다..."):
                bids = crawl_and_download_bids_by_date(target_url, DATA_DIR, start_d, end_d,
                    search_kw, selected_site_name, selected_categories, analysis_mode != "OpenRouter", force_refresh, stats,
                    selected_solutions=selected_solutions, selected_work=selected_work, include_uncertain=include_uncertain)
            if bids:
                progress_bar = st.progress(0.0)
                status_text = st.empty()
                def on_progress(done, total, item):
                    progress_bar.progress(done / total)
                    status_text.text(f"[{done}/{total}] {item['제목'][:30]}: {item.get('analysis_status', '미확인')}")
                _, analysis_stats = process_bids(bids, selected_categories,
                    engine_configs(analysis_mode, cloud_model, local_model, budget), pri_kw, sec_kw, on_progress)
                stats.update(analysis_stats)
            stats.update(budget.snapshot())
            st.session_state["last_run_metrics"] = stats
            st.rerun()
        except (ValueError, RuntimeError) as error:
            st.error(str(error))

if "last_run_metrics" in st.session_state:
    st.info("최근 실행 결과")
    st.json(st.session_state["last_run_metrics"])

# --- 메인 데이터 뷰 및 탭 ---
bids_data = load_all_bids()
df = pd.DataFrame(bids_data) if bids_data else pd.DataFrame()

if not df.empty:
    df['fit_score'] = pd.to_numeric(df['fit_score'], errors='coerce').fillna(0).astype(int)
    df['summary'] = df['summary'].fillna('미분석: 분류 확정 후 첨부를 수집해 주세요')
    df['fit_reason'] = df['fit_reason'].fillna('아직 분석하지 않음')
    df["분야"] = df["tags"].apply(lambda value: ", ".join(json.loads(value or "[]")))
    df["세부 항목"] = df["solution_tags"].apply(lambda value: ", ".join(json.loads(value or "[]")))
    df["업무"] = df["work_tags"].apply(lambda value: ", ".join(json.loads(value or "[]")))
    df["D-Day"] = df["deadline"].apply(calculate_d_day)
    df["starred_symbol"] = df["starred"].apply(lambda x: "⭐" if x == 1 else "☆")

col_f1, col_f2, col_f3, col_f4 = st.columns([1.2, 1.4, 2.0, 1.1])

with col_f1:
    unique_sites = ["전체 사이트"] + (sorted(list(df["site_name"].unique())) if not df.empty else [])
    selected_site_filter = st.selectbox("🌐 수집 사이트 선택", options=unique_sites)

with col_f2:
    min_score = st.slider("최저 적합도 점수", 0, 100, 0, step=10)
with col_f3:
    filter_title = st.text_input("공고 제목 검색", "")
with col_f4:
    st.markdown('<div style="padding-top: 35px;"></div>', unsafe_allow_html=True)
    only_starred = st.checkbox("⭐ 찜한 공고만", value=False)

category_filter = st.multiselect("표시 분야 (하나라도 일치)", CATEGORIES)
solution_filter = st.multiselect("표시 세부 항목", taxonomy_options("세부 항목", category_filter))
work_filter = st.multiselect("표시 업무", taxonomy_options("업무"))
classification_filter = st.selectbox("표시 분류 상태", ["전체", "자동", "판단 보류", "수집 대기", "수동 분류", "수집 제외"])

if not df.empty:
    counts = {tag: sum(tag in json.loads(value or "[]") for value in df["tags"]) for tag in CATEGORIES}
    st.caption(" · ".join(f"{tag} {count}건" for tag, count in counts.items() if count))
filtered_df = df.copy() if not df.empty else pd.DataFrame()

if not filtered_df.empty:
    if category_filter:
        filtered_df = filtered_df[filtered_df["tags"].apply(lambda value: bool(set(json.loads(value or "[]")) & set(category_filter)))]
    if solution_filter:
        filtered_df = filtered_df[filtered_df["solution_tags"].apply(lambda v: bool(set(json.loads(v or "[]")) & set(solution_filter)))]
    if work_filter:
        filtered_df = filtered_df[filtered_df["work_tags"].apply(lambda v: bool(set(json.loads(v or "[]")) & set(work_filter)))]
    if classification_filter != "전체":
        filtered_df = filtered_df[filtered_df["classification_status"] == classification_filter]
    if selected_site_filter != "전체 사이트":
        filtered_df = filtered_df[filtered_df["site_name"] == selected_site_filter]
    if min_score > 0:
        filtered_df = filtered_df[filtered_df["fit_score"] >= min_score]
    if filter_title:
        filtered_df = filtered_df[filtered_df["title"].str.contains(filter_title, case=False, na=False)]
    if only_starred:
        filtered_df = filtered_df[filtered_df["starred"] == 1]

    filtered_df["num_int"] = pd.to_numeric(filtered_df["num"], errors='coerce').fillna(0)
    filtered_df = filtered_df.sort_values(by=["reg_date", "num_int"], ascending=[False, False])

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 입찰 공고 & 제안 파이프라인",
    "📋 핵심 체크리스트 & RFP 1장 요약",
    "💬 AI 서류 묻고 답하기 (Q&A)",
    "분류 보류·추가 수집"
])

# TAB 1: 대시보드 표
with tab1:
    st.subheader("📋 전체 수집 공고 목록")
    if filtered_df.empty:
        st.info("💡 사이드바에서 [🚀 수집 시작] 버튼을 누르거나 필터 조건을 변경해 주세요.")
    else:
        disp_df = filtered_df[["num", "starred_symbol", "status", "D-Day", "site_name", "org", "title", "origin_url", "reg_date", "fit_score", "fit_reason", "summary", "분야", "세부 항목", "업무", "classification_status", "extraction_status", "analysis_status", "analysis_model"]].copy()
        disp_df.columns = ["번호", "찜", "진행상태", "D-Day", "수집사이트", "계열사", "제목", "원본글", "등록일", "적합도", "적합사유", "사업요약", "분야", "세부 항목", "업무", "분류 상태", "문서 상태", "분석 상태", "분석 모델"]
        disp_df.index = range(1, len(disp_df) + 1)

        st.dataframe(
            disp_df,
            width="stretch",
            height=425,
            column_order=["제목", "분야", "세부 항목", "업무", "분류 상태", "문서 상태", "분석 상태", "D-Day", "수집사이트", "찜", "진행상태", "적합도", "사업요약", "원본글", "계열사", "번호", "등록일", "적합사유", "분석 모델"],
            column_config={
                "분야": st.column_config.TextColumn("분야", width=160),
                "문서 상태": st.column_config.TextColumn("문서 상태", width=100),
                "분석 상태": st.column_config.TextColumn("분석 상태", width=100),
                "번호": st.column_config.TextColumn("번호", alignment="center", width=65),
                "찜": st.column_config.TextColumn("찜", alignment="center", width=45),
                "진행상태": st.column_config.TextColumn("진행상태", alignment="center", width=65),
                "D-Day": st.column_config.TextColumn("D-Day", alignment="center", width=85),
                "수집사이트": st.column_config.TextColumn("수집사이트", alignment="center", width=140),
                "계열사": st.column_config.TextColumn("계열사", alignment="center", width=75),
                "제목": st.column_config.TextColumn("제목", alignment="left", width=420),
                "원본글": st.column_config.LinkColumn("원본글", display_text="🔗 바로가기", width=95),
                "등록일": st.column_config.TextColumn("등록일", alignment="center", width=95),
                "적합도": st.column_config.ProgressColumn("적합도", format="%d점", min_value=0, max_value=100, width=85),
                "적합사유": st.column_config.TextColumn("적합사유", alignment="left", width=200),
                "사업요약": st.column_config.TextColumn("사업요약", alignment="left", width=350)
            }
        )

        st.markdown("---")
        st.subheader("⚙️ 제안 진행 상태(Pipeline) & 상세 리포트 팝업")

        if not filtered_df.empty:
            col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns([0.35, 4.5, 1.4, 1.35, 1.4], vertical_alignment="bottom")
            with col_p2:
                target_bid_labels = [f"[{row['starred_symbol']}] [{row['num']}] [{row['org']}] {row['title']} ({row['reg_date']}) · {row['bid_id']}" for _, row in filtered_df.iterrows()]
                target_bid_label = st.selectbox(
                    "상태 변경 및 상세 리포트 팝업 공고 선택",
                    options=target_bid_labels,
                    key="pipeline_select"
                )
                selected_index = list(target_bid_labels).index(target_bid_label)
                selected_row = filtered_df.iloc[selected_index]
            with col_p1:
                is_star = st.checkbox("⭐", value=bool(selected_row["starred"]), key="star_" + selected_row["bid_id"])
            with col_p3:
                status_options = ["검토중", "제안작성중", "제출완료", "낙찰", "포기"]
                curr_idx = status_options.index(selected_row["status"]) if selected_row["status"] in status_options else 0
                new_status = st.selectbox("진행상태", status_options, index=curr_idx)
            with col_p4:
                if st.button("💾 상태 저장", type="primary", width="stretch"):
                    update_bid_status(selected_row["bid_id"], new_status, 1 if is_star else 0)
                    upload_db_to_r2(DB_PATH)
                    st.success(f"[{selected_row['title'][:15]}...] 상태 업데이트 완료!")
                    st.rerun()
            with col_p5:
                if st.button("🔍 팝업 상세보기", width="stretch"):
                    st.session_state["modal_target_bid"] = selected_row.to_dict()
                    st.rerun()

        if st.session_state["modal_target_bid"] is not None:
            show_bid_detail_modal(st.session_state["modal_target_bid"])

# TAB 2: 핵심 체크리스트 & RFP 1장 요약
with tab2:
    st.subheader("📋 입찰 서류 체크리스트 & 📄 RFP 1장 요약 리포트")
    if filtered_df.empty:
        st.warning("필터 조건에 일치하는 공고가 없습니다.")
    else:
        engine_list = get_configured_engine_list()
        for _, row in filtered_df.iterrows():
            star_mark = "⭐ " if row['starred'] == 1 else ""
            expander_title = f"{star_mark}[{row['status']}] [{row['D-Day']}] [{row['org']}] {row['title']} (적합도: {row['fit_score']}점)"

            with st.expander(expander_title, expanded=True if row['fit_score']>=70 else False):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"⏰ **제출 마감일시**: `{row.get('deadline', '상세 서류 참조')}` ({row['D-Day']})")
                    st.markdown(f"📨 **제출 방식**: `{row.get('submit_type', '전자/방문')}`")
                with col2:
                    st.markdown(f"📜 **필수 제출 서류**: `{row.get('required_docs', '제안서 등')}`")
                    st.markdown(f"✅ **핵심 자격 요건**: `{row.get('qualifications', '상세 서류 참조')}`")

                orig_link = row.get("origin_url", "")
                if orig_link:
                    st.markdown(f"🔗 **원본 공고 게시글 링크**: [{row['title']}]({orig_link})")

                st.markdown(f"💡 **AI 사업 요약**: {row['summary']}")
                st.markdown(f"🎯 **적합 사유**: {row['fit_reason']}")

                st.markdown("---")
                rfp_btn_key = f"rfp_btn_{row['bid_id']}"
                if st.button(f"📄 [{row['title'][:20]}...] RFP 1장 요약 리포트 생성", key=rfp_btn_key):
                    with st.spinner("첨부 서류를 분석하여 RFP 1장 요약 리포트를 생성 중입니다..."):
                        doc_text = process_folder_documents(row["folder_path"])
                        rfp_report = generate_rfp_one_pager(row["title"], doc_text, engine_configs=engine_list)
                        st.markdown("#### 📄 RFP 1장 요약 리포트:")
                        st.info(rfp_report)

# TAB 3: AI 서류 Q&A
with tab3:
    st.subheader("🤖 입찰 첨부 서류 기반 AI 대화")
    if filtered_df.empty:
        st.warning("필터 조건에 일치하는 공고가 없습니다.")
    else:
        engine_list = get_configured_engine_list()
        bid_options = {f"[{b['org']}] {b['title']} ({b['reg_date']}) - {b['fit_score']}점": b for _, b in filtered_df.iterrows()}
        selected_label = st.selectbox("질문할 입찰 공고 선택", list(bid_options.keys()))
        selected_bid = bid_options[selected_label]

        st.info(f"📌 **선택된 공고**: {selected_bid['title']}\n\n💡 **AI 요약**: {selected_bid['summary']}")

        folder_path = selected_bid["folder_path"]
        doc_text = ""
        if st.button("첨부 문서 읽기", key="load_qa_document"):
            st.session_state["qa_document"] = (selected_bid["bid_id"], process_folder_documents(folder_path))
        saved_document = st.session_state.get("qa_document")
        if saved_document and saved_document[0] == selected_bid["bid_id"]:
            doc_text = saved_document[1]

        if doc_text and doc_text.strip():
            st.success(f"📄 서류 텍스트 읽기 완료! (총 {len(doc_text)}자 추출됨)")
        else:
            st.caption("문서를 읽은 뒤 질문할 수 있습니다. 추출에 실패하면 원본을 확인해 주세요.")

        st.markdown("---")
        col_q1, col_q2, col_q3 = st.columns(3)
        with col_q1:
            if st.button("📌 필수 제출 서류 목록이 뭐야?"):
                st.session_state["qa_preset_text"] = "이 사업의 필수 제출 서류 목록과 입찰 참가 자격 요건을 알려줘."
                st.rerun()
        with col_q2:
            if st.button("⏰ 입찰 마감일시와 장소는?"):
                st.session_state["qa_preset_text"] = "입찰 서류 제출 마감 일시와 개찰 장소를 알려줘."
                st.rerun()
        with col_q3:
            if st.button("💰 사업 예산 및 수행 기간은?"):
                st.session_state["qa_preset_text"] = "이 사업의 예상 예산(사업비) 및 수행 기간은 얼마인가요?"
                st.rerun()

        user_question = st.text_input("질문을 입력하세요:", value=st.session_state["qa_preset_text"])

        if st.button("🚀 AI 질문 전송", type="primary"):
            if not user_question.strip():
                st.warning("질문 내용을 입력해주세요.")
            else:
                st.session_state["qa_preset_text"] = user_question.strip()
                if not doc_text:
                    doc_text = process_folder_documents(folder_path)
                with st.spinner("AI 엔진이 답변을 작성 중입니다..."):
                    answer = answer_bid_question(
                        title=selected_bid['title'],
                        doc_text=doc_text,
                        question=user_question,
                        engine_configs=engine_list
                    )
                    st.markdown("### 🤖 AI 답변:")
                    st.write(answer)

with tab4:
    show_review_queue(DATA_DIR, get_configured_engine_list())
