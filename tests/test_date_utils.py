import unittest
from datetime import date

from date_utils import calculate_d_day, extract_date_from_text


class TestDateUtils(unittest.TestCase):
    def test_extract_date_from_text(self):
        self.assertEqual(extract_date_from_text("2026-07-31"), date(2026, 7, 31))
        self.assertEqual(extract_date_from_text("2026.07.31"), date(2026, 7, 31))
        self.assertEqual(extract_date_from_text("2026년 7월 31일"), date(2026, 7, 31))
        self.assertEqual(extract_date_from_text("26.07.31"), date(2026, 7, 31))
        self.assertIsNone(extract_date_from_text("유효하지 않은 날짜 텍스트"))

    def test_extract_date_range(self):
        self.assertEqual(extract_date_from_text("2026.10.01 ~ 2026.11.15"), date(2026, 11, 15))
        self.assertEqual(extract_date_from_text("2026-10-01 ~ 2026-11-15 18:00"), date(2026, 11, 15))
        self.assertEqual(extract_date_from_text("2026년 10월 01일 ~ 2026년 11월 15일"), date(2026, 11, 15))
        self.assertEqual(extract_date_from_text("26.10.01 ~ 26.11.15"), date(2026, 11, 15))
        self.assertEqual(extract_date_from_text("2026/10/01 - 2026/11/15"), date(2026, 11, 15))

    def test_calculate_d_day(self):
        ref_date = date(2026, 7, 31)
        self.assertEqual(calculate_d_day("2026-07-31", target_date=ref_date), "🔥 D-DAY")
        self.assertEqual(calculate_d_day("2026-08-05", target_date=ref_date), "⏳ D-5")
        self.assertEqual(calculate_d_day("2026-07-25", target_date=ref_date), "마감됨")
        self.assertEqual(calculate_d_day("상세참조", target_date=ref_date), "상세참조")

    def test_calculate_d_day_range(self):
        ref_date = date(2026, 10, 15)
        # 10월 1일 시작, 11월 15일 마감 공고는 10월 15일 기준 D-31이어야 함 (마감됨 아님)
        self.assertEqual(calculate_d_day("2026.10.01 ~ 2026.11.15", target_date=ref_date), "⏳ D-31")
        # MM월 DD일 형식 범위도 마감일 기준 계산
        self.assertEqual(calculate_d_day("10월 01일 ~ 11월 15일", target_date=ref_date), "⏳ D-31")
        # 오늘이 마감일인 범위
        self.assertEqual(calculate_d_day("2026.10.01 ~ 2026.10.15", target_date=ref_date), "🔥 D-DAY")
        # 이미 마감일이 지난 범위
        self.assertEqual(calculate_d_day("2026.10.01 ~ 2026.10.10", target_date=ref_date), "마감됨")

    def test_calculate_d_day_year_rollover(self):
        # 12월 15일 기준, 연도 생략 "1월 10일 마감" 공고는 익년 1월 10일이므로 D-26이어야 함 (마감됨 아님)
        ref_date = date(2026, 12, 15)
        self.assertEqual(calculate_d_day("1월 10일 17:00까지", target_date=ref_date), "⏳ D-26")
        self.assertEqual(calculate_d_day("12월 01일 ~ 01월 15일", target_date=ref_date), "⏳ D-31")
        # 12월 15일 기준, 같은 12월의 과거 날짜(12월 10일)는 마감됨
        self.assertEqual(calculate_d_day("12월 10일 17:00까지", target_date=ref_date), "마감됨")


if __name__ == "__main__":
    unittest.main()
