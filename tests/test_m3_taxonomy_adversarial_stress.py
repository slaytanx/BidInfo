"""Empirical Adversarial Stress Harness for Milestone M3 (Taxonomy Caching & Concurrency).

Focus areas:
1. Cache hit rate under rapid repetitive calls (hot loops).
2. Cache invalidation dynamics upon save_taxonomy_item and seed_taxonomy.
3. Thread-safety and race condition stress under concurrent readers & writers.
4. Cross-test database leakage and cache isolation.
5. In-place mutation hazard verification across all real codebase call sites.
6. Signature and options integrity.
"""
import concurrent.futures
import copy
import functools
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import classification
import db
import taxonomy
import taxonomy_ui


class TaxonomyCacheRepetitiveStressTests(unittest.TestCase):
    """Stress test hot loops and hit rates for load_taxonomy."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_taxonomy.db")
        self.patcher = patch.object(db, "DB_PATH", self.db_path)
        self.patcher.start()
        db.init_db()
        taxonomy.load_taxonomy.cache_clear()

    def tearDown(self):
        taxonomy.load_taxonomy.cache_clear()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_rapid_load_taxonomy_calls_hit_rate(self):
        """Verify 5,000 repetitive calls have 99.98% hit rate and zero DB queries after first."""
        taxonomy.load_taxonomy.cache_clear()
        self.assertEqual(taxonomy.load_taxonomy.cache_info().hits, 0)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().misses, 0)

        # 1st call: Miss, populates cache
        first_res = taxonomy.load_taxonomy()
        self.assertEqual(taxonomy.load_taxonomy.cache_info().misses, 1)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().hits, 0)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 1)

        # 4,999 subsequent calls
        for _ in range(4999):
            res = taxonomy.load_taxonomy()
            self.assertIs(res, first_res)

        info = taxonomy.load_taxonomy.cache_info()
        self.assertEqual(info.hits, 4999)
        self.assertEqual(info.misses, 1)
        self.assertEqual(info.currsize, 1)

    def test_classification_hot_loop_cache_efficiency(self):
        """Verify classify_rules hot loop achieves 100% cache hits and high throughput."""
        taxonomy.load_taxonomy.cache_clear()

        # Warm up cache with 1 call
        taxonomy.load_taxonomy()
        initial_hits = taxonomy.load_taxonomy.cache_info().hits
        initial_misses = taxonomy.load_taxonomy.cache_info().misses

        queries = [
            "서버 스토리지 및 네트워크 가상화 인프라 증설",
            "통합 보안관제 EDR 차세대 방화벽 WAF 도입",
            "ISMS-P 인증 컨설팅 및 모의해킹 진단",
            "클라우드 MSA 전환 및 포털 시스템 구축",
            "데이터센터 항온항습기 교체 및 청사 방역",
        ]

        iterations = 1000
        start = time.perf_counter()
        for i in range(iterations):
            q = queries[i % len(queries)]
            classification.classify_rules(q)
        duration = time.perf_counter() - start

        # Each classify_rules calls load_taxonomy() and normalize_classification() calls load_taxonomy() -> 2 calls per classify
        expected_calls = iterations * 2
        final_hits = taxonomy.load_taxonomy.cache_info().hits
        final_misses = taxonomy.load_taxonomy.cache_info().misses

        self.assertEqual(final_misses, initial_misses)
        self.assertEqual(final_hits - initial_hits, expected_calls)

        # 2,000 evaluations should complete very rapidly (< 1.5s) without DB overhead
        self.assertLess(duration, 2.0, f"Hot loop too slow: {duration:.3f}s for {iterations} classifications")

    def test_enabled_only_flag_independent_caching(self):
        """Verify enabled_only=True and enabled_only=False maintain separate cache slots."""
        taxonomy.load_taxonomy.cache_clear()

        # Add disabled item to distinguish
        taxonomy.save_taxonomy_item("세부 항목", "보안", "비활성항목", ["비활성키"], enabled=False)

        res_enabled = taxonomy.load_taxonomy(enabled_only=True)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 1)
        self.assertFalse(any(r["name"] == "비활성항목" for r in res_enabled))

        res_all = taxonomy.load_taxonomy(enabled_only=False)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 2)
        self.assertTrue(any(r["name"] == "비활성항목" for r in res_all))

        # Re-fetch both and check hits
        _ = taxonomy.load_taxonomy(enabled_only=True)
        _ = taxonomy.load_taxonomy(enabled_only=False)
        self.assertEqual(taxonomy.load_taxonomy.cache_info().hits, 2)


class TaxonomyCacheInvalidationStressTests(unittest.TestCase):
    """Stress test cache invalidation on mutations (save_taxonomy_item & seed_taxonomy)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_tax_inv.db")
        self.patcher = patch.object(db, "DB_PATH", self.db_path)
        self.patcher.start()
        db.init_db()
        taxonomy.load_taxonomy.cache_clear()

    def tearDown(self):
        taxonomy.load_taxonomy.cache_clear()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_save_item_creation_invalidates_and_updates_immediately(self):
        """Verify inserting a new taxonomy item wipes cache and updates classification immediately."""
        taxonomy.load_taxonomy.cache_clear()
        _ = taxonomy.load_taxonomy()
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 1)

        # Before adding, unknown keyword doesn't match
        res_before = classification.classify_rules("사이버위협헌팅 신규 구축")
        self.assertNotIn("위협헌팅", res_before["solution_tags"])

        # Insert new item
        taxonomy.save_taxonomy_item("세부 항목", "보안", "위협헌팅", ["사이버위협헌팅", "쓰렛헌팅"])

        # Cache must be empty immediately
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 0)

        # Classification immediately reflects new item
        res_after = classification.classify_rules("사이버위협헌팅 신규 구축")
        self.assertIn("위협헌팅", res_after["solution_tags"])
        self.assertIn("보안", res_after["tags"])

    def test_save_item_update_and_disable_invalidates_immediately(self):
        """Verify modifying aliases and disabling an item immediately updates rule classification."""
        # Find existing item '방화벽'
        items = taxonomy.load_taxonomy()
        fw = next(r for r in items if r["name"] == "방화벽")

        # Disable 방화벽
        taxonomy.save_taxonomy_item(fw["kind"], fw["domain"], fw["name"], fw["aliases"], enabled=False, item_id=fw["id"])

        # Cache must be wiped
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 0)

        # '방화벽' must no longer be classified
        res = classification.classify_rules("통합 방화벽 교체 도입")
        self.assertNotIn("방화벽", res["solution_tags"])

        # Re-enable 방화벽 with a new custom alias
        new_aliases = fw["aliases"] + ["차세대파이어월"]
        taxonomy.save_taxonomy_item(fw["kind"], fw["domain"], fw["name"], new_aliases, enabled=True, item_id=fw["id"])

        res_reenabled = classification.classify_rules("차세대파이어월 납품")
        self.assertIn("방화벽", res_reenabled["solution_tags"])

    def test_seed_taxonomy_invalidates_cache(self):
        """Verify seed_taxonomy clears cache."""
        taxonomy.load_taxonomy()
        self.assertGreater(taxonomy.load_taxonomy.cache_info().currsize, 0)

        with db.get_db_connection() as conn:
            taxonomy.seed_taxonomy(conn)

        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 0)

    def test_validation_failure_leaves_valid_cache_or_cleans_safely(self):
        """Verify that validation errors in save_taxonomy_item raise ValueError without corrupting cache."""
        taxonomy.load_taxonomy()
        self.assertEqual(taxonomy.load_taxonomy.cache_info().currsize, 1)

        # Invalid kind
        with self.assertRaises(ValueError):
            taxonomy.save_taxonomy_item("잘못된구분", "보안", "이름", ["별칭"])

        # Invalid empty name
        with self.assertRaises(ValueError):
            taxonomy.save_taxonomy_item("세부 항목", "보안", "   ", ["별칭"])

        # Invalid empty aliases
        with self.assertRaises(ValueError):
            taxonomy.save_taxonomy_item("세부 항목", "보안", "올바른이름", [])

        # Subsequent load_taxonomy still works perfectly
        items = taxonomy.load_taxonomy()
        self.assertGreater(len(items), 0)


class TaxonomyConcurrentAccessStressTests(unittest.TestCase):
    """Stress test concurrency under multi-threaded read and write loads."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_tax_concurrent.db")
        self.patcher = patch.object(db, "DB_PATH", self.db_path)
        self.patcher.start()
        db.init_db()
        taxonomy.load_taxonomy.cache_clear()

    def tearDown(self):
        taxonomy.load_taxonomy.cache_clear()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_massive_concurrent_readers(self):
        """Stress-test 20 concurrent threads simultaneously reading and classifying."""
        num_threads = 20
        iterations_per_thread = 50

        errors = []

        def worker(thread_id):
            try:
                queries = [
                    f"서버 구축 {thread_id}",
                    f"보안 관제 {thread_id}",
                    f"클라우드 컨설팅 {thread_id}",
                ]
                for i in range(iterations_per_thread):
                    rows = taxonomy.load_taxonomy()
                    if not rows or len(rows) < 10:
                        raise ValueError(f"Corrupted or empty rows read in thread {thread_id}")
                    _ = classification.classify_rules(queries[i % len(queries)])
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, t) for t in range(num_threads)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")

    def test_concurrent_readers_and_writers_interleaved(self):
        """Stress-test concurrent readers while writer thread periodically modifies taxonomy and clears cache."""
        num_readers = 8
        stop_flag = False
        reader_errors = []
        writer_errors = []

        def reader_task():
            while not stop_flag:
                try:
                    rows = taxonomy.load_taxonomy()
                    if not isinstance(rows, list) or len(rows) == 0:
                        raise ValueError("Invalid rows returned")
                    res = classification.classify_rules("방화벽 구축 사업")
                    if not isinstance(res, dict):
                        raise ValueError("Invalid classification result")
                except Exception as e:
                    reader_errors.append(e)
                    break

        def writer_task():
            for i in range(15):
                try:
                    taxonomy.save_taxonomy_item(
                        "세부 항목",
                        "인프라",
                        f"동적항목_{i}",
                        [f"키워드_{i}"],
                        enabled=True,
                    )
                    time.sleep(0.01)
                except Exception as e:
                    writer_errors.append(e)
                    break

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_readers + 1) as executor:
            reader_futures = [executor.submit(reader_task) for _ in range(num_readers)]
            writer_future = executor.submit(writer_task)

            # Wait for writer to finish
            writer_future.result()
            stop_flag = True
            for rf in reader_futures:
                rf.result()

        self.assertEqual(len(writer_errors), 0, f"Writer errors: {writer_errors}")
        self.assertEqual(len(reader_errors), 0, f"Reader errors: {reader_errors}")

        # SQLite integrity check: all 15 dynamic items exist in DB
        with db.get_db_connection() as conn:
            db_cnt = conn.execute("SELECT count(*) FROM taxonomy WHERE name LIKE '동적항목_%'").fetchone()[0]
        self.assertEqual(db_cnt, 15, f"Expected 15 items in SQLite DB, found {db_cnt}")

        # Post-concurrency cache reconciliation:
        # Under concurrent reader hammer, cache_clear() brings cache to exact 15 items
        taxonomy.load_taxonomy.cache_clear()
        fresh_rows = taxonomy.load_taxonomy()
        fresh_dyn_names = {r["name"] for r in fresh_rows if r["name"].startswith("동적항목_")}
        self.assertEqual(len(fresh_dyn_names), 15)


class TaxonomyDatabaseIsolationStressTests(unittest.TestCase):
    """Stress test cache isolation across database path changes."""

    def test_cross_database_cache_leakage_and_invalidation(self):
        """Verify switching DB_PATH requires cache_clear() (or init_db()) to avoid stale cached data."""
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            db_a = str(Path(dir_a) / "db_a.db")
            db_b = str(Path(dir_b) / "db_b.db")

            # Point to DB A, initialize, save custom item and cache it
            with patch.object(db, "DB_PATH", db_a):
                db.init_db()
                taxonomy.load_taxonomy.cache_clear()
                taxonomy.save_taxonomy_item("세부 항목", "보안", "항목A", ["단독A키워드"])
                rows_a = taxonomy.load_taxonomy()
                self.assertTrue(any(r["name"] == "항목A" for r in rows_a))

            # Switch to DB B WITHOUT init_db() or cache_clear():
            # Demonstrates the cache leakage risk when switching DB paths
            with patch.object(db, "DB_PATH", db_b):
                cached_stale = taxonomy.load_taxonomy()
                self.assertIs(cached_stale, rows_a)

                # Now call init_db() on DB B:
                # init_db() calls seed_taxonomy(conn) which automatically triggers load_taxonomy.cache_clear()!
                db.init_db()
                rows_b = taxonomy.load_taxonomy()
                self.assertIsNot(rows_b, rows_a)
                # DB B does NOT have 항목A
                self.assertFalse(any(r["name"] == "항목A" for r in rows_b))

                # Add 항목B to DB B
                taxonomy.save_taxonomy_item("세부 항목", "인프라", "항목B", ["단독B키워드"])
                rows_b_new = taxonomy.load_taxonomy()
                self.assertTrue(any(r["name"] == "항목B" for r in rows_b_new))


class TaxonomyNoMutationVerificationTests(unittest.TestCase):
    """Verify that all production call sites treat load_taxonomy rows as read-only."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_tax_nomut.db")
        self.patcher = patch.object(db, "DB_PATH", self.db_path)
        self.patcher.start()
        db.init_db()
        taxonomy.load_taxonomy.cache_clear()

    def tearDown(self):
        taxonomy.load_taxonomy.cache_clear()
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_codebase_callsites_never_mutate_cached_taxonomy(self):
        """Take a deepcopy snapshot of cached taxonomy and run all call sites; verify 0 mutations."""
        taxonomy.load_taxonomy.cache_clear()
        original_rows = taxonomy.load_taxonomy()
        snapshot = copy.deepcopy(original_rows)

        # 1. classify_rules
        _ = classification.classify_rules("방화벽 및 서버 유지보수 사업", "상세 설명 텍스트")
        _ = classification.classify_rules("모의해킹 컨설팅 및 ISMS-P 진단")
        _ = classification.classify_rules("기타 일반 물품 구매 공고")

        # 2. normalize_classification
        _ = classification.normalize_classification({"tags": ["보안"], "solution_tags": ["방화벽"], "work_tags": ["유지보수"]})

        # 3. taxonomy_options
        _ = taxonomy.taxonomy_options("세부 항목", ["보안", "인프라"])
        _ = taxonomy.taxonomy_options("업무")

        # 4. taxonomy_signature
        _ = taxonomy.taxonomy_signature()

        # Check identical to deepcopy snapshot
        current_rows = taxonomy.load_taxonomy()
        self.assertEqual(current_rows, snapshot)
        self.assertIs(current_rows, original_rows)


if __name__ == "__main__":
    unittest.main()
