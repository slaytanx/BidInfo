import json
import math
import os
from datetime import date, timedelta
import importlib

import pandas as pd
import streamlit as st

import admin_console
importlib.reload(admin_console)
from admin_console import show_admin_console_modal
from classification import CATEGORIES, DEFAULT_CATEGORIES
from crawler import crawl_and_download_bids_by_date
from date_utils import calculate_d_day
from db import (
    DB_PATH,
    get_setting,
    init_db,
    load_all_bids,
    load_all_sites,
    save_setting,
    save_site,
    update_bid_status,
)
from hwp_parser import process_folder_documents
from llm_manager import (
    CLOUD_MODELS,
    LOCAL_MODELS,
    RunBudget,
    engine_configs,
    get_secret,
)
from ollama_summary import answer_bid_question, generate_rfp_one_pager
from pipeline import process_bids
from r2_storage import upload_db_to_r2
from taxonomy import taxonomy_options
from taxonomy_ui import show_review_queue

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
    return admin_pass_secret or ""

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

    /* 💥 ⚙️ 어드민 톱니바퀴 버튼 회색 테두리 및 배경 완벽 투명화 & 수평 정렬 💥 */
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
        transform: none !important;
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

    /* 📱 사이드바 최적화 레이아웃 (상하단 여백 축소 & 내부 밸런스 확장 & 수집시작 최하단 안착) */
    [data-testid="stSidebarHeader"] {
        height: 32px !important;
        min-height: 32px !important;
        padding-top: 2px !important;
    }
    [data-testid="stSidebarUserContent"] {
        padding-top: 0px !important;
        padding-bottom: 0.3rem !important;
        padding-left: 0.95rem !important;
        padding-right: 0.95rem !important;
    }
    [data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] {
        gap: 8px !important;
    }
    [data-testid="stSidebar"] div:has(> h3) {
        margin-bottom: 0px !important;
    }
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] [data-testid="stSubheader"] {
        font-size: 0.96rem !important;
        font-weight: 700 !important;
        margin-top: 8px !important;
        margin-bottom: 3px !important;
        padding-bottom: 4px !important;
        line-height: 1.3 !important;
    }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] label p {
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        margin-bottom: 0.15rem !important;
        line-height: 1.25 !important;
    }
    [data-testid="stSidebar"] div[data-baseweb="input"] > div,
    [data-testid="stSidebar"] div[data-baseweb="select"] > div {
        min-height: 35px !important;
    }
    [data-testid="stSidebar"] span[data-baseweb="tag"] {
        height: 22px !important;
        font-size: 0.74rem !important;
        margin: 1px 2px !important;
        padding: 0 4px !important;
    }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        margin-bottom: 4px !important;
    }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
        font-size: 0.76rem !important;
        margin-top: 0.1rem !important;
        margin-bottom: 0.15rem !important;
        line-height: 1.2 !important;
    }
    [data-testid="stSidebar"] [data-testid="stCheckbox"] {
        margin-bottom: 1px !important;
    }
    [data-testid="stSidebar"] [data-testid="stCheckbox"] label p {
        font-size: 0.82rem !important;
        line-height: 1.25 !important;
    }
    [data-testid="stSidebar"] hr {
        margin: 5px 0 !important;
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

def safe_parse_run_budget(raw_val, default: float = 1.0) -> float:
    try:
        val = float(raw_val)
        if not math.isfinite(val) or val < 0.0:
            return default
        return min(100.0, max(0.0, val))
    except (ValueError, TypeError):
        return default


# --- 1차/2차/3차 엔진 구성 함수 ---
def get_configured_engine_list():
    budget_val = safe_parse_run_budget(globals().get("run_budget", get_setting("run_budget", "1.0")))
    try:
        budget_obj = RunBudget(budget_val)
    except (ValueError, TypeError, ArithmeticError):
        budget_obj = RunBudget(1.0)
    return engine_configs(globals().get("analysis_mode", get_setting("analysis_mode", "혼합")),
                          globals().get("cloud_model", get_setting("cloud_model", CLOUD_MODELS[0])),
                          globals().get("local_model", get_setting("local_model", LOCAL_MODELS[0])),
                          budget_obj)

# --- 사이드바 설정 영역 ---
with st.sidebar:
    # 💡 ⚙️ 아이콘과 텍스트 수평 센터(baseline) 100% 정밀 맞춤
    col_ic, col_txt = st.columns([0.65, 5.35], vertical_alignment="center")
    with col_ic:
        if st.button("⚙️", help="🔒 관리자 어드민 콘솔 열기"):
            st.session_state["show_admin_modal"] = True
            st.rerun()
    with col_txt:
        st.markdown("<h3 style='margin:0; font-size:1.12rem; font-weight:700;'>수집 & 필터 설정</h3>", unsafe_allow_html=True)

    st.markdown("<div style='margin-bottom: 6px;'></div>", unsafe_allow_html=True)

    # --- 1단계: 수집 대상 선택 ---
    st.subheader("1단계: 수집 대상 선택")
    sites = load_all_sites()
    if "KB국민은행" not in sites:
        save_site("KB국민은행", "https://omoney.kbstar.com/quics?page=C018592")
        sites = load_all_sites()

    selected_site_name = st.selectbox("🌐 수집 대상 사이트 선택", list(sites.keys()))
    target_url = sites.get(selected_site_name, "https://www.nonghyup.com/ecenter/bid/bidList.do")

    today = date.today()
    default_start = today - timedelta(days=30)
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_d = st.date_input("시작일", default_start)
    with col_d2:
        end_d = st.date_input("종료일", today)

    search_kw = st.text_input("검색어 필터 (선택)", value="", placeholder="키워드 입력 시 제목 필터링")

    st.markdown("---")

    # --- 2단계: 수집 분야 & 모델 ---
    st.subheader("2단계: 수집 분야 & 모델")
    selected_categories = st.multiselect("수집 분야 (복수 선택)", CATEGORIES,
        default=json.loads(get_setting("selected_categories", json.dumps(DEFAULT_CATEGORIES))))
    solution_options = taxonomy_options("세부 항목", selected_categories)
    selected_solutions = st.multiselect("수집 세부 항목 (비우면 분야 전체)", solution_options,
        default=[x for x in json.loads(get_setting("selected_solutions", "[]")) if x in solution_options])
    work_options = taxonomy_options("업무")
    selected_work = st.multiselect("수집 업무 (비우면 업무 전체)", work_options,
        default=[x for x in json.loads(get_setting("selected_work", "[]")) if x in work_options])

    # 💡 처리 방식을 한 줄로 가로 배치 (라벨 + 셀렉트박스 정밀 수직 센터링)
    mode_options = ["혼합", "로컬", "OpenRouter"]
    saved_mode = get_setting("analysis_mode", "혼합")
    col_m_lbl, col_m_val = st.columns([1.1, 2.5], vertical_alignment="center")
    with col_m_lbl:
        st.markdown("<div style='display: flex; align-items: center; height: 36px; font-weight: 600; font-size: 0.85rem; transform: translateY(-8px);'>처리 방식</div>", unsafe_allow_html=True)
    with col_m_val:
        analysis_mode = st.selectbox(
            "처리 방식",
            mode_options,
            index=mode_options.index(saved_mode) if saved_mode in mode_options else 0,
            label_visibility="collapsed",
        )

    curr_local = get_setting("local_model", LOCAL_MODELS[0])
    curr_cloud = get_setting("cloud_model", CLOUD_MODELS[0])
    curr_cloud_short = curr_cloud.split("/")[-1].replace("-0731", "")
    if analysis_mode == "로컬":
        st.caption(f"🤖 `{curr_local}` (어드민)")
    elif analysis_mode == "OpenRouter":
        st.caption(f"🤖 `{curr_cloud_short}` (어드민)")
    else:
        st.caption(f"🤖 `{curr_local}` + `{curr_cloud_short}`")

    if analysis_mode != "로컬" and not get_secret("OPENROUTER_API_KEY"):
        st.caption("OpenRouter 키가 없어 로컬 모델로 분석합니다.")

    include_uncertain = st.checkbox(
        "애매한 공고도 첨부를 확인",
        value=False,
        help="같은 축에서는 하나라도 일치, 분야·세부 항목·업무 사이에서는 모두 충족해야 수집합니다. 기본값은 관련 확인된 공고만 수집하며 애매한 공고는 판단 보류에 저장합니다.",
    )
    force_refresh = st.checkbox("기존 공고의 첨부도 새로 확인", value=False)

    st.markdown("---")

    # --- 3단계: 실행 ---
    st.subheader("3단계: 실행")
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
    show_admin_console_modal(get_admin_password())

# --- 크롤링 및 AI 분석 실행 ---
if run_crawl:
    for name, value in {"selected_categories": json.dumps(selected_categories, ensure_ascii=False),
                        "selected_solutions": json.dumps(selected_solutions, ensure_ascii=False),
                        "selected_work": json.dumps(selected_work, ensure_ascii=False),
                        "analysis_mode": analysis_mode}.items():
        save_setting(name, value)
    if not selected_categories:
        st.warning("수집 분야를 하나 이상 선택해 주세요.")
    else:
        pri_kw = get_setting("pri_kw", "통신").strip()
        sec_kw = get_setting("sec_kw", "네트워크, 보안").strip()
        run_budget = safe_parse_run_budget(get_setting("run_budget", "1.0"))
        cloud_model = get_setting("cloud_model", CLOUD_MODELS[0])
        local_model = get_setting("local_model", LOCAL_MODELS[0])
        try:
            budget = RunBudget(run_budget)
        except (ValueError, TypeError, ArithmeticError):
            budget = RunBudget(1.0)
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

if "last_run_metrics" in st.session_state and isinstance(st.session_state["last_run_metrics"], dict):
    m = st.session_state["last_run_metrics"]
    coll = m.get("collected", 0)
    try:
        spent = float(m.get("spent_or_reserved_usd", 0.0) or 0.0)
    except (ValueError, TypeError):
        spent = 0.0
    succ = m.get("success", 0)
    with st.expander(f"📊 최근 수집·분석 결과 (수집 {coll}건 · 분석 성공 {succ}건 · 비용 ${spent:.4f})", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("확인 공고", f"{m.get('listed', 0)}건")
        c2.metric("수집 대상", f"{coll}건", f"첨부 {m.get('downloaded_files', 0)}개")
        c3.metric("분석 성공", f"{succ}건", f"보류 {m.get('pending_review', 0)}건")
        c4.metric("사용 예산", f"${spent:.4f}", f"호출 {m.get('cloud_calls', 0)}회")
        st.caption("상세 통계 로그 (JSON):")
        st.json(m)

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
                rfp_state_key = f"rfp_report_{row['bid_id']}"
                rfp_btn_key = f"rfp_btn_{row['bid_id']}"
                if st.button(f"📄 [{row['title'][:20]}...] RFP 1장 요약 리포트 생성", key=rfp_btn_key):
                    with st.spinner("첨부 서류를 분석하여 RFP 1장 요약 리포트를 생성 중입니다..."):
                        doc_text = process_folder_documents(row["folder_path"])
                        st.session_state[rfp_state_key] = generate_rfp_one_pager(row["title"], doc_text, engine_configs=engine_list)
                if rfp_state_key in st.session_state:
                    st.markdown("#### 📄 RFP 1장 요약 리포트:")
                    st.info(st.session_state[rfp_state_key])

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
