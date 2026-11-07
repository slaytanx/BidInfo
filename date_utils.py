import re
from datetime import datetime, date

def extract_date_from_text(text: str) -> date | None:
    """
    다양한 날짜 형식(YYYY.MM.DD, YYYY년 MM월 DD일, YY.MM.DD 등)의 텍스트에서 datetime.date 객체 추출
    """
    if not text:
        return None
    
    # 1. YYYY.MM.DD 또는 YYYY-MM-DD 또는 YYYY/MM/DD
    for match in reversed(list(re.finditer(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text))):
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    # 2. YYYY년 MM월 DD일
    for match_kor in reversed(list(re.finditer(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text))):
        try:
            return date(int(match_kor.group(1)), int(match_kor.group(2)), int(match_kor.group(3)))
        except ValueError:
            pass

    # 3. YY.MM.DD 또는 YY-MM-DD
    for match_short in reversed(list(re.finditer(r"(\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text))):
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
        matches_md = list(re.finditer(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", deadline_str))
        if matches_md:
            ref_date = target_date or date.today()
            for match_md in reversed(matches_md):
                try:
                    month = int(match_md.group(1))
                    day = int(match_md.group(2))
                    # 연말(10~12월)에 고시된 익년 초(1~3월) 마감 공고의 연도 롤오버 보정
                    year = ref_date.year + 1 if ref_date.month >= 10 and month <= 3 else ref_date.year
                    deadline_date = date(year, month, day)
                    break
                except ValueError:
                    pass
            if not deadline_date:
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
