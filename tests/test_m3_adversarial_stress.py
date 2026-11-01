"""Milestone M3 Adversarial Stress & Edge-Case Test Suite.

Exhaustively verifies:
1. crawler._base_domain and host authorization boundary (subdomain matching, ccTLDs, IP, attack bypasses).
2. date_utils.extract_date_from_text and calculate_d_day (range extraction, leap years, multi-date texts, fallback).
3. taxonomy.load_taxonomy LRU caching, cache invalidation on save/seed, and cache separation.
"""
from datetime import date
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import crawler
from date_utils import calculate_d_day, extract_date_from_text
import db
import taxonomy
from classification import classify_rules, normalize_classification


class AdversarialCrawlerBaseDomainTests(unittest.TestCase):
    """Stress-test domain extraction and host authorization boundary in crawler."""

    def test_base_domain_subdomains_and_cctlds(self):
        # Standard 2-level ccTLD for Korea
        self.assertEqual(crawler._base_domain("nonghyupit.com"), "nonghyupit.com")
        self.assertEqual(crawler._base_domain("file.nonghyupit.com"), "nonghyupit.com")
        self.assertEqual(crawler._base_domain("deep.sub.file.nonghyupit.com"), "nonghyupit.com")
        self.assertEqual(crawler._base_domain("bank.co.kr"), "bank.co.kr")
        self.assertEqual(crawler._base_domain("ebid.bank.co.kr"), "bank.co.kr")
        self.assertEqual(crawler._base_domain("file.ebid.bank.co.kr"), "bank.co.kr")
        self.assertEqual(crawler._base_domain("portal.korea.go.kr"), "korea.go.kr")
        self.assertEqual(crawler._base_domain("data.gov.or.kr"), "gov.or.kr")
        self.assertEqual(crawler._base_domain("library.univ.ac.kr"), "univ.ac.kr")
        self.assertEqual(crawler._base_domain("nic.ne.kr"), "nic.ne.kr")
        self.assertEqual(crawler._base_domain("inst.re.kr"), "inst.re.kr")

    def test_base_domain_case_insensitivity(self):
        self.assertEqual(crawler._base_domain("FILE.NONGHYUPIT.COM"), "nonghyupit.com")
        self.assertEqual(crawler._base_domain("Ebid.Bank.CO.KR"), "bank.co.kr")

    def test_base_domain_adversarial_spoof_attempts(self):
        # Attacker registers suffix domain imitating target
        self.assertNotEqual(
            crawler._base_domain("nonghyupit.com.attacker.com"),
            crawler._base_domain("nonghyupit.com"),
        )
        # Attacker registers prefix dash
        self.assertNotEqual(
            crawler._base_domain("attacker-nonghyupit.com"),
            crawler._base_domain("nonghyupit.com"),
        )
        # Different TLD
        self.assertNotEqual(
            crawler._base_domain("nonghyupit.co.kr"),
            crawler._base_domain("nonghyupit.com"),
        )
        # Korean TLD spoofing
        self.assertNotEqual(
            crawler._base_domain("evilgo.kr"),
            crawler._base_domain("safe.go.kr"),
        )
        self.assertNotEqual(
            crawler._base_domain("go.kr"),
            crawler._base_domain("safe.go.kr"),
        )
        self.assertNotEqual(
            crawler._base_domain("co.kr"),
            crawler._base_domain("bank.co.kr"),
        )

    def test_base_domain_ip_addresses(self):
        self.assertEqual(crawler._base_domain("192.168.1.1"), "192.168.1.1")
        self.assertEqual(crawler._base_domain("10.0.0.1"), "10.0.0.1")
        self.assertNotEqual(
            crawler._base_domain("192.168.1.1"),
            crawler._base_domain("192.168.1.2"),
        )
        self.assertNotEqual(
            crawler._base_domain("192.168.1.1"),
            crawler._base_domain("10.0.0.1"),
        )

    def test_base_domain_empty_and_edge_inputs(self):
        self.assertEqual(crawler._base_domain(""), "")
        self.assertEqual(crawler._base_domain(None), "")
        self.assertEqual(crawler._base_domain("localhost"), "localhost")

    def test_collect_item_attachment_host_isolation_stress(self):
        origin = "https://www.nonghyupit.com/board/view?id=42"
        item = {
            '게시물ID': '42',
            '수집사이트': 'NH농협',
            '제목': '인프라 서버 구축 공고',
            '원본URL': origin,
            '번호': '42',
            '등록일': '2026-10-15',
            '계열사/조직': 'NH',
        }

        markup = """
        <html><body>
            <a href="https://file.nonghyupit.com/rfp1.zip">정상 서브도메인 첨부</a>
            <a href="https://attach.ebid.nonghyupit.com/rfp2.zip">정상 중첩서브도메인 첨부</a>
            <a href="https://www.nonghyupit.com/rfp3.zip">정상 동일호스트 첨부</a>
            <a href="https://nonghyupit.com.attacker.com/evil.zip">공격 도메인 차단대상</a>
            <a href="https://malicious.org/trojan.zip">외부 도메인 차단대상</a>
            <a href="javascript:void(0)">링크 아닌 항목</a>
        </body></html>
        """

        mock_resp = Mock(status_code=200, headers={}, content=b'PK\x05\x06' + b'\x00' * 18)
        mock_resp.iter_content.return_value = [b'PK\x05\x06' + b'\x00' * 18]

        requested = []

        def fake_request(session, method, url, **kwargs):
            if 'view' in url:
                return Mock(text=markup)
            requested.append(url)
            return mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(crawler, '_request', side_effect=fake_request), \
                 patch.object(crawler, 'handle_download_and_extract_zip'):
                res = crawler._collect_item(
                    item, tmpdir, ['인프라'], False, False, selected_solutions=['서버']
                )

        # 3 legitimate attachments should be requested
        self.assertIn("https://file.nonghyupit.com/rfp1.zip", requested)
        self.assertIn("https://attach.ebid.nonghyupit.com/rfp2.zip", requested)
        self.assertIn("https://www.nonghyupit.com/rfp3.zip", requested)

        # Malicious domains must NOT be requested
        self.assertNotIn("https://nonghyupit.com.attacker.com/evil.zip", requested)
        self.assertNotIn("https://malicious.org/trojan.zip", requested)

        # Verify failures report other site rejection
        self.assertEqual(res['download_status'], '부분 성공')
        self.assertTrue(any('다른 사이트 첨부: 원본 확인 필요' in err for err in res['download_errors']))


class AdversarialDateUtilsStressTests(unittest.TestCase):
    """Stress-test date parsing and D-Day calculation edge cases."""

    def test_extract_date_range_various_formats(self):
        # YYYY.MM.DD range
        self.assertEqual(
            extract_date_from_text("2026.10.01 ~ 2026.11.15"),
            date(2026, 11, 15),
        )
        # YYYY-MM-DD range with time
        self.assertEqual(
            extract_date_from_text("입찰기간: 2026-05-01 09:00 ~ 2026-05-31 18:00"),
            date(2026, 5, 31),
        )
        # YYYY/MM/DD range
        self.assertEqual(
            extract_date_from_text("2026/01/10 ~ 2026/02/20"),
            date(2026, 2, 20),
        )
        # Korean format range
        self.assertEqual(
            extract_date_from_text("2026년 03월 01일 ~ 2026년 04월 30일"),
            date(2026, 4, 30),
        )
        # Short format range
        self.assertEqual(
            extract_date_from_text("26.08.01 ~ 26.08.15"),
            date(2026, 8, 15),
        )

    def test_extract_date_leap_year_handling(self):
        # Leap year 2024-02-29 is valid
        self.assertEqual(extract_date_from_text("2024.02.29"), date(2024, 2, 29))
        # Non-leap year 2026-02-29 is invalid; if in a range, fallback to earlier valid date
        self.assertEqual(
            extract_date_from_text("2026.02.01 ~ 2026.02.29"),
            date(2026, 2, 1),
        )

    def test_extract_date_multiple_dates_in_text(self):
        text = "공고일자: 2026-09-01, 설명회: 2026-09-10, 제안서 제출마감: 2026-09-25"
        # Must extract the latest (last) date matching pattern
        self.assertEqual(extract_date_from_text(text), date(2026, 9, 25))

    def test_extract_date_empty_and_garbage(self):
        self.assertIsNone(extract_date_from_text(""))
        self.assertIsNone(extract_date_from_text(None))
        self.assertIsNone(extract_date_from_text("상세 참조 바람"))
        self.assertIsNone(extract_date_from_text("2026.99.99"))

    def test_calculate_d_day_boundary_conditions(self):
        today = date(2026, 10, 15)
        # Future date in range
        self.assertEqual(
            calculate_d_day("2026.10.01 ~ 2026.10.30", target_date=today),
            "⏳ D-15",
        )
        # Ending today
        self.assertEqual(
            calculate_d_day("2026.10.01 ~ 2026.10.15", target_date=today),
            "🔥 D-DAY",
        )
        # Ended yesterday
        self.assertEqual(
            calculate_d_day("2026.10.01 ~ 2026.10.14", target_date=today),
            "마감됨",
        )
        # MM월 DD일 range fallback
        self.assertEqual(
            calculate_d_day("10월 01일 ~ 10월 25일", target_date=today),
            "⏳ D-10",
        )
        self.assertEqual(
            calculate_d_day("10월 01일 ~ 10월 15일", target_date=today),
            "🔥 D-DAY",
        )
        self.assertEqual(
            calculate_d_day("10월 01일 ~ 10월 10일", target_date=today),
            "마감됨",
        )
        # Unparseable fallback
        self.assertEqual(calculate_d_day("마감 미정", target_date=today), "상세참조")
        self.assertEqual(calculate_d_day("", target_date=today), "상세참조")
        self.assertEqual(calculate_d_day(None, target_date=today), "상세참조")


class AdversarialTaxonomyCacheStressTests(unittest.TestCase):
    """Stress-test taxonomy LRU caching, invalidation, and data integrity."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DB_PATH', str(Path(self.temp.name) / 'fixture.db'))
        self.patch.start()
        db.init_db()
        taxonomy.load_taxonomy.cache_clear()

    def tearDown(self):
        taxonomy.load_taxonomy.cache_clear()
        self.patch.stop()
        self.temp.cleanup()

    def test_cache_hits_and_zero_db_queries_in_classification_hotloop(self):
        # Initial call populates cache
        taxonomy.load_taxonomy.cache_clear()
        t1 = taxonomy.load_taxonomy()
        hits_start = taxonomy.load_taxonomy.cache_info().hits

        # Run 50 classification checks (hot loop)
        for i in range(50):
            res = classify_rules(f"제목 {i} 클라우드 보안 관제 구축", "서버 및 방화벽 운영")
            self.assertIn("보안", res['tags'])

        # Hits must have increased significantly (at least 50 calls * 2 calls per rule)
        hits_end = taxonomy.load_taxonomy.cache_info().hits
        self.assertGreaterEqual(hits_end - hits_start, 100)

    def test_cache_separation_enabled_only_flag(self):
        taxonomy.load_taxonomy.cache_clear()
        all_items = taxonomy.load_taxonomy(enabled_only=False)
        enabled_items = taxonomy.load_taxonomy(enabled_only=True)

        info = taxonomy.load_taxonomy.cache_info()
        self.assertEqual(info.currsize, 2)  # Two separate entries for True and False

    def test_cache_cleared_on_save_item_and_seed(self):
        taxonomy.load_taxonomy.cache_clear()
        taxonomy.load_taxonomy()
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 1)

        # 1. Clear on insert
        taxonomy.save_taxonomy_item("세부 항목", "인프라", "신규스토리지", ["스토리지X"])
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 0)

        # Re-populate
        items = taxonomy.load_taxonomy()
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 1)
        self.assertTrue(any(item['name'] == '신규스토리지' for item in items))

        # 2. Clear on update
        new_item = next(item for item in items if item['name'] == '신규스토리지')
        taxonomy.save_taxonomy_item(
            new_item['kind'], new_item['domain'], new_item['name'],
            ["스토리지X", "스토리지Y"], True, new_item['id']
        )
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 0)

        # 3. Clear on seed_taxonomy
        with db.get_db_connection() as conn:
            taxonomy.seed_taxonomy(conn)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 0)


if __name__ == '__main__':
    unittest.main()
