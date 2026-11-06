import unittest
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


if __name__ == "__main__":
    unittest.main()
