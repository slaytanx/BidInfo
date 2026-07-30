import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, date
import streamlit as st
import pandas as pd

from crawler import crawl_and_download_bids_by_date
from hwp_parser import process_folder_documents
from ollama_summary import analyze_bid_with_ollama, answer_bid_question, generate_rfp_one_pager
from db import (
    init_db, save_bid, load_all_bids, save_site, load_all_sites, 
    delete_site, save_setting, get_setting, update_bid_status, 
    delete_bids, reset_all_bids, DB_PATH
)
from r2_storage import download_db_from_r2, upload_db_to_r2, upload_file_to_r2, delete_r2_folder

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
        admin_pass_secret = sec.get("ADMIN_PASSWORD", None)
except BaseException:
    admin_pass_secret = None

ADMIN_PASSWORD = admin_pass_secret or get_setting("admin_password", "admin123!")

# 💡 R2 스토리지에서 최신 bids_history.db 영구 데이터 다운로드 동기화
download_db_from_r2(DB_PATH)
init_db()

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
    
    /* 💥 톱니바퀴 아이콘과 수집 & 필터 설정 텍스트 100% 수평 센터 완벽 정렬 💥 */
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button {
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
        display: flex !important;
        align-items: center !important;
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button p {
        font-size: 1.3rem !important;
        line-height: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child button:hover {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        opacity: 0.7;
    }
    [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child h3 {
        font-size: 1.3rem !important;
        line-height: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
        font-weight: 700 !important;
    }
    
    /* 어드민 로그인 폼 외곽선 깔끔 제거 */
    [data-testid="stForm"] {
        border: none !important;
        padding: 0 !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("🎯 입찰 공고 통합 수집 · 분석 · 제안 파이프라인 플랫폼")
st.caption("공고 수집부터 D-Day 계산, RFP 1장 요약 리포트, 1차/2차/3차 멀티 AI 폴백 엔진 및 어드민 보안 관리를 통합 제공합니다.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

def calculate_d_day(deadline_str: str) -> str:
    """D-Day 계산"""
    if not deadline_str:
        return "상세참조"
    
    match = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", deadline_str)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            deadline_date = date(year, month, day)
        except ValueError:
            return "상세참조"
    else:
        match_kor = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", deadline_str)
        if match_kor:
            year, month, day = int(match_kor.group(1)), int(match_kor.group(2)), int(match_kor.group(3))
            try:
                deadline_date = date(year, month, day)
            except ValueError:
                return "상세참조"
        else:
            match_md = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", deadline_str)
            if match_md:
                year = date.today().year
                month, day = int(match_md.group(1)), int(match_md.group(2))
                try:
                    deadline_date = date(year, month, day)
                except ValueError:
                    return "상세참조"
            else:
                match_short_yr = re.search(r"(\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", deadline_str)
                if match_short_yr:
                    year = 2000 + int(match_short_yr.group(1))
                    month, day = int(match_short_yr.group(2)), int(match_short_yr.group(3))
                    try:
                        deadline_date = date(year, month, day)
                    except ValueError:
                        return "상세참조"
                else:
                    return "상세참조"

    today = date.today()
    diff = (deadline_date - today).days

    if diff < 0:
        return "마감됨"
    elif diff == 0:
        return "🔥 D-DAY"
    else:
        return f"⏳ D-{diff}"

# 💡 공고 상세 리포트 팝업 모달창
@st.dialog("📄 입찰 공고 상세 리포트", width="large")
def show_bid_detail_modal(row):
    star_mark = "⭐ 관심공고 찜" if row["starred"] == 1 else "☆ 일반공고"
    st.markdown(f"### {row['title']}")
    st.caption(f"수집 사이트: {row.get('site_name', '미지정')} | 계열사: {row['org']} | 등록일: {row['reg_date']} | {star_mark}")
    
    origin_link_url = row.get("origin_url", "") or row.get("url", "")
    if origin_link_url:
        st.link_button("🌐 실제 원본 공고 게시글로 이동 (새 창)", origin_link_url, use_container_width=True)

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

    if st.button("닫기", type="primary", use_container_width=True):
        st.session_state["modal_target_bid"] = None
        st.rerun()

# 💡 시크릿 톱니바퀴 클릭 시 뜨는 🔒 시스템 어드민 관리자 팝업 모달창
@st.dialog("🔒 시스템 관리자 센터 (Admin Console)", width="large")
def show_admin_console_modal():
    st.caption("AI 1차/2차/3차 폴백 엔진 설정 및 불필요한 공고/첨부파일 완전 삭제를 관리합니다.")

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
                login_submit = st.form_submit_button("🔓 로그인", type="primary", use_container_width=True)

            if login_submit:
                if input_pass == ADMIN_PASSWORD:
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
            if st.button("🔒 로그아웃", use_container_width=True):
                st.session_state["admin_logged_in"] = False
                st.rerun()

        st.markdown("---")

        # 1. AI 1차/2차/3차 폴백 엔진 설정
        st.markdown("### 🤖 AI 멀티 엔진 (1차 · 2차 · 3차 폴백) 설정")
        st.caption("1차 엔진 실패 시 2차 엔진으로, 2차 엔진 실패 시 3차 엔진으로 자동 전환되어 100% 무중단 요약을 수행합니다.")

        provider_options = ["Google Gemini", "OpenRouter", "NVIDIA NIM", "Local Ollama", "사용 안함"]

        col_e1, col_e2, col_e3 = st.columns(3)
        with col_e1:
            st.markdown("#### 🥇 1차 엔진 (기본)")
            e1_provider = st.selectbox("1차 프로바이더", provider_options, index=provider_options.index(get_setting("e1_provider", "Google Gemini")))
            e1_key = st.text_input("1차 API Key", value=get_setting("e1_key", os.environ.get("GEMINI_API_KEY", "")), type="password")
            e1_model = st.text_input("1차 모델명", value=get_setting("e1_model", "gemini-1.5-flash"))

        with col_e2:
            st.markdown("#### 🥈 2차 엔진 (자동 폴백)")
            e2_provider = st.selectbox("2차 프로바이더", provider_options, index=provider_options.index(get_setting("e2_provider", "OpenRouter")))
            e2_key = st.text_input("2차 API Key", value=get_setting("e2_key", os.environ.get("OPENROUTER_API_KEY", "")), type="password")
            e2_model = st.text_input("2차 모델명", value=get_setting("e2_model", "google/gemini-2.0-flash-exp:free"))

        with col_e3:
            st.markdown("#### 🥉 3차 엔진 (최종 폴백)")
            e3_provider = st.selectbox("3차 프로바이더", provider_options, index=provider_options.index(get_setting("e3_provider", "Local Ollama")))
            e3_key = st.text_input("3차 API Key", value=get_setting("e3_key", ""), type="password")
            e3_model = st.text_input("3차 모델명", value=get_setting("e3_model", "gemma4:e4b-mlx"))

        col_cfg1, col_cfg2 = st.columns([3, 1])
        with col_cfg1:
            new_admin_pass = st.text_input("🔑 어드민 비밀번호 변경 (선택)", value=get_setting("admin_password", "admin123!"), type="password")
        with col_cfg2:
            st.write("")
            st.write("")
            if st.button("💾 어드민 설정 저장", type="primary", use_container_width=True):
                save_setting("e1_provider", e1_provider)
                save_setting("e1_key", e1_key)
                save_setting("e1_model", e1_model)

                save_setting("e2_provider", e2_provider)
                save_setting("e2_key", e2_key)
                save_setting("e2_model", e2_model)

                save_setting("e3_provider", e3_provider)
                save_setting("e3_key", e3_key)
                save_setting("e3_model", e3_model)

                if new_admin_pass.strip():
                    save_setting("admin_password", new_admin_pass.strip())

                upload_db_to_r2(DB_PATH)
                st.success("🎉 AI 멀티 엔진 및 비밀번호 설정이 DB/R2 스토리지에 저장되었습니다!")

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
                if st.button("🚨 선택한 공고 및 첨부파일/R2 동기화 완결 삭제", type="primary", use_container_width=True):
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

    if st.button("닫기", type="primary", use_container_width=True):
        st.session_state["show_admin_modal"] = False
        st.rerun()

# --- 1차/2차/3차 엔진 구성 함수 ---
def get_configured_engine_list():
    """DB에 저장된 1차, 2차, 3차 AI 엔진 설정 목록 반환"""
    e1_p = get_setting("e1_provider", "Google Gemini")
    e1_k = get_setting("e1_key", os.environ.get("GEMINI_API_KEY", ""))
    e1_m = get_setting("e1_model", "gemini-1.5-flash")

    e2_p = get_setting("e2_provider", "OpenRouter")
    e2_k = get_setting("e2_key", os.environ.get("OPENROUTER_API_KEY", ""))
    e2_m = get_setting("e2_model", "google/gemini-2.0-flash-exp:free")

    e3_p = get_setting("e3_provider", "Local Ollama")
    e3_k = get_setting("e3_key", "")
    e3_m = get_setting("e3_model", "gemma4:e4b-mlx")

    return [
        {"provider": e1_p, "api_key": e1_k, "model": e1_m},
        {"provider": e2_p, "api_key": e2_k, "model": e2_m},
        {"provider": e3_p, "api_key": e3_k, "model": e3_m}
    ]

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
            if st.button("💾 사이트 저장", use_container_width=True):
                if new_site_name.strip() and new_site_url.strip():
                    save_site(new_site_name, new_site_url)
                    upload_db_to_r2(DB_PATH)
                    st.success(f"[{new_site_name}] 사이트 저장 완료!")
                    st.rerun()
        with col_s2:
            if st.button("🗑️ 사이트 삭제", use_container_width=True):
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
        run_crawl = st.button("🚀 수집 시작", type="primary", use_container_width=True)
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
                use_container_width=True
            )
        else:
            st.button("📥 CSV 다운", disabled=True, use_container_width=True)

# 💡 세션 상태에 따라 시크릿 어드민 모달 호출
if st.session_state["show_admin_modal"]:
    show_admin_console_modal()

# --- 크롤링 및 AI 분석 실행 ---
if run_crawl:
    save_setting("pri_kw", pri_kw.strip())
    save_setting("sec_kw", sec_kw.strip())

    engine_list = get_configured_engine_list()

    with st.spinner(f"[{selected_site_name}] 수집 및 AI 멀티 엔진 분석 진행 중..."):
        bids = crawl_and_download_bids_by_date(
            url=target_url, 
            base_dir=DATA_DIR, 
            start_date=start_d, 
            end_date=end_d, 
            keyword=search_kw,
            site_name=selected_site_name
        )
        
        if not bids:
            st.warning("지정한 기간 내 수집된 공고가 없습니다.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for idx, item in enumerate(bids, 1):
                status_text.text(f"[{idx}/{len(bids)}] AI 멀티 엔진 분석 중: {item['제목'][:30]}...")
                extracted_text = process_folder_documents(item["폴더경로"])
                
                ai_res = analyze_bid_with_ollama(
                    title=item["제목"],
                    doc_text=extracted_text,
                    pri_keywords=pri_kw,
                    sec_keywords=sec_kw,
                    engine_configs=engine_list
                )
                
                item["사업요약"] = ai_res["summary"]
                item["적합도점수"] = ai_res["fit_score"]
                item["적합사유"] = ai_res["fit_reason"]
                item["제출마감일시"] = ai_res["deadline"]
                item["제출방식"] = ai_res["submit_type"]
                item["필수제출서류"] = ai_res["required_docs"]
                item["참가자격요건"] = ai_res["qualifications"]
                item["starred"] = 0
                item["status"] = "검토중"
                
                save_bid(item)
                progress_bar.progress(idx / len(bids))

            upload_db_to_r2(DB_PATH)
            st.success(f"🎉 총 {len(bids)}건의 공고 분석 및 R2 스토리지 동기화 완료!")
            st.rerun()

# --- 메인 데이터 뷰 및 탭 ---
bids_data = load_all_bids()
df = pd.DataFrame(bids_data) if bids_data else pd.DataFrame()

if not df.empty:
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

filtered_df = df.copy() if not df.empty else pd.DataFrame()

if not filtered_df.empty:
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

tab1, tab2, tab3 = st.tabs([
    "📊 입찰 공고 & 제안 파이프라인", 
    "📋 핵심 체크리스트 & RFP 1장 요약", 
    "💬 AI 서류 묻고 답하기 (Q&A)"
])

# TAB 1: 대시보드 표
with tab1:
    st.subheader("📋 전체 수집 공고 목록")
    if filtered_df.empty:
        st.info("💡 사이드바에서 [🚀 수집 시작] 버튼을 누르거나 필터 조건을 변경해 주세요.")
    else:
        disp_df = filtered_df[["num", "starred_symbol", "status", "D-Day", "site_name", "org", "title", "origin_url", "reg_date", "fit_score", "fit_reason", "summary"]].copy()
        disp_df.columns = ["번호", "찜", "진행상태", "D-Day", "수집사이트", "계열사", "제목", "원본글", "등록일", "적합도", "적합사유", "사업요약"]
        disp_df.index = range(1, len(disp_df) + 1)

        st.dataframe(
            disp_df, 
            use_container_width=True,
            height=425,
            column_config={
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
            col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns([0.35, 4.5, 1.4, 1.35, 1.4])
            with col_p1:
                st.write("")
                st.write("")
                is_star = st.checkbox("⭐", value=bool(filtered_df.iloc[0]["starred"]))
            with col_p2:
                target_bid_label = st.selectbox(
                    "상태 변경 및 상세 리포트 팝업 공고 선택", 
                    options=[f"[{row['starred_symbol']}] [{row['num']}] [{row['org']}] {row['title']} ({row['reg_date']})" for _, row in filtered_df.iterrows()],
                    key="pipeline_select"
                )
                selected_num = target_bid_label.split("]")[1].replace("[", "").strip()
                selected_row = filtered_df[filtered_df["num"] == selected_num].iloc[0]
            with col_p3:
                status_options = ["검토중", "제안작성중", "제출완료", "낙찰", "포기"]
                curr_idx = status_options.index(selected_row["status"]) if selected_row["status"] in status_options else 0
                new_status = st.selectbox("진행상태", status_options, index=curr_idx)
            with col_p4:
                st.write("")
                st.write("")
                if st.button("💾 상태 저장", type="primary", use_container_width=True):
                    update_bid_status(selected_row["bid_id"], new_status, 1 if is_star else 0)
                    upload_db_to_r2(DB_PATH)
                    st.success(f"[{selected_row['title'][:15]}...] 상태 업데이트 완료!")
                    st.rerun()
            with col_p5:
                st.write("")
                st.write("")
                if st.button("🔍 팝업 상세보기", use_container_width=True):
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
        with st.spinner("해당 공고의 첨부 서류 텍스트를 로드 중입니다..."):
            doc_text = process_folder_documents(folder_path)

        if doc_text and doc_text.strip():
            st.success(f"📄 서류 텍스트 읽기 완료! (총 {len(doc_text)}자 추출됨)")
        else:
            st.caption("ℹ️ **안내**: 첨부서류 파일이 없는 공고입니다.")

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
                with st.spinner("AI 엔진이 답변을 작성 중입니다..."):
                    answer = answer_bid_question(
                        title=selected_bid['title'], 
                        doc_text=doc_text if doc_text else f"공고 제목: {selected_bid['title']}\n사업 요약: {selected_bid['summary']}", 
                        question=user_question, 
                        engine_configs=engine_list
                    )
                    st.markdown("### 🤖 AI 답변:")
                    st.write(answer)
