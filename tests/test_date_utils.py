import unittest
from datetime import date

from date_utils import calculate_d_day, extract_date_from_text, parse_user_date


class TestDateUtils(unittest.TestCase):
    def test_extract_date_from_text(self):
        self.assertEqual(extract_date_from_text("2026-07-31"), date(2026, 7, 31))
        self.assertEqual(extract_date_from_text("2026.07.31"), date(2026, 7, 31))
        self.assertEqual(extract_date_from_text("2026년 7월 31일"), date(2026, 7, 31))
        self.assertEqual(extract_date_from_text("26.07.31"), date(2026, 7, 31))
        self.assertIsNone(extract_date_from_text("유효하지 않은 날짜 텍스트"))

    def test_calculate_d_day(self):
        ref_date = date(2026, 7, 31)
        self.assertEqual(calculate_d_day("2026-07-31", target_date=ref_date), "🔥 D-DAY")
        self.assertEqual(calculate_d_day("2026-08-05", target_date=ref_date), "⏳ D-5")
        self.assertEqual(calculate_d_day("2026-07-25", target_date=ref_date), "마감됨")
        self.assertEqual(calculate_d_day("상세참조", target_date=ref_date), "상세참조")

    def test_parse_user_date(self):
        default_d = date(2026, 1, 1)
        self.assertEqual(parse_user_date("2026-07-31", default_d), date(2026, 7, 31))
        self.assertEqual(parse_user_date("잘못된입력", default_d), default_d)
        self.assertEqual(parse_user_date("", default_d), default_d)

if __name__ == "__main__":
    unittest.main()
