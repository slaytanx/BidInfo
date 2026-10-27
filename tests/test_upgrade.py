import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import zipfile

import classification
import crawler
import db
import hwp_parser as parser
import llm_manager as llm
import ollama_summary as analysis


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = patch.object(parser, 'CACHE_DIR', self.root/'cache');self.cache.start()

    def tearDown(self):
        self.cache.stop();self.tmp.cleanup()

    def test_hwp_records_controls_extended_and_odd_tail(self):
        body = '서버'.encode('utf-16-le') + struct.pack('<H', 2) + bytes(14) + ' 유지보수'.encode('utf-16-le') + b'\x00'
        stream = struct.pack('<I',67 | (4095 << 20)) + struct.pack('<I',len(body)) + body
        self.assertEqual(parser.parse_hwp_records(stream), '서버 유지보수')
        with self.assertRaises(ValueError):
            parser.parse_hwp_records(stream[:-3])

    def test_hwp_compression_flag(self):
        text = '입찰 문단'.encode('utf-16-le')
        record = struct.pack('<I',67 | len(text)<<20)+text
        header = bytearray(40)
        ole = Mock();ole.__enter__ = Mock(return_value=ole);ole.__exit__ = Mock(return_value=False)
        ole.listdir.return_value = [['BodyText','Section0']]
        ole.openstream.side_effect = lambda name: io.BytesIO(header if name=='FileHeader' else record)
        with patch.object(parser.olefile,'OleFileIO',return_value=ole):
            segments,errors,_ = parser._hwp_segments('synthetic.hwp')
        self.assertEqual(segments[0]['text'],'입찰 문단');self.assertFalse(errors)

    def test_xml_format_detection_and_order(self):
        p=self.root/'wrong.file'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('Contents/section10.xml','<s><p><t>후반 자격요건</t></p></s>')
            z.writestr('Contents/section2.xml','<s><p><t>앞부분</t></p></s>')
        result=parser.extract_document(p)
        self.assertEqual(result['status'],'성공')
        self.assertLess(result['text'].index('앞부분'),result['text'].index('후반'))

    def test_cache_and_changed_content(self):
        p=self.root/'sample.txt';p.write_text('서버 구축 입찰 안내')
        first=parser.extract_document(p);second=parser.extract_document(p)
        self.assertFalse(first['cache_hit']);self.assertTrue(second['cache_hit'])
        p.write_text('서버 운영 입찰 변경 공고')
        third=parser.extract_document(p)
        self.assertFalse(third['cache_hit']);self.assertIn('변경',third['text'])

    def test_zip_path_and_symlink_rejected(self):
        p=self.root/'bad.zip'
        with zipfile.ZipFile(p,'w') as z:z.writestr('../outside.txt','bad')
        with self.assertRaises(ValueError):parser.extract_zip(p,self.root/'out')
        self.assertFalse((self.root/'outside.txt').exists())

    def test_changed_archive_replaces_attachment(self):
        path = self.root/'docs.zip'
        for text in ('기존 자격요건', '변경된 자격요건'):
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('rfp.txt', text)
            parser.extract_zip(path, self.root/'out')
        self.assertEqual((self.root/'out/rfp.txt').read_text(), '변경된 자격요건')

    def test_failed_document_does_not_read_other_folder(self):
        other=self.root/'비슷한제목';other.mkdir();(other/'file.txt').write_text('다른 공고의 데이터')
        result=parser.process_folder_result(self.root/'없는폴더')
        self.assertEqual(result['text'],'');self.assertEqual(result['status'],'실패')

    @unittest.skipUnless(shutil.which('tesseract'),'OCR 도구 없음')
    def test_scan_and_mixed_pdf(self):
        import pymupdf as fitz
        text='RFP Deadline 2026-11-20 15:00. Required documents: proposal and registration certificate.'
        source=fitz.open();page=source.new_page();page.insert_text((50,70),text,fontsize=9)
        image=page.get_pixmap(matrix=fitz.Matrix(3,3)).tobytes('png')
        scan=fitz.open();scan.new_page().insert_image(fitz.Rect(0,0,595,842),stream=image)
        scan.new_page().insert_text((50,70),'Electronic submission. Technical evaluation eighty points.',fontsize=10)
        p=self.root/'mixed.pdf';scan.save(p);source.close();scan.close()
        result=parser.extract_document(p,use_cache=False)
        self.assertIn('2026',result['text']);self.assertIn('Electronic',result['text'])
        self.assertEqual(result['segments'][0]['method'],'OCR')
        self.assertEqual(result['segments'][1]['method'],'PDF 텍스트')


class ClassificationCrawlerTests(unittest.TestCase):
    def test_multiple_tags_and_unknown(self):
        result=classification.classify_rules('서버 유지보수')
        self.assertIn('인프라',result['tags']);self.assertIn('서버',result['solution_tags']);self.assertIn('유지보수',result['work_tags'])
        self.assertTrue(classification.should_collect(classification.classify_rules('업무 개선 사업'),['보안']))
        self.assertFalse(classification.should_collect(result,[]))

    def test_unrelated_does_not_fetch_or_call_model(self):
        item={'제목':'영업점 인테리어 공사','원본URL':'https://example.com/detail'}
        with patch.object(crawler,'_request') as request, patch.object(crawler,'classify_local') as model:
            self.assertIsNone(crawler._collect_item(item,'unused',['서버'],True,False))
        request.assert_not_called();model.assert_not_called()

    def test_date_range_and_real_nhit_link(self):
        from datetime import date
        markup='<tr><td>182</td><td><a href="/bbs/nonghyupit/675/100/artclView.do">서버 유지보수</a></td><td>대기</td><td>2026.10.01</td></tr>'
        with patch.object(crawler,'_request',return_value=Mock(text=markup)):
            result=crawler.list_bids('https://www.nonghyupit.com/nonghyupit/4485/subview.do',date(2026,10,1),date(2026,10,2),site_name='농협정보시스템')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['게시물ID'],'100')
        self.assertIn('/675/100/artclView.do',result[0]['원본URL'])

    def test_duplicate_skipped_before_detail(self):
        item={'제목':'서버','게시물ID':'1'}
        with patch.object(crawler,'list_bids',return_value=[item]),patch.object(crawler,'existing_bid_ids',return_value={'1'}),patch.object(crawler,'_collect_item') as detail:
            result=crawler.crawl_and_download_bids_by_date('https://example.com','unused',None,None,selected_categories=['서버'])
        self.assertEqual(result,[]);detail.assert_not_called()

    def test_nh_and_kb_attachments(self):
        nh="<a onclick=\"fn_egov_downFile('id', '2')\">첨부</a>"
        self.assertEqual(len(crawler._attachments(nh,'https://www.nonghyup.com/detail')),1)
        kb='<form name="frmDownload" action="/download"><input value="rfp.hwp" name="_FILE_NAME"></form>'
        files=crawler._attachments(kb,'https://omoney.kbstar.com/detail')
        self.assertEqual(files[0][2]['_FILE_NAME'],'rfp.hwp')


class BudgetAnalysisTests(unittest.TestCase):
    def test_parallel_reservation_and_unknown_charge(self):
        budget=llm.RunBudget(1);accepted=[]
        def reserve():
            try:budget.reserve(Decimal('.6'));accepted.append(True)
            except llm.BudgetExceeded:accepted.append(False)
        threads=[threading.Thread(target=reserve) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(accepted),1)
        budget.settle(Decimal('.6'))
        self.assertEqual(budget.spent,Decimal('.6'))

    def test_empty_and_bad_json(self):
        self.assertEqual(analysis.analyze_bid_with_ollama('서버','')['analysis_status'],'실패')
        with self.assertRaises(ValueError):analysis.validate_analysis({'summary':'불완전'})
        with self.assertRaises(Exception):llm.parse_json('잘못된 JSON')

    def test_tail_is_processed_and_cache_reused(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            result=dict(analysis.SCHEMA,summary='서버 구축',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[])
            call.return_value=json.dumps(result,ensure_ascii=False)
            text='앞부분\n'*3000+'후반 요구사항: 특수자격증'
            analysis.analyze_bid_with_ollama('서버',text)
            self.assertTrue(any('특수자격증' in c.args[0] for c in call.call_args_list))
            count=call.call_count
            analysis.analyze_bid_with_ollama('서버',text)
            self.assertEqual(call.call_count,count)

    def test_unquoted_conditions_are_unknown(self):
        result=dict(analysis.SCHEMA,deadline='2026-10-30',evidence=[{'field':'deadline','quote':'지어낸 인용','location':'p.1'}])
        self.assertEqual(analysis._ground(result,'원문')['evidence'],[])
        self.assertEqual(result['deadline'],'확인 불가')

    def test_budget_exhausted_uses_local(self):
        budget=llm.RunBudget(0)
        with patch.object(budget,'price',return_value=(Decimal('0.01'),Decimal('0.01'))),patch.object(llm,'get_secret',return_value='fixture'),patch.object(llm,'call_ollama',return_value='local'),patch.object(llm.requests,'post') as post:
            result=llm.generate_llm_response('prompt',llm.engine_configs(budget=budget))
        self.assertEqual(result,'local');post.assert_not_called()

    def test_question_answer_rejects_invented_quotes_and_handles_failure(self):
        with patch.object(analysis, 'generate_llm_response', return_value='{"answer":"허위 마감일","evidence":[{"quote":"없는 원문","location":"p.1"}]}'):
            answer = analysis.answer_bid_question('사업', '실제 문서 본문', '마감일?')
        self.assertNotIn('허위 마감일', answer)
        with patch.object(analysis, 'generate_llm_response', side_effect=RuntimeError('synthetic failure')):
            answer = analysis.answer_bid_question('사업', '실제 문서 본문', '마감일?')
        self.assertIn('처리 실패', answer)


class DBUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_PATH;db.DB_PATH=str(Path(self.tmp.name)/'test.db')
    def tearDown(self):
        db.DB_PATH=self.old;self.tmp.cleanup()
    def test_cross_source_id_and_status_preservation(self):
        for site in ['KB국민은행','NH농협 입찰공고']:
            db.save_bid({'게시물ID':'123','수집사이트':site,'제목':'서버','starred':1,'status':'제출완료'})
        self.assertEqual(len(db.load_all_bids()),2)
        with self.assertRaises(ValueError):db.update_bid_status('123','포기',0)
        db.save_bid({'게시물ID':'123','수집사이트':'KB국민은행','제목':'서버 변경','starred':0,'status':'검토중'})
        row=next(r for r in db.load_all_bids() if r['site_name']=='KB국민은행')
        self.assertEqual(row['starred'],1);self.assertEqual(row['status'],'제출완료')
    def test_failed_retry_preserves_good_summary(self):
        db.save_bid({'게시물ID':'1','수집사이트':'KB국민은행','사업요약':'정상 요약','analysis_status':'성공'})
        db.save_bid({'게시물ID':'1','수집사이트':'KB국민은행','사업요약':'실패','analysis_status':'실패'})
        self.assertEqual(db.load_all_bids()[0]['summary'],'정상 요약')
    def test_empty_updates_no_syntax_error(self):
        db.save_bid({'게시물ID':'99','수집사이트':'KB국민은행','starred':1,'status':'제출완료'})
        db.save_bid({'게시물ID':'99','수집사이트':'KB국민은행'})
        self.assertEqual(len(db.load_all_bids()),1)


class PageTests(unittest.TestCase):
    def test_category_filter_and_detail_with_isolated_bids(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, 'DB_PATH', str(Path(tmp)/'fixture.db')), patch.object(llm, 'get_secret', return_value=''):
            db.save_bid({'게시물ID':'1','수집사이트':'KB국민은행','제목':'서버 유지보수','적합도점수':80,'tags':['인프라'],'solution_tags':['서버'],'work_tags':['유지보수'],'starred':1,'status':'제출완료'})
            db.save_bid({'게시물ID':'1','수집사이트':'NH농협 입찰공고','제목':'보안 운영','적합도점수':60,'tags':['보안'],'work_tags':['운영']})
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=20)
            self.assertFalse(app.exception)
            next(w for w in app.multiselect if w.label == '표시 분야 (하나라도 일치)').set_value(['인프라'])
            app.run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 1)
            self.assertEqual(app.dataframe[0].value.iloc[0]['제목'], '서버 유지보수')
            next(b for b in app.button if b.label == '🔍 팝업 상세보기').click()
            app.run(timeout=20)
            self.assertFalse(app.exception)

    def test_cloud_retry_is_budgeted(self):
        budget = llm.RunBudget(1)
        busy = Mock(status_code=429, headers={'Retry-After':'0'})
        okay = Mock(status_code=200)
        okay.json.return_value = {'usage':{'cost':.001}, 'choices':[{'message':{'content':'{"ok":true}'}}]}
        with patch.object(budget, 'price', return_value=(Decimal('.000001'),Decimal('.000001'))), patch.object(llm.requests, 'post', side_effect=[busy,okay]), patch.object(llm.time,'sleep'):
            llm.call_openrouter('시험', 'synthetic-key', budget=budget, max_tokens=100)
        self.assertEqual(budget.calls,2)
        self.assertGreater(budget.spent,Decimal('.001'))


if __name__ == '__main__':unittest.main()
