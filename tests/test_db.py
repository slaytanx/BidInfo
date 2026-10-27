import os
import shutil
import tempfile
import unittest

import db


class TestDB(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db_file = os.path.join(self.test_dir, "test_bids.db")
        self.orig_db_path = db.DB_PATH
        db.DB_PATH = self.test_db_file

    def tearDown(self):
        db.DB_PATH = self.orig_db_path
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_db_context_manager_and_crud(self):
        # DB 초기화
        db.init_db()
        self.assertTrue(os.path.exists(self.test_db_file))
        
        # 공고 저장 테스트
        sample_bid = {
            "게시물ID": "TEST_001",
            "수집사이트": "테스트 사이트",
            "번호": "1",
            "계열사/조직": "NH농협",
            "제목": "테스트 입찰 공고",
            "등록일": "2026-07-31",
            "사업요약": "테스트 사업 요약입니다.",
            "적합도점수": 90,
            "적합사유": "1차 키워드 일치",
            "폴더경로": "/tmp/test_folder"
        }
        
        db.save_bid(sample_bid)
        self.assertTrue(db.is_bid_exists("TEST_001"))
        
        bids = db.load_all_bids()
        self.assertEqual(len(bids), 1)
        self.assertEqual(bids[0]["title"], "테스트 입찰 공고")
        self.assertEqual(bids[0]["fit_score"], 90)
        
        # 상태 변경 테스트
        db.update_bid_status("TEST_001", "제안작성중", 1)
        updated_bids = db.load_all_bids()
        self.assertEqual(updated_bids[0]["status"], "제안작성중")
        self.assertEqual(updated_bids[0]["starred"], 1)
        
        # 삭제 테스트
        db.delete_bids(["TEST_001"])
        self.assertFalse(db.is_bid_exists("TEST_001"))

if __name__ == "__main__":
    unittest.main()
