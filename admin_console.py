import json
import math
import os
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from benchmark import run_benchmark
from classification import CATEGORIES, DEFAULT_CATEGORIES
from db import (
    DB_PATH,
    delete_bids,
    delete_site,
    get_setting,
    load_all_bids,
    load_all_sites,
    reset_all_bids,
    save_setting,
    save_site,
)
from llm_manager import (
    CLOUD_MODELS,
    LOCAL_MODELS,
    RunBudget,
    engine_configs,
    get_secret,
    save_secrets,
)
from ollama_summary import PROMPT_VERSION
from pipeline import backfill_existing, reanalyze_existing
from r2_storage import delete_r2_folder, sync_local_to_r2, upload_db_to_r2
from taxonomy_ui import show_taxonomy_editor

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def login_allowed(expected, typed):
    """비밀번호가 설정되어 있고 입력이 일치할 때만 로그인을 허용한다."""
    return bool(expected) and bool(str(expected).strip()) and typed is not None and str(typed) == str(expected)


def get_safe_run_budget(default: float = 1.0, min_val: float = 0.0, max_val: float = 100.0) -> float:
    """DB에서 run_budget 설정을 안전하게 읽어와 NaN/무한대를 필터링하고 [min_val, max_val] 범위로 클램핑한다."""
    try:
        raw_val = float(get_setting("run_budget", str(default)))
        if not math.isfinite(raw_val):
            return default
        return max(min_val, min(max_val, raw_val))
    except (ValueError, TypeError):
        return default


def get_bid_r2_folder_key(folder_p: str) -> str:
    """공고 로컬 경로에서 R2 폴더 키('data/<folder_name>')를 안전하게 추출한다."""
    if not folder_p:
        return ""
    clean_p = folder_p.rstrip("/\\")
    folder_bname = os.path.basename(clean_p)
    return f"data/{folder_bname}" if folder_bname else ""


def render_model_and_budget_tab():
    """[탭 1] AI 모델 연결 & 예산 (Model & Budget)"""
    st.markdown("### 기본 분석 모델 설정 (Ollama & OpenRouter)")
    st.caption("수집된 공고의 상세 요약 및 질의응답에 사용할 AI 모델을 설정합니다.")
    with st.form("admin_model_selection_form"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            saved_local = get_setting("local_model", LOCAL_MODELS[0])
            local_model_val = st.selectbox(
                "로컬 분석 모델 (Ollama)",
                LOCAL_MODELS,
                index=LOCAL_MODELS.index(saved_local) if saved_local in LOCAL_MODELS else 0,
                key="admin_local_model_select",
                help="로컬 Ollama에서 구동할 분석 모델을 선택합니다.",
            )
        with col_m2:
            saved_cloud = get_setting("cloud_model", CLOUD_MODELS[0])
            cloud_model_val = st.selectbox(
                "클라우드 분석 모델 (OpenRouter)",
                CLOUD_MODELS,
                index=CLOUD_MODELS.index(saved_cloud) if saved_cloud in CLOUD_MODELS else 0,
                key="admin_cloud_model_select",
                help="OpenRouter를 통해 호출할 클라우드 분석 모델을 선택합니다.",
            )
        if st.form_submit_button("모델 설정 저장"):
            save_setting("local_model", local_model_val)
            save_setting("cloud_model", cloud_model_val)
            st.success(f"로컬 모델 [{local_model_val}], 클라우드 모델 [{cloud_model_val}] 설정이 저장되었습니다.")

    st.markdown("---")
    st.markdown("### OpenRouter API 키 연결")
    st.caption("API 키는 로컬 비밀 파일에 저장하며 공고 DB와 R2에는 포함하지 않습니다.")
    with st.form("admin_model_credentials"):
        openrouter_key = st.text_input(
            "OpenRouter API 키",
            type="password",
            value=get_secret("OPENROUTER_API_KEY"),
            key="admin_openrouter_key",
        )
        if st.form_submit_button("연결 설정 저장"):
            save_secrets({"OPENROUTER_API_KEY": openrouter_key})
            st.success("로컬 연결 설정을 저장했습니다.")

    st.markdown("---")
    st.markdown("### 실행당 클라우드 예산 설정")
    st.caption("수집 및 분석 실행 1회당 허용할 최대 클라우드 API 지출 한도를 설정합니다.")
    with st.form("admin_budget_form"):
        current_budget = get_safe_run_budget(default=1.0, min_val=0.0, max_val=100.0)
        budget_val = st.number_input(
            "실행당 클라우드 예산 (USD)",
            min_value=0.0,
            max_value=100.0,
            value=current_budget,
            step=0.5,
            key="admin_run_budget",
        )
        if st.form_submit_button("예산 설정 저장"):
            save_setting("run_budget", str(budget_val))
            st.success(f"실행당 클라우드 예산이 {budget_val:.1f} USD로 저장되었습니다.")

    st.markdown("---")
    st.markdown("### 적합도 평가 가중치 키워드 설정")
    st.caption("AI 모델이 사업 공고의 관련성을 평가할 때 가중치를 부여할 키워드를 설정합니다.")
    with st.form("admin_keywords_form"):
        saved_pri_kw = get_setting("pri_kw", "통신")
        saved_sec_kw = get_setting("sec_kw", "네트워크, 보안")
        pri_kw_val = st.text_input("1차 핵심 분야 (가중치 높음)", value=saved_pri_kw, key="admin_pri_kw")
        sec_kw_val = st.text_input("2차 관련 분야 (가중치 보통)", value=saved_sec_kw, key="admin_sec_kw")
        if st.form_submit_button("가중치 키워드 저장"):
            save_setting("pri_kw", pri_kw_val.strip())
            save_setting("sec_kw", sec_kw_val.strip())
            st.success("적합도 평가 가중치 키워드가 저장되었습니다.")


def render_sites_and_r2_tab():
    """[탭 2] 수집 사이트 & Cloudflare R2 백업 (Sites & R2)"""
    st.markdown("### 수집 대상 사이트 관리")
    st.caption("등록된 수집 사이트 목록을 조회하고, 신규 사이트를 등록하거나 기존 사이트를 삭제합니다.")

    sites = load_all_sites()
    if "KB국민은행" not in sites:
        save_site("KB국민은행", "https://omoney.kbstar.com/quics?page=C018592")
        sites = load_all_sites()

    st.markdown("#### 현재 등록된 사이트 목록")
    if sites:
        sites_df = pd.DataFrame([{"사이트 이름": k, "URL": v} for k, v in sites.items()])
        st.dataframe(sites_df, width="stretch", hide_index=True)
    else:
        st.info("현재 등록된 사이트가 없습니다.")

    st.markdown("#### 수집 사이트 신규 저장 / 삭제")
    st.caption("비로그인 공개 게시판(신한DS 사업공시, 하나금융티아이 등)을 신규 등록하거나 삭제할 수 있습니다.")

    # 1행: 사이트 신규 등록 (이름, URL, 등록 버튼)
    col_r1, col_r2, col_r3 = st.columns([1.2, 2.5, 1.0], vertical_alignment="bottom")
    with col_r1:
        new_site_name = st.text_input("사이트 이름", placeholder="예: 신한DS 사업공시", key="admin_new_site_name")
    with col_r2:
        new_site_url = st.text_input("사이트 URL", placeholder="https://www.shinhands.co.kr/insight/disclosure", key="admin_new_site_url")
    with col_r3:
        if st.button("사이트 등록", width="stretch", key="admin_save_site_btn"):
            if new_site_name.strip() and new_site_url.strip():
                save_site(new_site_name.strip(), new_site_url.strip())
                upload_db_to_r2(DB_PATH)
                st.success(f"[{new_site_name.strip()}] 사이트 저장 완료!")
                st.rerun()
            else:
                st.warning("사이트 이름과 URL을 모두 입력해 주세요.")

    st.markdown("<div style='margin-top: 8px;'></div>", unsafe_allow_html=True)

    # 2행: 등록 사이트 삭제 (안내 라벨, 선택바, 삭제 버튼)
    col_d1, col_d2, col_d3 = st.columns([1.2, 2.5, 1.0], vertical_alignment="bottom")
    with col_d1:
        st.markdown("<p style='margin-bottom: 8px; font-weight: 500; font-size: 0.95rem;'>삭제할 사이트 선택</p>", unsafe_allow_html=True)
    with col_d2:
        site_to_delete = st.selectbox(
            "삭제할 사이트 선택",
            options=list(sites.keys()) if sites else [],
            label_visibility="collapsed",
            key="admin_site_to_delete",
        )
    with col_d3:
        if st.button("사이트 삭제", width="stretch", key="admin_delete_site_btn", disabled=not bool(sites)):
            if site_to_delete:
                delete_site(site_to_delete)
                upload_db_to_r2(DB_PATH)
                st.success(f"[{site_to_delete}] 삭제 완료!")
                st.rerun()

    st.markdown("---")
    st.markdown("### Cloudflare R2 스토리지 동기화")
    st.caption("로컬 SQLite DB와 첨부파일을 Cloudflare R2 오브젝트 스토리지에 백업 및 동기화합니다.")
    current_r2 = get_setting("r2_enabled", "0")
    r2_enabled = st.checkbox("R2 동기화 사용", value=(current_r2 == "1"), key="admin_r2_enabled")
    new_r2 = "1" if r2_enabled else "0"
    if new_r2 != current_r2:
        save_setting("r2_enabled", new_r2)
    os.environ["BIDINFO_R2_ENABLED"] = new_r2

    if st.button("로컬 DB·첨부를 R2에 동기화", disabled=not r2_enabled, width="stretch", key="admin_sync_r2_btn"):
        with st.spinner("로컬 자료를 R2에 동기화 중입니다..."):
            synced = sync_local_to_r2(DATA_DIR)
        st.info(f"동기화 결과: 성공 {synced['success']}개 / 실패 {synced['failed']}개")


def render_maintenance_and_benchmark_tab():
    """[탭 3] 데이터 재처리 & 벤치마크 (Maintenance & Benchmark)"""
    st.markdown("### 데이터 재추출 및 재분석")
    st.caption("기존에 수집된 공고 문서를 재추출하거나, 특정 분야 공고를 최신 모델로 재분석합니다.")

    if st.button("기존 공고 문서 재추출·분류", width="stretch", key="admin_backfill_btn"):
        with st.spinner("기존 요약을 보존하고 문서·분류를 보강합니다..."):
            st.session_state["last_run_metrics"] = backfill_existing()
        st.success("기존 공고 문서 재추출 및 분류 작업이 완료되었습니다.")
        st.rerun()

    default_reanalyze_cats = json.loads(get_setting("selected_categories", json.dumps(DEFAULT_CATEGORIES)))
    reanalyze_cats = st.multiselect(
        "재분석 대상 분야 선택",
        CATEGORIES,
        default=[c for c in default_reanalyze_cats if c in CATEGORIES],
        key="admin_reanalyze_cats",
    )
    if st.button("선택 분야 기존 공고 재분석", width="stretch", key="admin_reanalyze_btn"):
        if not reanalyze_cats:
            st.warning("재분석할 분야를 하나 이상 선택해 주세요.")
        else:
            budget_limit = get_safe_run_budget(default=1.0, min_val=0.0, max_val=100.0)
            try:
                budget = RunBudget(budget_limit)
            except (ValueError, TypeError, ArithmeticError):
                budget = RunBudget(1.0)
            cloud_model = get_setting("cloud_model", CLOUD_MODELS[0])
            local_model = get_setting("local_model", LOCAL_MODELS[0])
            analysis_mode = get_setting("analysis_mode", "혼합")
            with st.spinner("선택 분야의 기존 첨부를 분석합니다..."):
                _, stats = reanalyze_existing(
                    engine_configs(analysis_mode, cloud_model, local_model, budget),
                    reanalyze_cats,
                )
            st.session_state["last_run_metrics"] = dict(stats, **budget.snapshot())
            st.success("선택 분야 기존 공고 재분석이 완료되었습니다.")
            st.rerun()

    st.markdown("---")
    st.markdown("### 모델 비교 벤치마크")
    with st.expander("모델 비교 결과", expanded=True):
        report = Path(DATA_DIR) / "model_benchmark.json"
        if report.exists():
            try:
                benchmark_result = json.loads(report.read_text(encoding="utf-8"))
                st.dataframe(benchmark_result.get("results", []), width="stretch")
                if benchmark_result.get("prompt_version") != PROMPT_VERSION:
                    st.caption("분류 체계 변경 이전의 비교 결과입니다. 선택 모델로 다시 비교할 수 있습니다.")
            except (json.JSONDecodeError, OSError) as ex:
                st.caption(f"벤치마크 결과 확인 실패: {ex}")
        else:
            st.caption("아직 모델 실측 결과가 없습니다.")
        st.caption("동일한 한글 RFP 12건으로 속도·요건 정확도·JSON 성공률·비용을 비교합니다.")

        current_local = get_setting("local_model", LOCAL_MODELS[0])
        current_cloud = get_setting("cloud_model", CLOUD_MODELS[0])
        current_mode = get_setting("analysis_mode", "혼합")
        available_models = list(dict.fromkeys(LOCAL_MODELS + CLOUD_MODELS))
        default_models = [current_local] + ([current_cloud] if current_mode != "로컬" and current_cloud != current_local else [])
        selected_benchmark_models = st.multiselect(
            "비교 실행 모델 선택",
            options=available_models,
            default=[m for m in default_models if m in available_models],
            key="admin_benchmark_models",
        )
        if st.button("선택 모델로 비교 실행", width="stretch", key="admin_run_benchmark_btn"):
            if not selected_benchmark_models:
                st.warning("비교 실행할 모델을 하나 이상 선택해 주세요.")
            else:
                run_budget = get_safe_run_budget(default=1.0, min_val=0.0, max_val=100.0)
                with st.spinner("한글 합성 RFP 12건으로 모델을 비교합니다..."):
                    run_benchmark(selected_benchmark_models, run_budget, output=report)
                st.success("모델 비교 벤치마크 실행 완료!")
                st.rerun()


def render_taxonomy_and_clean_tab():
    """[탭 4] 분류 사전 & 공고 삭제 센터 (Taxonomy & Clean)"""
    show_taxonomy_editor()

    st.markdown("---")
    st.markdown("### 불필요 데이터 & 첨부파일 통합 삭제 센터")
    st.caption("선택한 공고를 DB에서 제거하고, 컴퓨터 로컬 폴더 및 Cloudflare R2 스토리지 내부 파일까지 한 번에 깨끗이 지웁니다.")

    flash = st.session_state.pop("admin_flash", None)
    if flash:
        st.success(flash)

    all_bids_for_admin = load_all_bids()
    if not all_bids_for_admin:
        st.info("현재 저장된 공고 데이터가 없습니다.")
    else:
        bid_map = {f"[{b['num']}] [{b['org']}] {b['title']} ({b['reg_date']})": b for b in all_bids_for_admin}
        selected_del_labels = st.multiselect(
            "삭제할 공고 목록 선택 (다중 선택 가능)",
            options=list(bid_map.keys()),
            key="admin_del_bids_select",
        )

        confirm_del = st.checkbox(
            f"선택한 {len(selected_del_labels)}건과 첨부파일(로컬·R2)을 복구할 수 없음을 확인했습니다.",
            key="admin_del_confirm",
            disabled=not selected_del_labels,
        )
        if st.button("선택한 공고 및 첨부파일/R2 동기화 완결 삭제", type="primary", width="stretch",
                     key="admin_del_selected_btn", disabled=not (selected_del_labels and confirm_del)):
            del_ids, failed = delete_bid_items([bid_map[label] for label in selected_del_labels])
            st.session_state["admin_flash"] = (
                f"선택한 {len(del_ids)}건의 공고를 삭제했습니다."
                + (f" 로컬 폴더 삭제에 실패한 {len(failed)}건은 직접 확인해 주세요." if failed else "")
            )
            st.session_state["admin_del_confirm"] = False
            st.rerun()

        with st.expander("[주의] 전체 공고 및 파일 클린 포맷"):
            st.warning("저장된 모든 공고 데이터와 로컬 data/ 폴더 및 Cloudflare R2 파일이 전부 삭제됩니다.")
            typed = st.text_input("계속하려면 '초기화'를 입력하세요.", key="admin_reset_confirm_text")
            if st.button("전체 공고 데이터 & 파일 클린 포맷 실행", key="admin_reset_all_btn",
                         disabled=typed.strip() != "초기화"):
                reset_all_bids()
                if os.path.exists(DATA_DIR):
                    for item in os.listdir(DATA_DIR):
                        item_p = os.path.join(DATA_DIR, item)
                        if item != "bids_history.db" and os.path.isdir(item_p):
                            shutil.rmtree(item_p)
                delete_r2_folder("data")
                upload_db_to_r2(DB_PATH)
                st.session_state["admin_flash"] = "전체 공고와 첨부파일을 초기화했습니다."
                st.session_state["admin_reset_confirm_text"] = ""
                st.rerun()


def delete_bid_items(items):
    """공고와 로컬·R2 첨부를 삭제하고 (삭제한 id 목록, 로컬 폴더 삭제 실패 id 목록)을 돌려준다."""
    del_ids, failed = [], []
    for item in items:
        del_ids.append(item["bid_id"])
        folder_p = item.get("folder_path", "")
        if folder_p and os.path.exists(folder_p):
            try:
                shutil.rmtree(folder_p)
            except OSError:
                failed.append(item["bid_id"])
        if folder_p:
            r2_key = get_bid_r2_folder_key(folder_p)
            if r2_key:
                delete_r2_folder(r2_key)
    delete_bids(del_ids)
    upload_db_to_r2(DB_PATH)
    return del_ids, failed


# 💡 시크릿 톱니바퀴 클릭 시 뜨는 🔒 시스템 어드민 관리자 팝업 모달창
@st.dialog("⚙️ 시스템 관리자 센터 (Admin Console)", width="large")
def show_admin_console_modal(expected_password=None):
    st.caption("시스템 관리자 모달: 모델 연결, 수집 사이트, R2 백업, 데이터 재처리, 분류 체계 및 데이터 관리를 제공합니다.")

    if not st.session_state.get("admin_logged_in"):
        # 비밀번호 미설정 시 열지 않음(fail-closed)
        if not expected_password:
            st.error("관리자 비밀번호(ADMIN_PASSWORD)가 설정되지 않아 콘솔을 열 수 없습니다.")
        else:
            with st.form("admin_login_form"):
                entered = st.text_input("관리자 비밀번호", type="password")
                if st.form_submit_button("로그인", type="primary"):
                    if login_allowed(expected_password, entered):
                        st.session_state["admin_logged_in"] = True
                        st.rerun()
                    st.error("비밀번호가 올바르지 않습니다.")
        if st.button("닫기", key="admin_login_close_btn"):
            st.session_state["show_admin_modal"] = False
            st.rerun()
        return

    tab_model, tab_sites, tab_maint, tab_taxonomy = st.tabs([
        "AI 모델 연결 & 예산",
        "수집 사이트 & Cloudflare R2 백업",
        "데이터 재처리 & 벤치마크",
        "분류 사전 & 공고 삭제 센터",
    ])

    with tab_model:
        render_model_and_budget_tab()

    with tab_sites:
        render_sites_and_r2_tab()

    with tab_maint:
        render_maintenance_and_benchmark_tab()

    with tab_taxonomy:
        render_taxonomy_and_clean_tab()

    if st.button("닫기", type="primary", width="stretch", key="admin_modal_close_btn"):
        st.session_state["show_admin_modal"] = False
        st.rerun()

