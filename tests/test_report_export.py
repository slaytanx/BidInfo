import io
import re
import unittest
from openpyxl import load_workbook
from report_export import generate_excel_export, generate_bid_review_markdown, generate_bid_review_html


class ReportExportTests(unittest.TestCase):
    def setUp(self):
        self.sample_bid = {
            "bid_id": "kb:12345",
            "num": "1",
            "site_name": "KB국민은행",
            "org": "KB국민은행",
            "title": "지능형 위협 대응 보안 고도화(방화벽) 구매 입찰공고",
            "reg_date": "2026-11-01",
            "deadline": "2026-11-20 17:00",
            "submit_type": "방문 제출",
            "required_docs": "입찰참가신청서, 제안서 10부, 가격제안서 1부, 사업자등록증",
            "qualifications": "정보보안 전문업체 등록법인",
            "summary": "침입차단시스템 방화벽 교체 및 고도화 사업입니다. 안정적인 망 분리 환경을 구축합니다.",
            "fit_score": 85,
            "fit_reason": "당사 주력 보안 솔루션 구축 요구조건과 일치함",
            "starred": 1,
            "status": "제안작성중",
            "origin_url": "https://omoney.kbstar.com/quics?page=C018592&bid=12345",
            "tags": '["보안", "인프라"]',
            "solution_tags": '["방화벽", "IPS"]',
            "work_tags": '["구축"]',
            "evidence": '[{"field": "budget", "quote": "총 예산 15억원", "location": "제안요청서 3p"}, {"field": "deadline", "quote": "2026-11-20 17:00까지", "location": "공고문 1p"}]'
        }

    def test_generate_excel_export(self):
        bids = [self.sample_bid]
        xlsx_bytes = generate_excel_export(bids)
        self.assertIsInstance(xlsx_bytes, bytes)
        self.assertGreater(len(xlsx_bytes), 1000)
        # Check PK zip header for xlsx
        self.assertTrue(xlsx_bytes.startswith(b"PK\x03\x04"))

    def test_generate_excel_export_hyperlink_validation(self):
        valid_bid = dict(self.sample_bid, origin_url="https://example.com/valid")
        invalid_bid = dict(self.sample_bid, origin_url="javascript:alert(1)")
        empty_url_bid = dict(self.sample_bid, origin_url="")

        xlsx_bytes = generate_excel_export([valid_bid, invalid_bid, empty_url_bid])
        wb = load_workbook(io.BytesIO(xlsx_bytes))
        ws = wb.active

        # Row 2 (valid_bid): col 21 should have hyperlink and value "바로가기"
        cell_valid = ws.cell(row=2, column=21)
        self.assertEqual(cell_valid.value, "바로가기")
        self.assertIsNotNone(cell_valid.hyperlink)
        self.assertEqual(cell_valid.hyperlink.target, "https://example.com/valid")

        # Row 3 (invalid_bid): col 21 should NOT have hyperlink and value "-"
        cell_invalid = ws.cell(row=3, column=21)
        self.assertEqual(cell_invalid.value, "-")
        self.assertIsNone(cell_invalid.hyperlink)

        # Row 4 (empty_url_bid): col 21 should NOT have hyperlink and value "-"
        cell_empty = ws.cell(row=4, column=21)
        self.assertEqual(cell_empty.value, "-")
        self.assertIsNone(cell_empty.hyperlink)

    def test_generate_bid_review_markdown(self):
        md = generate_bid_review_markdown(self.sample_bid)
        self.assertIn("# [제안 검토 보고서]", md)
        self.assertIn("KB국민은행", md)
        self.assertIn("총 예산 15억원", md)
        self.assertIn("- [ ] 제안서 10부", md)

    def test_generate_bid_review_html(self):
        html_doc = generate_bid_review_html(self.sample_bid)
        self.assertIn("<!DOCTYPE html>", html_doc)
        self.assertIn("지능형 위협 대응 보안 고도화", html_doc)
        self.assertIn("85점", html_doc)
        self.assertIn("<input type='checkbox'>", html_doc)

    def test_none_field_handling(self):
        empty_bid = {
            "title": None,
            "org": None,
            "site_name": None,
            "reg_date": None,
            "deadline": None,
            "submit_type": None,
            "required_docs": None,
            "qualifications": None,
            "summary": None,
            "fit_score": None,
            "fit_reason": None,
            "starred": None,
            "status": None,
            "origin_url": None,
            "tags": None,
            "solution_tags": None,
            "work_tags": None,
            "evidence": None
        }
        xlsx = generate_excel_export([empty_bid])
        self.assertIsInstance(xlsx, bytes)

        md = generate_bid_review_markdown(empty_bid)
        self.assertIn("# [제안 검토 보고서]", md)

        html_doc = generate_bid_review_html(empty_bid)
        self.assertIn("<!DOCTYPE html>", html_doc)
        self.assertIn("링크 없음", html_doc)
        self.assertNotIn('<a href=""', html_doc)

    def test_markdown_escaping_pipe_and_newlines(self):
        tricky_bid = dict(self.sample_bid)
        tricky_bid["title"] = "[긴급] 차세대 인프라 구축 | 2차"
        tricky_bid["budget"] = "10억원\n(VAT 포함) | 변동가능"
        tricky_bid["evaluation"] = "기술 80% | 가격 20%"
        tricky_bid["origin_url"] = ""
        tricky_bid["evidence"] = '[{"field": "budget", "quote": "예산 10억 | 확정", "location": "제안요청서 1p | 2p"}]'

        md = generate_bid_review_markdown(tricky_bid)
        self.assertIn("차세대 인프라 구축 \\| 2차", md)
        self.assertIn("10억원 (VAT 포함) \\| 변동가능", md)
        self.assertIn("기술 80% \\| 가격 20%", md)
        self.assertIn("예산 10억 \\| 확정", md)
        self.assertIn("링크 없음", md)

        # 개요 테이블 각 행의 파이프 개수 검증 (각 행당 정확히 2개 컬럼 -> 3개의 파이프 기호)
        table_lines = [line.strip() for line in md.split("\n") if line.strip().startswith("| **")]
        for line in table_lines:
            # 이스케이프되지 않은 파이프 개수 확인
            unescaped_pipes = [m.start() for m in re.finditer(r'(?<!\\)\|', line)]
            self.assertEqual(len(unescaped_pipes), 3, f"테이블 열 파싱 깨짐: {line}")

    def test_html_origin_link_safety(self):
        # 1. 빈 URL 테스트
        no_url_bid = dict(self.sample_bid, origin_url="")
        html_out = generate_bid_review_html(no_url_bid)
        self.assertIn('<span style="color:#888;">링크 없음</span>', html_out)
        self.assertNotIn('<a href=""', html_out)

        # 2. XSS 시도 또는 특수문자 포함 URL 테스트
        unsafe_bid = dict(self.sample_bid, origin_url='https://example.com/bid?id=1&name="test"<script>')
        html_out = generate_bid_review_html(unsafe_bid)
        self.assertIn('&amp;', html_out)
        self.assertIn('&quot;', html_out)
        self.assertNotIn('<script>', html_out)
        self.assertNotIn('name="test"', html_out)


if __name__ == "__main__":
    unittest.main()
