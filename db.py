import os
import sqlite3
from typing import Optional, Dict, List

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bids_history.db")

def init_db():
    """SQLite 데이터베이스, bids, sites, settings 테이블 초기화 및 마이그레이션"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 공고 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bids (
            bid_id TEXT PRIMARY KEY,
            site_name TEXT,
            num TEXT,
            org TEXT,
            title TEXT,
            reg_date TEXT,
            summary TEXT,
            fit_score INTEGER,
            fit_reason TEXT,
            deadline TEXT,
            submit_type TEXT,
            required_docs TEXT,
            qualifications TEXT,
            starred INTEGER DEFAULT 0,
            status TEXT DEFAULT '검토중',
            folder_path TEXT,
            origin_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 기존 DB 마이그레이션
    cursor.execute("PRAGMA table_info(bids)")
    existing_cols = [col[1] for col in cursor.fetchall()]
    new_cols = {
        "site_name": "TEXT DEFAULT '미지정'",
        "deadline": "TEXT DEFAULT '상세 서류 참조'",
        "submit_type": "TEXT DEFAULT '전자/방문'",
        "required_docs": "TEXT DEFAULT '제안서 등'",
        "qualifications": "TEXT DEFAULT '상세 서류 참조'",
        "starred": "INTEGER DEFAULT 0",
        "status": "TEXT DEFAULT '검토중'",
        "origin_url": "TEXT DEFAULT ''"
    }
    for col_name, col_type in new_cols.items():
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE bids ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass

    # 사이트 관리 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            site_name TEXT PRIMARY KEY,
            site_url TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO sites (site_name, site_url)
        VALUES ('NH농협 입찰공고', 'https://www.nonghyup.com/ecenter/bid/bidList.do')
    """)

    # 사용자 설정 자동 기억 테이블 (키-값)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

def is_bid_exists(bid_id: str) -> bool:
    """게시물 ID가 이미 DB에 존재하는지 확인"""
    if not bid_id:
        return False
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM bids WHERE bid_id = ?", (bid_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def save_bid(bid_data: dict):
    """수집된 공고 및 AI 평가 데이터 DB 저장"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO bids (bid_id, site_name, num, org, title, reg_date, summary, fit_score, fit_reason, deadline, submit_type, required_docs, qualifications, folder_path, starred, status, origin_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bid_id) DO UPDATE SET
            site_name = excluded.site_name,
            summary = excluded.summary,
            fit_score = excluded.fit_score,
            fit_reason = excluded.fit_reason,
            deadline = excluded.deadline,
            submit_type = excluded.submit_type,
            required_docs = excluded.required_docs,
            qualifications = excluded.qualifications,
            folder_path = excluded.folder_path,
            origin_url = excluded.origin_url
    """, (
        bid_data.get("게시물ID", ""),
        bid_data.get("수집사이트", "기타 사이트"),
        bid_data.get("번호", ""),
        bid_data.get("계열사/조직", ""),
        bid_data.get("제목", ""),
        bid_data.get("등록일", ""),
        bid_data.get("사업요약", ""),
        bid_data.get("적합도점수", 0),
        bid_data.get("적합사유", ""),
        bid_data.get("제출마감일시", "상세 서류 참조"),
        bid_data.get("제출방식", "전자/방문"),
        bid_data.get("필수제출서류", "제안서 등"),
        bid_data.get("참가자격요건", "상세 서류 참조"),
        bid_data.get("폴더경로", ""),
        bid_data.get("starred", 0),
        bid_data.get("status", "검토중"),
        bid_data.get("원본URL", "")
    ))
    conn.commit()
    conn.close()

def update_bid_status(bid_id: str, status: str, starred: int):
    """공고의 찜 상태(starred) 및 제안 진행 상태(status) 업데이트"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE bids SET status = ?, starred = ? WHERE bid_id = ?", (status, starred, bid_id))
    conn.commit()
    conn.close()

def load_all_bids() -> List[dict]:
    """DB에 저장된 전체 공고 목록을 가져옴"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bids ORDER BY reg_date DESC, CAST(num AS INTEGER) DESC, created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# --- 사이트 CRUD 함수 ---
def save_site(site_name: str, site_url: str):
    """사이트 이름 및 URL 저장/업데이트"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sites (site_name, site_url)
        VALUES (?, ?)
        ON CONFLICT(site_name) DO UPDATE SET site_url = excluded.site_url
    """, (site_name.strip(), site_url.strip()))
    conn.commit()
    conn.close()

def load_all_sites() -> Dict[str, str]:
    """저장된 사이트 목록을 {사이트명: URL} 딕셔너리로 반환"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT site_name, site_url FROM sites ORDER BY site_name ASC")
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def delete_site(site_name: str):
    """사이트 삭제"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sites WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()

# --- 사용자 설정(키워드/모델) 자동 기억 함수 ---
def save_setting(key: str, value: str):
    """설정값 저장 (키-값)"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()

def get_setting(key: str, default_val: str = "") -> str:
    """설정값 읽기 (없을 경우 기본값 반환)"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default_val
