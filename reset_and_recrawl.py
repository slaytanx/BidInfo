import os
import shutil
import sqlite3
from datetime import date, timedelta
from crawler import crawl_and_download_bids_by_date
from hwp_parser import process_folder_documents
from ollama_summary import analyze_bid_with_ollama
from db import init_db, save_bid, get_setting, load_all_sites

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "bids_history.db")

def reset_data_and_database():
    """기존 DB 및 data 폴더 내 수집 데이터를 깔끔히 초기화"""
    print("🧹 [초기화 1단계] DB 및 수집 폴더 데이터 정리 시작...")
    
    # 1. DB 초기화 (bids 테이블 데이터 비우기)
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bids")
        conn.commit()
        conn.close()
        print("  - 🗑️ SQLite bids 테이블 비우기 완료")

    # 2. data 폴더 내 파일/폴더 삭제 (bids_history.db 파일 및 .gitkeep 등은 보존)
    if os.path.exists(DATA_DIR):
        for item in os.listdir(DATA_DIR):
            if item == "bids_history.db":
                continue
            item_path = os.path.join(DATA_DIR, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            elif os.path.isfile(item_path) and not item.startswith("."):
                os.remove(item_path)
        print("  - 🗑️ data 폴더 내 기존 수집 파일/폴더 삭제 완료")

    init_db()
    print("✅ 데이터베이스 및 폴더 클린 초기화 완수!")

def recrawl_all():
    """최근 30일간의 NH농협 및 KB국민은행 입찰 데이터 퍼펙트 일괄 수집"""
    today = date.today()
    start_d = today - timedelta(days=30)

    sites = load_all_sites()
    pri_kw = get_setting("pri_kw", "통신")
    sec_kw = get_setting("sec_kw", "네트워크, 보안")
    ollama_model = get_setting("ollama_model", "gemma4:e4b-mlx")

    print(f"\n🚀 [클린 재수집 구동] 수집 기간: {start_d} ~ {today}")

    total_count = 0
    for site_name, site_url in sites.items():
        print(f"\n🌐 [{site_name}] 일괄 퍼펙트 수집 시작...")
        bids = crawl_and_download_bids_by_date(
            url=site_url,
            base_dir=DATA_DIR,
            start_date=start_d,
            end_date=today,
            site_name=site_name
        )
        
        print(f"  └ 📦 총 {len(bids)}건 수집 및 서류 다운로드/ZIP 해제 완료! AI 평가 진행 중...")
        
        for idx, item in enumerate(bids, 1):
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
            print(f"    - [{idx}/{len(bids)}] {item['제목'][:30]}... (적합도: {ai_res['fit_score']}점)")
        
        total_count += len(bids)

    print(f"\n🎉 모든 사이트 클린 재수집 & AI 분석 완료! (총 {total_count}건 보관)")

if __name__ == "__main__":
    reset_data_and_database()
    recrawl_all()
