import os
from datetime import datetime, timedelta, date
import pandas as pd
from crawler import crawl_and_download_bids_by_date
from hwp_parser import process_folder_documents
from ollama_summary import analyze_bid_with_ollama
from db import init_db, save_bid, load_all_bids

def parse_user_date(prompt_msg: str, default_date: date) -> date:
    """사용자 입력 문자열(YYYY-MM-DD)을 date 객체로 파싱합니다."""
    user_input = input(f"{prompt_msg} [기본값: {default_date.strftime('%Y-%m-%d')}]: ").strip()
    if not user_input:
        return default_date
    try:
        return datetime.strptime(user_input, "%Y-%m-%d").date()
    except ValueError:
        print(f"⚠️ 날짜 형식이 올바르지 않아 기본값({default_date})으로 설정합니다.")
        return default_date

def main():
    print("=" * 60)
    print("🚀 맞춤 입찰공고 수집, AI 적합도 분석 및 DB 관리 시스템")
    print("=" * 60)

    # 0. DB 초기화
    init_db()

    today = date.today()
    default_start = today - timedelta(days=7)

    # 1. 대화형 검색 조건 입력 받기
    print("\n📅 [검색 조건 입력]")
    print("👉 그냥 [Enter]를 누르면 기본값으로 실행됩니다.")
    
    url_input = input("1. 수집 대상 URL [기본값: https://www.nonghyup.com/ecenter/bid/bidList.do]: ").strip()
    TARGET_URL = url_input if url_input else "https://www.nonghyup.com/ecenter/bid/bidList.do"

    start_date = parse_user_date("2. 공고 검색 시작일 (YYYY-MM-DD) [기본값: 1주일 전]", default_start)
    end_date = parse_user_date("3. 공고 검색 종료일 (YYYY-MM-DD) [기본값: 오늘]", today)
    
    if start_date > end_date:
        print("⚠️ 시작일이 종료일보다 늦어 두 날짜를 교환합니다.")
        start_date, end_date = end_date, start_date

    keyword = input("4. 사업 키워드/검색어 (엔터 시 전체 수집): ").strip()
    ollama_input = input("5. 로컬 Ollama 모델명 [기본값: gemma4:e4b-mlx]: ").strip()
    OLLAMA_MODEL = ollama_input if ollama_input else "gemma4:e4b-mlx"
    
    kw_disp = f"'{keyword}'" if keyword else '전체 수집'
    print(f"\n✅ 수집 대상 URL: {TARGET_URL}")
    print(f"✅ 설정된 수집 범위: {start_date} ~ {end_date}")
    print(f"✅ 검색 키워드: {kw_disp}")
    print(f"✅ 사용 AI 모델: {OLLAMA_MODEL}")

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")

    # 2. 크롤링 및 파일 다운로드 실행
    print(f"\n[Step 1] 지정된 공고 기간 내 데이터 수집 및 게시물별 폴더 다운로드 중...")
    bids = crawl_and_download_bids_by_date(
        url=TARGET_URL, 
        base_dir=DATA_DIR, 
        start_date=start_date, 
        end_date=end_date, 
        keyword=keyword,
        site_name="NH농협 입찰공고"
    )
    
    if not bids:
        print("\n⚠️ 해당 기간 내 조건에 맞는 입찰 공고가 없습니다.")
        return

    print(f"✅ 총 {len(bids)}개 게시물 수집 및 폴더 구성 완료!")

    # 3. 각 게시물 폴더별 문서 분석 & Ollama 사업 요약 및 적합도 평가 (1차: 통신, 2차: 네트워크, 보안)
    print(f"\n[Step 2] 첨부문서 텍스트 추출 & AI 사업 요약 + 적합도 평가(1차: 통신 / 2차: 네트워크, 보안) 진행 중...")
    
    processed_bids = []
    for idx, item in enumerate(bids, 1):
        folder_path = item["폴더경로"]
        title = item["제목"]
        
        print(f"\n({idx}/{len(bids)}) 📄 게시물: {title[:40]}...")
        
        extracted_text = process_folder_documents(folder_path)
        
        if extracted_text:
            print(f"  └ 📑 추출된 본문 길이: {len(extracted_text)} 자")
        else:
            print("  └ ℹ️ 본문 첨부문서가 없거나 텍스트 추출 대상 파일 없음")

        print(f"  🤖 Ollama ({OLLAMA_MODEL}) 사업 요약 및 적합도 평가 중...")
        ai_res = analyze_bid_with_ollama(title, extracted_text, model_name=OLLAMA_MODEL)
        
        item["사업요약"] = ai_res["summary"]
        item["적합도점수"] = ai_res["fit_score"]
        item["적합사유"] = ai_res["fit_reason"]
        
        print(f"  └ 🎯 적합도 점수: {ai_res['fit_score']}점 (사유: {ai_res['fit_reason'][:40]}...)")
        print(f"  └ ✨ 요약: {ai_res['summary'][:50]}...")

        # 4. DB 저장 (SQLite)
        save_bid(item)
        processed_bids.append(item)

    # 5. DB 저장 안내
    print("\n" + "=" * 60)
    print(f"🎉 모든 작업이 완료되었습니다!")
    print(f"🗄️ 수집 공고 & AI 분석 결과가 SQLite DB(bids_history.db)에 보관되었습니다.")
    print(f"💻 대시보드 조회를 위해 'streamlit run app.py'를 실행하세요.")
    print("=" * 60)

if __name__ == "__main__":
    main()
