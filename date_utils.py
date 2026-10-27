import re
from datetime import datetime, date, timedelta

def extract_date_from_text(text: str) -> date | None:
    """
    다양한 날짜 형식(YYYY.MM.DD, YYYY년 MM월 DD일, YY.MM.DD 등)의 텍스트에서 datetime.date 객체 추출
    """
    if not text:
        return None
    
    # 1. YYYY.MM.DD 또는 YYYY-MM-DD 또는 YYYY/MM/DD
    match = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    # 2. YYYY년 MM월 DD일
    match_kor = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if match_kor:
        try:
            return date(int(match_kor.group(1)), int(match_kor.group(2)), int(match_kor.group(3)))
        except ValueError:
            pass

    # 3. YY.MM.DD 또는 YY-MM-DD
    match_short = re.search(r"(\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text)
    if match_short:
        try:
            yr = 2000 + int(match_short.group(1))
            return date(yr, int(match_short.group(2)), int(match_short.group(3)))
        except ValueError:
            pass

    return None

def calculate_d_day(deadline_str: str, target_date: date = None) -> str:
    """
    마감 문자열(YYYY.MM.DD 등)을 분석하여 오늘(또는 지정일) 기준 D-Day 문자열 반환
    """
    if not deadline_str:
        return "상세참조"
    
    deadline_date = extract_date_from_text(deadline_str)
    if not deadline_date:
        # MM월 DD일 형식 Fallback 처리
        match_md = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", deadline_str)
        if match_md:
            ref_date = target_date or date.today()
            try:
                deadline_date = date(ref_date.year, int(match_md.group(1)), int(match_md.group(2)))
            except ValueError:
                return "상세참조"
        else:
            return "상세참조"

    today = target_date or date.today()
    diff = (deadline_date - today).days

    if diff < 0:
        return "마감됨"
    elif diff == 0:
        return "🔥 D-DAY"
    else:
        return f"⏳ D-{diff}"

def parse_user_date(user_input: str, default_date: date) -> date:
    """
    사용자 입력 문자열(YYYY-MM-DD)을 date 객체로 파싱합니다.
    """
    if not user_input or not user_input.strip():
        return default_date
    try:
        return datetime.strptime(user_input.strip(), "%Y-%m-%d").date()
    except ValueError:
        return default_date
