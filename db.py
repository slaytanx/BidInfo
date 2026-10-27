"""로컬 SQLite 저장과 기존 공고를 보존하는 추가 마이그레이션."""
import hashlib
import json
import os
import sqlite3
from urllib.parse import urlparse
from contextlib import contextmanager
from llm_manager import save_secrets, get_secret

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/bids_history.db')
_READY = set()
EXTRA_COLUMNS = {'source_key': "TEXT DEFAULT ''", 'source_id': "TEXT DEFAULT ''", 'tags': "TEXT DEFAULT '[]'",
 'classification_reason': "TEXT DEFAULT ''", 'extraction_status': "TEXT DEFAULT '미확인'",
 'extraction_details': "TEXT DEFAULT '{}'", 'analysis_status': "TEXT DEFAULT '기존 결과'", 'analysis_model': "TEXT DEFAULT ''",
 'analysis_key': "TEXT DEFAULT ''", 'evidence': "TEXT DEFAULT '[]'", 'download_status': "TEXT DEFAULT '미확인'"}
EXTRA_COLUMNS.update({'solution_tags': "TEXT DEFAULT '[]'", 'work_tags': "TEXT DEFAULT '[]'",
                      'legacy_tags': "TEXT DEFAULT '[]'", 'classification_status': "TEXT DEFAULT '자동'",
                      'manual_classification': "TEXT DEFAULT '{}'", 'notice_text': "TEXT DEFAULT ''"})
SECRET_KEYS = {'e1_key': 'GEMINI_API_KEY', 'e2_key': 'OPENROUTER_API_KEY', 'e3_key': 'NVIDIA_API_KEY'}


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        yield conn
    finally:
        conn.close()


def source_key(site='', url=''):
    host = urlparse(url).hostname or ''
    if 'nonghyupit.com' in host or '농협정보시스템' in site:
        return 'nhit'
    if 'kbstar.com' in host or '국민' in site or 'KB' in site:
        return 'kb'
    if 'nonghyup.com' in host or '농협' in site:
        return 'nh'
    return hashlib.sha256((host or site or 'unknown').encode()).hexdigest()[:12]


def canonical_id(raw_id, site='', url=''):
    prefix = source_key(site, url)
    return str(raw_id) if str(raw_id).startswith(prefix + ':') else prefix + ':' + str(raw_id)


def init_db():
    if DB_PATH in _READY and os.path.exists(DB_PATH):
        return
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db_connection() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS bids (
            bid_id TEXT PRIMARY KEY, site_name TEXT, num TEXT, org TEXT, title TEXT, reg_date TEXT,
            summary TEXT, fit_score INTEGER, fit_reason TEXT, deadline TEXT, submit_type TEXT,
            required_docs TEXT, qualifications TEXT, starred INTEGER DEFAULT 0,
            status TEXT DEFAULT '검토중', folder_path TEXT, origin_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        existing = {r[1] for r in conn.execute('PRAGMA table_info(bids)')}
        columns = {'site_name': "TEXT DEFAULT '미지정'", 'deadline': "TEXT DEFAULT '확인 불가'",
                   'submit_type': "TEXT DEFAULT '확인 불가'", 'required_docs': "TEXT DEFAULT '확인 불가'",
                   'qualifications': "TEXT DEFAULT '확인 불가'", 'starred': 'INTEGER DEFAULT 0',
                   'status': "TEXT DEFAULT '검토중'", 'origin_url': "TEXT DEFAULT ''", **EXTRA_COLUMNS}
        for name, spec in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE bids ADD COLUMN {name} {spec}')
        for raw_id, site, url in conn.execute("SELECT bid_id,site_name,origin_url FROM bids WHERE source_key='' OR source_key IS NULL").fetchall():
            source = source_key(site or '', url or '')
            ident = canonical_id(raw_id, site or '', url or '')
            conn.execute('UPDATE bids SET bid_id=?,source_key=?,source_id=? WHERE bid_id=?', (ident, source, raw_id, raw_id))
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_bid_source ON bids(source_key,source_id)')
        conn.execute('CREATE TABLE IF NOT EXISTS sites (site_name TEXT PRIMARY KEY, site_url TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        defaults = [('NH농협 입찰공고', 'https://www.nonghyup.com/ecenter/bid/bidList.do'),
                    ('KB국민은행', 'https://omoney.kbstar.com/quics?page=C018592'),
                    ('농협정보시스템', 'https://www.nonghyupit.com/nonghyupit/4485/subview.do')]
        conn.executemany('INSERT OR IGNORE INTO sites(site_name,site_url) VALUES (?,?)', defaults)
        conn.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        from taxonomy import seed_taxonomy, legacy_classification, DEFAULT_DOMAINS
        seed_taxonomy(conn)
        if not conn.execute("SELECT 1 FROM settings WHERE key='taxonomy_migrated_v1'").fetchone():
            for bid_id, tags in conn.execute('SELECT bid_id,tags FROM bids').fetchall():
                old = json.loads(tags or '[]')
                converted = legacy_classification(old)
                conn.execute('UPDATE bids SET legacy_tags=?,tags=?,solution_tags=?,work_tags=? WHERE bid_id=?',
                    (json.dumps(old,ensure_ascii=False),json.dumps(converted['tags'],ensure_ascii=False),json.dumps(converted['solution_tags'],ensure_ascii=False),json.dumps(converted['work_tags'],ensure_ascii=False),bid_id))
            old_selection = conn.execute("SELECT value FROM settings WHERE key='selected_categories'").fetchone()
            if old_selection:
                conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('legacy_selected_categories',?)",old_selection)
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('selected_categories',?)",(json.dumps(DEFAULT_DOMAINS,ensure_ascii=False),))
            conn.execute("INSERT INTO settings(key,value) VALUES ('taxonomy_migrated_v1','1')")
        secrets = {SECRET_KEYS[k]: v for k, v in conn.execute('SELECT key,value FROM settings') if k in SECRET_KEYS and v}
        if secrets:
            save_secrets(secrets)
        conn.executemany('DELETE FROM settings WHERE key=?', [(k,) for k in SECRET_KEYS])
        conn.commit()
    _READY.add(DB_PATH)


def is_bid_exists(bid_id, site_name=None, url=''):
    if not bid_id:
        return False
    init_db()
    with get_db_connection() as conn:
        if site_name is not None:
            return conn.execute('SELECT 1 FROM bids WHERE bid_id=?', (canonical_id(bid_id, site_name, url),)).fetchone() is not None
        return conn.execute('SELECT 1 FROM bids WHERE bid_id=? OR source_id=?', (bid_id, bid_id)).fetchone() is not None


def existing_bid_ids(site, url):
    init_db()
    with get_db_connection() as conn:
        return {r[0] for r in conn.execute('SELECT source_id FROM bids WHERE source_key=? AND analysis_status=? AND extraction_status=? AND download_status=?',
                                         (source_key(site, url), '성공', '성공', '성공'))}


MAPPING = {'수집사이트': 'site_name', '번호': 'num', '계열사/조직': 'org', '제목': 'title', '등록일': 'reg_date',
 '사업요약': 'summary', '적합도점수': 'fit_score', '적합사유': 'fit_reason', '제출마감일시': 'deadline',
 '제출방식': 'submit_type', '필수제출서류': 'required_docs', '참가자격요건': 'qualifications',
 '폴더경로': 'folder_path', '원본URL': 'origin_url'}


def save_bid(bid_data):
    init_db()
    values = {MAPPING.get(k, k): v for k, v in bid_data.items() if k in MAPPING or k in EXTRA_COLUMNS or k in ('starred', 'status')}
    raw = str(bid_data.get('게시물ID', ''))
    if not raw:
        raise ValueError('게시물 ID 없음')
    source = source_key(values.get('site_name', ''), values.get('origin_url', ''))
    values.update(bid_id=canonical_id(raw, values.get('site_name', ''), values.get('origin_url', '')), source_key=source, source_id=raw.removeprefix(source + ':'))
    for name in ('tags', 'solution_tags', 'work_tags', 'legacy_tags', 'manual_classification', 'extraction_details', 'evidence'):
        if name in values and not isinstance(values[name], str):
            values[name] = json.dumps(values[name], ensure_ascii=False)
    names = list(values)
    updates = [n for n in names if n not in ('bid_id', 'starred', 'status')]
    # 분석 실패 재시도는 이미 저장된 정상 요약을 지우지 않는다.
    if values.get('analysis_status') == '실패':
        updates = [n for n in updates if n not in ('summary', 'fit_score', 'fit_reason', 'deadline', 'submit_type', 'required_docs', 'qualifications', 'evidence')]
    with get_db_connection() as conn:
        previous = conn.execute('SELECT manual_classification FROM bids WHERE bid_id=?',(values['bid_id'],)).fetchone()
        if previous and json.loads(previous[0] or '{}'):
            updates = [n for n in updates if n not in ('tags','solution_tags','work_tags','classification_reason','manual_classification','classification_status')]
        conflict_action = 'DO UPDATE SET ' + ','.join(f'{n}=excluded.{n}' for n in updates) if updates else 'DO NOTHING'
        conn.execute(f"INSERT INTO bids ({','.join(names)}) VALUES ({','.join('?' for _ in names)}) ON CONFLICT(bid_id) {conflict_action}", [values[n] for n in names])
        conn.commit()


def update_enrichment(bid_id, **values):
    if not values or any(k not in EXTRA_COLUMNS for k in values):
        raise ValueError('잘못된 보강 필드')
    init_db()
    for k in ('tags', 'solution_tags', 'work_tags', 'manual_classification', 'legacy_tags', 'extraction_details', 'evidence'):
        if k in values and not isinstance(values[k], str):
            values[k] = json.dumps(values[k], ensure_ascii=False)
    with get_db_connection() as conn:
        previous = conn.execute('SELECT manual_classification FROM bids WHERE bid_id=?',(bid_id,)).fetchone()
        if previous and json.loads(previous[0] or '{}'):
            values = {k:v for k,v in values.items() if k not in ('tags','solution_tags','work_tags','classification_reason','classification_status','manual_classification')}
        if not values:
            return
        conn.execute('UPDATE bids SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE bid_id=?', [*values.values(), bid_id])
        conn.commit()


def manual_classifications(site, url):
    init_db()
    with get_db_connection() as conn:
        return {raw: json.loads(value) for raw,value in conn.execute("SELECT source_id,manual_classification FROM bids WHERE source_key=? AND manual_classification!='{}'",(source_key(site,url),))}


def set_manual_classification(bid_id, domains, solutions, work, collect=True):
    from classification import normalize_classification
    result = normalize_classification({'tags':domains,'solution_tags':solutions,'work_tags':work})
    if collect and not result['tags']:
        raise ValueError('분야를 하나 이상 선택해 주세요')
    result.update(collect=bool(collect),certain=True,reason='사용자 수동 분류')
    init_db()
    with get_db_connection() as conn:
        ident=_resolve_id(conn,bid_id)
        row=conn.execute('SELECT download_status,analysis_status FROM bids WHERE bid_id=?',(ident,)).fetchone()
        if not row: raise ValueError('공고를 찾을 수 없습니다')
        complete = row[0]=='성공' and row[1]=='성공'
        state = '수집 대기' if collect and not complete else '수동 분류' if collect else '수집 제외'
        conn.execute('UPDATE bids SET tags=?,solution_tags=?,work_tags=?,manual_classification=?,classification_status=?,classification_reason=? WHERE bid_id=?',
            (json.dumps(result['tags'],ensure_ascii=False),json.dumps(result['solution_tags'],ensure_ascii=False),json.dumps(result['work_tags'],ensure_ascii=False),json.dumps(result,ensure_ascii=False),state,result['reason'],ident))
        conn.commit()


def finish_manual_collection(bid_id, success):
    init_db()
    with get_db_connection() as conn:
        conn.execute('UPDATE bids SET classification_status=? WHERE bid_id=? AND manual_classification!=?',
                     ('수동 분류' if success else '수집 대기',bid_id,'{}'));conn.commit()


def update_bid_status(bid_id, status, starred):
    init_db()
    with get_db_connection() as conn:
        resolved = _resolve_id(conn, bid_id)
        conn.execute('UPDATE bids SET status=?,starred=? WHERE bid_id=?', (status, starred, resolved)); conn.commit()


def _resolve_id(conn, bid_id):
    if conn.execute('SELECT 1 FROM bids WHERE bid_id=?', (bid_id,)).fetchone():
        return bid_id
    rows = conn.execute('SELECT bid_id FROM bids WHERE source_id=?', (bid_id,)).fetchall()
    if len(rows) > 1:
        raise ValueError('여러 수집원에 같은 ID가 있습니다. 수집원 포함 ID를 사용해 주세요')
    return rows[0][0] if rows else bid_id


def delete_bids(bid_ids):
    if not bid_ids:
        return
    init_db()
    with get_db_connection() as conn:
        resolved = [_resolve_id(conn, bid_id) for bid_id in bid_ids]
        conn.execute(f"DELETE FROM bids WHERE bid_id IN ({','.join('?' for _ in resolved)})", resolved); conn.commit()


def reset_all_bids():
    init_db()
    with get_db_connection() as conn:
        conn.execute('DELETE FROM bids'); conn.commit()


def load_all_bids():
    init_db()
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM bids ORDER BY reg_date DESC,CAST(num AS INTEGER) DESC,created_at DESC')]


def save_site(site_name, site_url):
    init_db()
    with get_db_connection() as conn:
        conn.execute('INSERT INTO sites(site_name,site_url) VALUES (?,?) ON CONFLICT(site_name) DO UPDATE SET site_url=excluded.site_url', (site_name.strip(), site_url.strip())); conn.commit()


def load_all_sites():
    init_db()
    with get_db_connection() as conn:
        return dict(conn.execute('SELECT site_name,site_url FROM sites ORDER BY site_name'))


def delete_site(site_name):
    init_db()
    with get_db_connection() as conn:
        conn.execute('DELETE FROM sites WHERE site_name=?', (site_name,)); conn.commit()


def save_setting(key, value):
    if key in SECRET_KEYS:
        save_secrets({SECRET_KEYS[key]: value}); return
    init_db()
    with get_db_connection() as conn:
        conn.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value))); conn.commit()


def get_setting(key, default_val=''):
    init_db()
    if key in SECRET_KEYS:
        return get_secret(SECRET_KEYS[key], default_val)
    with get_db_connection() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default_val
