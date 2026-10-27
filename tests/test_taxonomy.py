import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

import db
import classification
import taxonomy
import crawler
import pipeline
import llm_manager


class TaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(db,'DB_PATH',str(Path(self.temp.name)/'fixture.db'));self.patch.start()
        db.init_db()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_three_axes_and_synonyms(self):
        result=classification.classify_rules('서버 스토리지 및 디도스 침입방지시스템 통합 유지 보수')
        self.assertEqual(result['tags'],['인프라','보안'])
        self.assertTrue({'서버','스토리지','DDoS','IPS'} <= set(result['solution_tags']))
        self.assertEqual(result['work_tags'],['유지보수'])
        built=classification.classify_rules('서버 스토리지 구축')
        self.assertEqual(built['tags'],['인프라'])
        self.assertEqual(built['work_tags'],['구축'])
        consulting=classification.classify_rules('ISMS-P 인증 컨설팅 및 모의해킹')
        self.assertTrue({'보안','컨설팅'} <= set(consulting['tags']))

    def test_acronym_boundaries_and_web_firewall(self):
        result=classification.classify_rules('chips snacks payroll software')
        self.assertNotIn('IPS',result['solution_tags']);self.assertNotIn('NAC',result['solution_tags'])
        result=classification.classify_rules('웹 방화벽 WAF 유지보수')
        self.assertIn('WAF',result['solution_tags']);self.assertNotIn('방화벽',result['solution_tags'])
        result=classification.classify_rules('리눅스 운영체제 구매')
        self.assertNotIn('운영',result['work_tags']);self.assertIn('도입/구매',result['work_tags'])

    def test_filter_any_within_axis_and_all_across_axes(self):
        result=classification.classify_rules('IPS 방화벽 유지보수')
        self.assertTrue(classification.should_collect(result,['보안'],['DDoS','IPS'],['유지보수'],False))
        self.assertFalse(classification.should_collect(result,['보안'],['DDoS'],['유지보수'],False))
        self.assertFalse(classification.should_collect(result,['보안'],['IPS'],['구축'],False))
        unknown=classification.classify_rules('차세대 업무 개선 사업')
        self.assertFalse(classification.should_collect(unknown,['보안'],include_uncertain=False))
        self.assertTrue(classification.should_collect(unknown,['보안'],include_uncertain=True))

    def test_persistent_custom_keyword_and_disabled_seed(self):
        taxonomy.save_taxonomy_item('세부 항목','보안','보안 신제품',['ZZSEC','신규 탐지'])
        self.assertIn('보안 신제품',classification.classify_rules('ZZSEC 구축')['solution_tags'])
        row=next(r for r in taxonomy.load_taxonomy() if r['name']=='IPS')
        taxonomy.save_taxonomy_item(row['kind'],row['domain'],row['name'],row['aliases'],False,row['id'])
        db._READY.discard(db.DB_PATH);db.init_db()
        self.assertNotIn('IPS',classification.classify_rules('IPS 도입')['solution_tags'])

    def _bid(self,ident='1',status='판단 보류'):
        db.save_bid({'게시물ID':ident,'수집사이트':'KB국민은행','번호':'1','제목':'업무 개선 사업','원본URL':'https://omoney.kbstar.com/detail/'+ident,
                     'classification_status':status,'analysis_status':'미분석','download_status':'미수집','폴더경로':''})
        return next(r for r in db.load_all_bids() if r['source_id']==ident)

    def test_manual_override_survives_automatic_updates(self):
        row=self._bid()
        db.set_manual_classification(row['bid_id'],['보안'],['IPS'],['유지보수'])
        db.save_bid({'게시물ID':'1','수집사이트':'KB국민은행','tags':['개발'],'solution_tags':[], 'work_tags':[], 'classification_status':'자동'})
        db.update_enrichment(row['bid_id'],tags=['개발'],classification_reason='자동 결과')
        changed=db.load_all_bids()[0]
        self.assertEqual(json.loads(changed['tags']),['보안'])
        self.assertEqual(json.loads(changed['solution_tags']),['IPS'])
        self.assertEqual(changed['classification_status'],'수집 대기')

    def test_pending_does_not_download_or_analyze(self):
        item={'게시물ID':'1','수집사이트':'KB국민은행','제목':'미확정 사업','원본URL':'https://omoney.kbstar.com/detail','번호':'1','등록일':'2026-10-13','계열사/조직':'KB'}
        with patch.object(crawler,'_request',return_value=Mock(text='<p>범위는 별도 협의</p>')) as network:
            result=crawler._collect_item(item,self.temp.name,['보안'],False,False,selected_solutions=['IPS'])
        self.assertEqual(result['classification_status'],'판단 보류');self.assertEqual(network.call_count,1)
        self.assertFalse(Path(result['폴더경로']).exists())
        with patch.object(pipeline,'process_folder_result') as parse,patch.object(pipeline,'analyze_bid_with_ollama') as analyze:
            processed,stats=pipeline.process_bids([result],['보안'],[])
        parse.assert_not_called();analyze.assert_not_called()
        self.assertEqual(db.load_all_bids()[0]['classification_status'],'판단 보류')

    def test_only_selected_confirmed_rows_are_processed(self):
        first=self._bid('1');second=self._bid('2');third=self._bid('3')
        for row in (first,second):db.set_manual_classification(row['bid_id'],['보안'],['IPS'],['유지보수'])
        fake={'게시물ID':first['bid_id'],'수집사이트':'KB국민은행','제목':'사용자 확정','download_status':'성공','analysis_status':'성공'}
        with patch.object(crawler,'collect_confirmed_bid',return_value=fake) as collect,patch.object(pipeline,'process_bids',return_value=([fake],{})):
            result=pipeline.process_confirmed([first['bid_id']],self.temp.name,[])
        self.assertEqual(collect.call_count,1);self.assertEqual(collect.call_args.args[0]['bid_id'],first['bid_id'])
        self.assertEqual(result['completed'],1)
        states={r['bid_id']:r['classification_status'] for r in db.load_all_bids()}
        self.assertEqual(states[first['bid_id']],'수동 분류');self.assertEqual(states[second['bid_id']],'수집 대기');self.assertEqual(states[third['bid_id']],'판단 보류')
        with self.assertRaises(ValueError):pipeline.process_confirmed([third['bid_id']],self.temp.name,[])
        with self.assertRaises(ValueError):pipeline.process_confirmed([first['bid_id']],self.temp.name,[])

    def test_failed_confirmed_collection_stays_retry_pending(self):
        row=self._bid();db.set_manual_classification(row['bid_id'],['보안'],['IPS'],[])
        fake={'download_status':'실패','analysis_status':'실패'}
        with patch.object(crawler,'collect_confirmed_bid',return_value=fake),patch.object(pipeline,'process_bids',return_value=([fake],{})):
            result=pipeline.process_confirmed([row['bid_id']],self.temp.name,[])
        self.assertEqual(result['retry_pending'],1);self.assertEqual(db.load_all_bids()[0]['classification_status'],'수집 대기')

    def test_confirmed_download_extract_and_analysis_end_to_end(self):
        import pymupdf
        import hwp_parser
        from ollama_summary import SCHEMA
        first=self._bid('1');second=self._bid('2')
        for row in (first,second):db.set_manual_classification(row['bid_id'],['보안'],['IPS'],['유지보수'])
        document=pymupdf.open();page=document.new_page();page.insert_text((50,70),'IPS maintenance proposal. Electronic submission. Deadline 2026-11-01.',fontsize=10)
        pdf=document.tobytes();document.close()
        detail=Mock(text='<form name="frmDownload" action="/download"><input name="_FILE_NAME" value="rfp.pdf"></form>')
        attachment=Mock(headers={'Content-Disposition':'attachment; filename="rfp.pdf"'})
        attachment.iter_content.return_value=[pdf]
        response=dict(SCHEMA,summary='IPS 유지보수',fit_reason='보안',tags=['보안'],solution_tags=['IPS'],work_tags=['유지보수'],analysis_status='성공',model='fixture-model',cache_key='fixture')
        with patch.object(crawler,'_request',side_effect=[detail,attachment]) as network,patch.object(pipeline,'analyze_bid_with_ollama',return_value=response) as model,patch.object(hwp_parser,'CACHE_DIR',Path(self.temp.name)/'cache'):
            stats=pipeline.process_confirmed([first['bid_id']],str(Path(self.temp.name)/'downloads'),[])
        self.assertEqual(network.call_count,2);self.assertEqual(model.call_count,1);self.assertEqual(stats['completed'],1)
        rows={r['bid_id']:r for r in db.load_all_bids()}
        self.assertEqual(rows[first['bid_id']]['classification_status'],'수동 분류')
        self.assertEqual(rows[second['bid_id']]['classification_status'],'수집 대기')
        self.assertTrue(Path(rows[first['bid_id']]['folder_path']).joinpath('rfp.pdf').exists())
        self.assertEqual(rows[second['bid_id']]['folder_path'],'')

    def test_general_crawl_does_not_consume_manual_waiting_queue(self):
        row=self._bid();db.set_manual_classification(row['bid_id'],['보안'],['IPS'],[])
        item={'게시물ID':'1','제목':'IPS 유지보수'}
        with patch.object(crawler,'list_bids',return_value=[item]),patch.object(crawler,'_collect_item') as collect:
            result=crawler.crawl_and_download_bids_by_date('https://omoney.kbstar.com/list',self.temp.name,None,None,site_name='KB국민은행',selected_categories=['보안'])
        self.assertEqual(result,[]);collect.assert_not_called()

    def test_review_ui_confirmation_does_not_download(self):
        from streamlit.testing.v1 import AppTest
        row=self._bid()
        with patch.object(llm_manager,'get_secret',return_value=''),patch.object(pipeline,'process_confirmed') as collect:
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=20)
            self.assertFalse(app.exception)
            next(w for w in app.multiselect if w.label=='수동 분야').set_value(['보안'])
            app.run(timeout=20)
            next(w for w in app.multiselect if w.label=='수동 세부 항목').set_value(['IPS'])
            next(w for w in app.multiselect if w.label=='수동 업무').set_value(['유지보수'])
            next(w for w in app.button if w.label=='수집 대상으로 확정').click()
            app.run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(db.load_all_bids()[0]['classification_status'],'수집 대기')
            self.assertTrue(any(w.label=='추가 수집할 확정 공고' for w in app.multiselect))
        collect.assert_not_called()


if __name__=='__main__':unittest.main()
