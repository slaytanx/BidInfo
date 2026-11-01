import unittest
from unittest.mock import patch
import ollama_summary as analysis


class QuoteExtractionShortQuotesStressTests(unittest.TestCase):
    """Empirically stress-test short quote extraction in _extract_facts."""

    def setUp(self):
        self.sample_text = (
            "[공고 제2026-001호]\n"
            "1. 사업명: 차세대 금융 AI 데이터 분석 플랫폼 구축\n"
            "2. 사업기간: 계약체결일로부터 6개월 (단, 하자보수는 1년)\n"
            "3. 사업예산: 총 1억원 (100,000,000원, 부가세 포함)\n"
            "4. 참가자격: 소프트웨어사업자 등록업체, 최근 3년간 30% 이상 실적 보유\n"
            "5. 납품기한: 발주일로부터 1일 이내 배포 완료\n"
            "6. 제출방식: 나라장터 전자제출\n"
            "7. 제안서 마감: 2026.11.30 17:00까지\n"
        )

    def test_short_quotes_exact_match_diverse(self):
        """Verify diverse 1~3 character short quotes ('6개월', '1억원', '30%', 'AI', '1일', '1년', '전자', '원')

        are correctly accepted and marked verified=True.
        """
        cases = [
            ("duration", "계약기간 6개월", "6개월"),
            ("budget", "사업예산 1억원", "1억원"),
            ("qualifications", "지분율 30%", "30%"),
            ("qualifications", "AI 플랫폼 구축", "AI"),
            ("duration", "납품기한 1일", "1일"),
            ("duration", "하자보수 1년", "1년"),
            ("submit_type", "전자제출", "전자"),
            ("budget", "예산 100,000,000원", "원"),
        ]

        for field, fact, quote in cases:
            with self.subTest(field=field, quote=quote):
                raw = [{"field": field, "fact": fact, "quote": quote}]
                with patch.object(analysis, "_cached_facts", return_value=raw):
                    facts = analysis._extract_facts(self.sample_text, [field], {}, [])
                self.assertEqual(len(facts), 1)
                self.assertTrue(facts[0]["verified"], f"Quote '{quote}' should be verified")
                self.assertEqual(facts[0]["quote"], quote)
                self.assertIn("원문 문자", facts[0]["location"])

    def test_short_quotes_with_korean_particles(self):
        """Test short quotes when attached to Korean postpositions/particles in the document."""
        text = "사업기간은 6개월간이며, 예산은 1억원으로 확정되었고, 최소 30%이상의 참여율과 AI기반 기술이 필요함."
        cases = [
            ("duration", "사업기간 6개월", "6개월"),
            ("budget", "사업예산 1억원", "1억원"),
            ("qualifications", "참여율 30%", "30%"),
            ("qualifications", "AI 기반 기술", "AI"),
        ]
        for field, fact, quote in cases:
            with self.subTest(field=field, quote=quote):
                raw = [{"field": field, "fact": fact, "quote": quote}]
                with patch.object(analysis, "_cached_facts", return_value=raw):
                    facts = analysis._extract_facts(text, [field], {}, [])
                self.assertEqual(len(facts), 1)
                self.assertTrue(facts[0]["verified"])
                self.assertEqual(facts[0]["quote"], quote)


class QuoteExtractionSeenPoisoningStressTests(unittest.TestCase):
    """Stress-test adversarial attempts to poison the 'seen' deduplication set."""

    def setUp(self):
        self.text = "사업기간은 계약일로부터 6개월입니다. 총 사업예산은 1억원입니다."

    def test_invalid_quote_first_does_not_poison_seen_set(self):
        """Verify that an initial invalid/hallucinated quote is rejected without poisoning

        the 'seen' set, allowing a subsequent valid quote for the same fact to be accepted.
        """
        raw_facts = [
            {"field": "duration", "fact": "사업기간 6개월", "quote": "999개월(거짓)"},
            {"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"},
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(self.text, ["duration"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["verified"])
        self.assertEqual(facts[0]["quote"], "6개월")

    def test_multiple_invalid_quotes_before_valid_quote(self):
        """Stress-test multiple consecutive invalid quotes before a valid quote."""
        raw_facts = [
            {"field": "duration", "fact": "사업기간 6개월", "quote": f"가짜_{i}"}
            for i in range(10)
        ] + [
            {"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"}
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(self.text, ["duration"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["verified"])
        self.assertEqual(facts[0]["quote"], "6개월")

    def test_duplicate_valid_quotes_deduplicated_cleanly(self):
        """Verify that identical valid facts are deduplicated to exactly 1 result."""
        raw_facts = [
            {"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"},
            {"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"},
            {"field": "duration", "fact": "사업기간 6개월", "quote": "계약일로부터 6개월"},
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(self.text, ["duration"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["verified"])
        self.assertEqual(facts[0]["quote"], "6개월")

    def test_valid_quote_first_blocks_subsequent_invalid_quote(self):
        """Verify that once a valid fact is accepted, a subsequent duplicate with an invalid quote

        is ignored (does not override or corrupt the accepted fact).
        """
        raw_facts = [
            {"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"},
            {"field": "duration", "fact": "사업기간 6개월", "quote": "거짓기간"},
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(self.text, ["duration"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["verified"])
        self.assertEqual(facts[0]["quote"], "6개월")

    def test_empty_quote_accepted_as_unverified_and_deduplicated(self):
        """Verify that an item with no quote is accepted as verified=False,

        and subsequent duplicates of that unverified fact are deduplicated.
        """
        raw_facts = [
            {"field": "duration", "fact": "사업기간 6개월", "quote": ""},
            {"field": "duration", "fact": "사업기간 6개월", "quote": ""},
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(self.text, ["duration"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertFalse(facts[0]["verified"])
        self.assertEqual(facts[0]["quote"], "")


class QuoteExtractionPassageAndEdgeCasesStressTests(unittest.TestCase):
    """Stress-test passages selection, boundary conditions, and type edge cases."""

    def test_long_document_passage_budget_fallback_location(self):
        """When text exceeds PASSAGE_BUDGET (6000 chars), verify that:

        1. A quote located within picked passages receives an exact block location.
        2. A quote outside picked passages falls back gracefully to '원문 문자 1-{len(text)}'.
        """
        # Create a document of ~10,000 characters
        # Put target keywords and target quote in block 1
        block1 = "사업기간 예산 개월 억원\n" + "계약기간은 6개월입니다.\n" + ("가나다라 " * 150) + "\n"
        # Middle filler without keywords (~7000 chars)
        filler = ("일반적인 설명 문장입니다. 아무런 관련 키워드가 없습니다. " * 30) + "\n"
        # Final block with a quote but no strong passage keywords
        final_block = "추가 안내사항: 납품일정은 1일 이내 완료하여야 합니다.\n" + ("기타 내용 " * 100)
        long_text = block1 + (filler * 5) + final_block

        self.assertGreater(len(long_text), 6000)

        # 1. Quote in picked block (block1 has '개월' keyword)
        raw_in_passage = [{"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"}]
        with patch.object(analysis, "_cached_facts", return_value=raw_in_passage):
            facts1 = analysis._extract_facts(long_text, ["duration"], {"개월": 3}, [])
        self.assertEqual(len(facts1), 1)
        self.assertTrue(facts1[0]["verified"])
        self.assertIn("원문 문자", facts1[0]["location"])
        # Should NOT be the entire text fallback
        self.assertNotEqual(facts1[0]["location"], f"원문 문자 1-{len(long_text)}")

        # 2. Quote in unpicked block (no matching keywords given)
        raw_outside_passage = [{"field": "duration", "fact": "납품기한 1일", "quote": "1일"}]
        with patch.object(analysis, "_cached_facts", return_value=raw_outside_passage):
            facts2 = analysis._extract_facts(long_text, ["duration"], {"전혀다른단어": 10}, [])
        self.assertEqual(len(facts2), 1)
        self.assertTrue(facts2[0]["verified"])
        # Gracefully falls back to entire text range without crashing
        self.assertEqual(facts2[0]["location"], f"원문 문자 1-{len(long_text)}")

    def test_fuzzy_matching_for_longer_quotes_with_formatting(self):
        """Quotes of len >= 4 should match fuzzy-flattened text across markdown/newlines."""
        text = "## 사업 기간\n| 구분 | 내용 |\n|---|---|\n| 계약기간 | 2026년 12월 31일까지 |\n"
        raw_facts = [
            {"field": "duration", "fact": "계약기간 2026년 12월 31일", "quote": "계약기간 2026년 12월 31일까지"}
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(text, ["duration"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["verified"])

    def test_non_string_quote_types_graceful_handling(self):
        """Non-string quotes (None, int, list, dict) should be treated as empty quotes without throwing exceptions."""
        text = "사업기간은 6개월이다."
        raw_facts = [
            {"field": "duration", "fact": "사업기간 6개월 (None)", "quote": None},
            {"field": "duration", "fact": "사업기간 6개월 (int)", "quote": 6},
            {"field": "duration", "fact": "사업기간 6개월 (list)", "quote": ["6개월"]},
            {"field": "duration", "fact": "사업기간 6개월 (dict)", "quote": {"quote": "6개월"}},
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(text, ["duration"], {}, [])
        # Only the first unique fact will be processed; the others have different fact strings
        self.assertEqual(len(facts), 4)
        for f in facts:
            self.assertFalse(f["verified"])
            self.assertEqual(f["quote"], "")

    def test_field_format_rejection(self):
        """FIELD_FORMAT regex must filter out malformed facts for budget, duration, deadline."""
        text = "사업기간 및 사업예산 1억원 관련 공고"
        raw_facts = [
            {"field": "budget", "fact": "협의에 따름", "quote": "협의에 따름"},  # No numbers/currency
            {"field": "duration", "fact": "추후 결정", "quote": "추후 결정"},  # No digits
            {"field": "deadline", "fact": "별도 통보", "quote": "별도 통보"},  # No digits
            {"field": "budget", "fact": "사업예산 1억원", "quote": "1억원"},  # Valid & in text
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(text, ["budget", "duration", "deadline"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["field"], "budget")
        self.assertEqual(facts[0]["fact"], "사업예산 1억원")

    def test_apply_facts_integration_with_short_quotes(self):
        """Test full _apply_facts integration with short quotes across all FIELD_GROUPS."""
        text = (
            "제출마감: 2026.11.30까지, 제출방식: 전자제출.\n"
            "필수서류: 사업자등록증 1부, 참가자격: 소프트웨어사업자 등록업체.\n"
            "사업기간: 6개월, 사업예산: 1억원, 평가방법: 기술평가 80점."
        )
        initial_result = {
            "deadline": "확인 불가",
            "submit_type": "확인 불가",
            "required_docs": "확인 불가",
            "qualifications": "확인 불가",
            "duration": "기존 기간",
            "budget": "기존 예산",
            "evaluation": "확인 불가",
            "evidence": []
        }
        # Provide facts covering all 3 FIELD_GROUPS so none are skipped
        all_facts = [
            {"field": "deadline", "fact": "제출마감 2026.11.30", "quote": "2026.11.30"},
            {"field": "submit_type", "fact": "전자제출", "quote": "전자제출"},
            {"field": "required_docs", "fact": "사업자등록증 1부", "quote": "사업자등록증"},
            {"field": "qualifications", "fact": "소프트웨어사업자 등록업체", "quote": "소프트웨어사업자"},
            {"field": "duration", "fact": "사업기간 6개월", "quote": "6개월"},
            {"field": "budget", "fact": "총 예산 1억원", "quote": "1억원"},
            {"field": "evaluation", "fact": "기술평가 80점", "quote": ""},  # unverified
        ]
        with patch.object(analysis, "_cached_facts", return_value=all_facts):
            updated, skipped = analysis._apply_facts(dict(initial_result), text, [{}])
        self.assertEqual(skipped, 0)
        self.assertEqual(updated["duration"], "사업기간 6개월")
        self.assertEqual(updated["budget"], "총 예산 1억원")
        self.assertTrue(updated["evaluation"].startswith(analysis.UNVERIFIED))
        self.assertGreaterEqual(len(updated["evidence"]), 2)
        quotes = [e["quote"] for e in updated["evidence"]]
        self.assertIn("6개월", quotes)
        self.assertIn("1억원", quotes)


if __name__ == "__main__":
    unittest.main()
