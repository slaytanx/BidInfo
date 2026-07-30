import os
import re
import sqlite3
from datetime import datetime, timedelta, date
import streamlit as st
import pandas as pd

from crawler import crawl_and_download_bids_by_date
from hwp_parser import process_folder_documents
from ollama_summary import analyze_bid_with_ollama, answer_bid_question, generate_rfp_one_pager
from db import (
    init_db, save_bid, load_all_bids, save_site, load_all_sites, 
    delete_site, save_setting, get_setting, update_bid_status, DB_PATH
)
from r2_storage import download_db_from_r2, upload_db_to_r2, upload_file_to_r2

st.set_page_config(page_title="입찰 통합 분석 & 제안 파이프라인", layout="wide")

# 💡 Streamlit Secrets에서 R2 스토리지 환경변수 로드
if "R2_ACCESS_KEY_ID" in st.secrets:
    os.environ["R2_ACCESS_KEY_ID"] = st.secrets["R2_ACCESS_KEY_ID"]
if "R2_SECRET_ACCESS_KEY" in st.secrets:
    os.environ["R2_SECRET_ACCESS_KEY"] = st.secrets["R2_SECRET_ACCESS_KEY"]
if "R2_ENDPOINT_URL" in st.secrets:
    os.environ["R2_ENDPOINT_URL"] = st.secrets["R2_ENDPOINT_URL"]
if "R2_BUCKET_NAME" in st.secrets:
    os.environ["R2_BUCKET_NAME"] = st.secrets["R2_BUCKET_NAME"]

# 💡 R2 스토리지에서 최신 bids_history.db 영구 데이터 다운로드 동기화
download_db_from_r2(DB_PATH)
init_db()

# --- 세션 상태 초기화 (팝업 열기/닫기 및 Q&A 질문 유지 제어용) ---
if "modal_target_bid" not in st.session_state:
    st.session_state["modal_target_bid"] = None
if "qa_preset_text" not in st.session_state:
    st.session_state["qa_preset_text"] = ""

# --- 배경색 제거 및 상단 여백 2.5rem 조절, 1행 항목명(헤더) 및 셀 강제 완벽 중앙 정렬 CSS ---
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
</style>
""", unsafe_allow_html=True)

st.title("🎯 입찰 공고 통합 수집 · 분석 · 제안 파이프라인 플랫폼")
st.caption("공고 수집부터 D-Day 계산, RFP 1장 요약 리포트, 관심 공고 찜하기 및 제안 진행 상태 관리까지 통합 제공합니다.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

def calculate_d_day(deadline_str: str) -> str:
    """다양한 날짜 텍스트 형식에서 날짜를 정밀 추출하여 D-Day 계산"""
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

# 💡 공고 상세 리포트 팝업 모달창 (st.dialog)
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

# --- 사이드바 설정 영역 ---
with st.sidebar:
    st.header("⚙️ 수집 & AI 분석 설정")
    
    saved_pri_kw = get_setting("pri_kw", "통신")
    saved_sec_kw = get_setting("sec_kw", "네트워크, 보안")
    saved_ollama_model = get_setting("ollama_model", "gemma4:e4b-mlx")

    st.subheader("🔑 적합도 가중치 키워드 설정")
    pri_kw = st.text_input("1차 핵심 분야 (가중치 높음)", value=saved_pri_kw, help="예: 통신, 5G, 전회선, 구축")
    sec_kw = st.text_input("2차 관련 분야 (가중치 보통)", value=saved_sec_kw, help="예: 네트워크, 보안, 방화벽, 스위치")
    
    ollama_model = st.text_input("🤖 Ollama 모델명", value=saved_ollama_model)

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
                    upload_db_to_r2(DB_PATH) # R2 스토리지 동기화
                    st.success(f"[{new_site_name}] 사이트 저장 완료!")
                    st.rerun()
                else:
                    st.warning("사이트 이름과 URL을 입력해 주세요.")
        with col_s2:
            if st.button("🗑️ 사이트 삭제", use_container_width=True):
                if selected_site_name:
                    delete_site(selected_site_name)
                    upload_db_to_r2(DB_PATH) # R2 스토리지 동기화
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


# --- 크롤링 및 AI 분석 실행 ---
if run_crawl:
    save_setting("pri_kw", pri_kw.strip())
    save_setting("sec_kw", sec_kw.strip())
    save_setting("ollama_model", ollama_model.strip())

    with st.spinner(f"[{selected_site_name}] 웹페이지 수집, 서류 다운로드 및 AI 분석 진행 중..."):
        bids = crawl_and_download_bids_by_date(
            url=target_url, 
            base_dir=DATA_DIR, 
            start_date=start_d, 
            end_date=end_d, 
            keyword=search_kw,
            site_name=selected_site_name
        )
        
        if not bids:
            st.warning("지정한 기간 내 수집된 공고가 없거나 페이지 응답이 없습니다.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for idx, item in enumerate(bids, 1):
                status_text.text(f"[{idx}/{len(bids)}] AI 분석 중: {item['제목'][:30]}...")
                extracted_text = process_folder_documents(item["폴더경로"])
                
                ai_res = analyze_bid_with_ollama(
                    title=item["제목"],
                    doc_text=extracted_text,
                    pri_keywords=pri_kw,
                    sec_keywords=sec_kw,
                    model_name=ollama_model
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

            # 💡 수집 완료 후 R2 스토리지에 DB 파일 업로드 동기화
            upload_db_to_r2(DB_PATH)

            st.success(f"🎉 총 {len(bids)}건의 공고 분석 및 R2 스토리지 보관 완료!")
            st.rerun()


# --- 메인 데이터 뷰 ---
bids_data = load_all_bids()

if not bids_data:
    st.info("💡 사이드바에서 [🚀 수집 시작] 버튼을 누르면 수집 및 AI 분석이 진행됩니다.")
else:
    df = pd.DataFrame(bids_data)

    df["D-Day"] = df["deadline"].apply(calculate_d_day)
    df["starred_symbol"] = df["starred"].apply(lambda x: "⭐" if x == 1 else "☆")

    col_f1, col_f2, col_f3, col_f4 = st.columns([1.2, 1.4, 2.0, 1.1])
    
    with col_f1:
        unique_sites = ["전체 사이트"] + sorted(list(df["site_name"].unique()))
        selected_site_filter = st.selectbox("🌐 수집 사이트 선택", options=unique_sites)

    with col_f2:
        min_score = st.slider("최저 적합도 점수", 0, 100, 0, step=10)
    with col_f3:
        filter_title = st.text_input("공고 제목 검색", "")
    with col_f4:
        st.markdown('<div style="padding-top: 35px;"></div>', unsafe_allow_html=True)
        only_starred = st.checkbox("⭐ 찜한 공고만", value=False)

    filtered_df = df.copy()
    
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
                "적합도": st.column_config.ProgressColumn(
                    "적합도",
                    format="%d점",
                    min_value=0,
                    max_value=100,
                    width=85
                ),
                "적합사유": st.column_config.TextColumn("적합사유", alignment="left", width=200),
                "사업요약": st.column_config.TextColumn("사업요약", alignment="left", width=350)
            }
        )

        st.markdown("---")
        st.subheader("⚙️ 제안 진행 상태(Pipeline) & 상세 리포트 팝업")
        
        if not filtered_df.empty:
            col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns([0.35, 4.5, 1.4, 1.35, 1.4])
            
            with col_p1:
                st.write("") # 높이 맞춤
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
                st.write("") # 높이 맞춤
                st.write("")
                if st.button("💾 상태 저장", type="primary", use_container_width=True):
                    update_bid_status(selected_row["bid_id"], new_status, 1 if is_star else 0)
                    upload_db_to_r2(DB_PATH) # R2 스토리지 동기화
                    st.success(f"[{selected_row['title'][:15]}...] 상태가 업데이트되었습니다!")
                    st.rerun()

            with col_p5:
                st.write("") # 높이 맞춤
                st.write("")
                if st.button("🔍 팝업 상세보기", use_container_width=True):
                    st.session_state["modal_target_bid"] = selected_row.to_dict()
                    st.rerun()

        # 💡 세션 스테이트 기반 팝업 호출
        if st.session_state["modal_target_bid"] is not None:
            show_bid_detail_modal(st.session_state["modal_target_bid"])

    # TAB 2: 핵심 체크리스트 & RFP 1장 요약 리포트
    with tab2:
        st.subheader("📋 입찰 서류 체크리스트 & 📄 RFP 1장 요약 리포트")
        st.caption("공고별 핵심 서류 요구사항 및 AI 작성 RFP 원페이지 가이드 리포트를 확인합니다.")

        if filtered_df.empty:
            st.warning("필터 조건에 일치하는 공고가 없습니다.")
        else:
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
                            rfp_report = generate_rfp_one_pager(row["title"], doc_text, ollama_model)
                            st.markdown("#### 📄 RFP 1장 요약 리포트:")
                            st.info(rfp_report)

    # TAB 3: AI 서류 Q&A
    with tab3:
        st.subheader("🤖 입찰 첨부 서류 기반 AI 대화")
        
        if filtered_df.empty:
            st.warning("필터 조건에 일치하는 공고가 없습니다.")
        else:
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
                st.caption("ℹ️ **안내**: 첨부서류 파일이 없는 공고입니다. (공고 제목과 AI 요약 정보를 기반으로 AI가 답변을 작성합니다)")

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
                    with st.spinner("로컬 Ollama LLM이 답변을 작성 중입니다..."):
                        answer = answer_bid_question(
                            title=selected_bid['title'], 
                            doc_text=doc_text if doc_text else f"공고 제목: {selected_bid['title']}\n사업 요약: {selected_bid['summary']}", 
                            question=user_question, 
                            model_name=ollama_model
                        )
                        st.markdown("### 🤖 AI 답변:")
                        st.write(answer)
