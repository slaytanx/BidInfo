import contextlib
from datetime import date
from decimal import Decimal
import io
import json
import re
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import requests
import zipfile

import classification
import crawler
import db
import hwp_parser as parser
import llm_manager as llm
import ollama_summary as analysis
import pipeline


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

    def test_hwp_table_records_to_markdown(self):
        rec=lambda tag,level,body:struct.pack('<I',tag|level<<10|len(body)<<20)+body
        text=lambda s:rec(67,2,s.encode('utf-16-le'))
        cell=lambda col,row:rec(72,1,struct.pack('<HHIHHHH',1,0,0,col,row,1,1))
        stream=rec(67,0,'앞 문단'.encode('utf-16-le'))+rec(71,0,b' lbt')+rec(77,1,struct.pack('<IHH',0,2,2))
        for col,row,value in ((0,0,'항목'),(1,0,'기준'),(0,1,'자격'),(1,1,'등록')):stream+=cell(col,row)+text(value)
        stream+=rec(67,0,'뒤 문단'.encode('utf-16-le'))
        self.assertEqual(parser.parse_hwp_records(stream),'앞 문단\n| 항목 | 기준 |\n| --- | --- |\n| 자격 | 등록 |\n뒤 문단')

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

    def test_hwpx_table_markdown_keeps_order(self):
        p=self.root/'table.hwpx'
        row=lambda a,b:f'<tr><tc><subList><p><run><t>{a}</t></run></p></subList></tc><tc><subList><p><run><t>{b}</t></run></p></subList></tc></tr>'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('Contents/section0.xml',f'<sec><p><run><t>앞 문단</t></run></p><p><run><tbl>{row("항목","기준")}{row("자격","등록|필수")}</tbl></run></p><p><run><t>뒤 문단</t></run></p></sec>')
        text=parser.extract_document(p)['text']
        self.assertIn('| 항목 | 기준 |\n| --- | --- |\n| 자격 | 등록\\|필수 |',text)
        self.assertLess(text.index('앞 문단'),text.index('| 항목'));self.assertLess(text.index('| 자격'),text.index('뒤 문단'))
        self.assertEqual(text.count('자격'),1)

    def test_excel_sheets_as_csv(self):
        import pandas as pd
        p=self.root/'내역.xlsx'
        with pd.ExcelWriter(p) as w:
            pd.DataFrame([['품목','수량'],['서버',2],[None,None]]).to_excel(w,sheet_name='내역',index=False,header=False)
            pd.DataFrame().to_excel(w,sheet_name='빈시트')
        result=parser.extract_document(p)
        self.assertEqual(result['method'],'Excel CSV');self.assertIn('[시트: 내역]\n```csv\n품목,수량\n서버,2\n```',result['text'])
        self.assertNotIn('빈시트',result['text'])

    def test_ole_format_split(self):
        p=self.root/'legacy.file';p.write_bytes(b'\xd0\xcf\x11\xe0'+bytes(8))
        for streams,kind in ((['FileHeader'],'hwp'),(['Workbook'],'xls'),(['WordDocument'],'doc')):
            ole=Mock();ole.__enter__=Mock(return_value=ole);ole.__exit__=Mock(return_value=False);ole.exists=lambda n,s=streams:n in s
            with patch.object(parser.olefile,'OleFileIO',return_value=ole):
                self.assertEqual(parser.detect_format(p),kind)
        with patch.object(parser.olefile,'OleFileIO',return_value=ole):
            self.assertIn('DOCX로 변환',parser.extract_document(p)['errors'][0])

    def test_legacy_doc_download_is_kept_with_reason(self):
        ole=Mock();ole.__enter__=Mock(return_value=ole);ole.__exit__=Mock(return_value=False);ole.exists=lambda n:n=='WordDocument'
        response=Mock(headers={'Content-Disposition':'attachment; filename="old_rfp.file"'})
        response.iter_content.return_value=[b'\xd0\xcf\x11\xe0'+bytes(100)]
        folder=self.root/'dl';folder.mkdir()
        with patch.object(parser.olefile,'OleFileIO',return_value=ole):
            saved=Path(crawler.handle_download_and_extract_zip(response,folder,'x'))
            result=parser.process_folder_result(folder)
        self.assertEqual(saved.name,'old_rfp.doc');self.assertTrue(saved.exists())
        self.assertEqual(result['status'],'실패');self.assertEqual(result['files'][0]['errors'],['구형 DOC: DOCX로 변환해 주세요'])

    @unittest.skipUnless(shutil.which('pandoc'),'pandoc 없음')
    def test_rtf_and_word_html_named_doc(self):
        folder=self.root/'dl';folder.mkdir()
        bodies={'rtf.doc':rb'{\rtf1\ansi Proposal deadline notice\par}',
                'word.doc':'<html xmlns:o="urn:schemas-microsoft-com:office:office"><body><p>제안 마감 안내</p></body></html>'.encode()}
        for name,body in bodies.items():
            response=Mock(headers={'Content-Disposition':f'attachment; filename="{name}"'});response.iter_content.return_value=[body]
            saved=Path(crawler.handle_download_and_extract_zip(response,folder,'x'))
            result=parser.extract_document(saved)
            self.assertEqual(result['method'],'pandoc');self.assertEqual(result['status'],'성공')
        self.assertEqual(parser.detect_format(folder/'word.doc'),'html');self.assertEqual(parser.detect_format(folder/'rtf.doc'),'rtf')
        error_page=Mock(headers={});error_page.iter_content.return_value=[b'<html><body>error</body></html>']
        with self.assertRaises(ValueError):crawler.handle_download_and_extract_zip(error_page,folder,'x.file')

    def test_pandoc_html_table_becomes_markdown(self):
        html='앞<table><thead><tr><th>항목</th><th colspan="2">기준</th></tr></thead><tbody><tr><td><p>자격</p></td><td>등록</td><td>필수</td></tr></tbody></table>뒤'
        text=re.sub(r'<table\b.*?</table>',parser._html_table,html,flags=re.S)
        self.assertEqual(text,'앞| 항목 | 기준 |  |\n| --- | --- | --- |\n| 자격 | 등록 | 필수 |뒤')

    @unittest.skipUnless(shutil.which('pandoc'),'pandoc 없음')
    def test_docx_paragraphs_in_order(self):
        import subprocess
        p=self.root/'rfp.docx'
        subprocess.run(['pandoc','-f','gfm','-o',str(p)],input='# 공고\n\n| 항목 | 기준 |\n|---|---|\n| 자격 | 등록 |\n',text=True,check=True)
        result=parser.extract_document(p)
        self.assertEqual(result['method'],'DOCX 문단');self.assertEqual(result['text'],'[본문]\n공고\n항목\n기준\n자격\n등록')

    def test_docx_textbox_read_once_in_order(self):
        p=self.root/'box.docx'
        body=('<w:document xmlns:w="w" xmlns:mc="mc"><w:body><w:p><w:r><w:t>앞 문단</w:t></w:r></w:p>'
              '<w:p><w:r><mc:AlternateContent><mc:Choice><w:txbxContent><w:p><w:r><w:t>상자 </w:t><w:t>내용</w:t></w:r></w:p></w:txbxContent></mc:Choice>'
              '<mc:Fallback><w:txbxContent><w:p><w:r><w:t>상자 내용</w:t></w:r></w:p></w:txbxContent></mc:Fallback></mc:AlternateContent></w:r></w:p>'
              '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>셀 문장</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml',body)
        self.assertEqual(parser.extract_document(p)['text'],'[본문]\n앞 문단\n상자 내용\n셀 문장')

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

    def test_skip_reason_by_name_and_body(self):
        skip=parser.skip_reason
        for name in ('입찰 참여 신청서.pdf','3. 제안요청서(RFP) 배부신청서.pdf','붙임3 청렴 각서.hwp','개인정보 동의서.hwp','물품구매계약서(안).hwp'):
            self.assertTrue(skip(name),name)
        for name in ('입찰공고.pdf','제안요청서(RFP).pdf','물품명세서.xlsx','제안요청서 및 계약서(안).pdf','입찰공고 및 참가신청서.pdf'):
            self.assertEqual(skip(name),'',name)
        self.assertEqual(skip('notice_1.pdf','# ~~입찰 참여 신청서~~\n내용'),'신청·서약 양식')
        self.assertEqual(skip('x.docx','[본문]\n(별첨 3)\n확약서'),'별첨 서식')
        self.assertEqual(skip('notice_2.pdf','[p.1]\n입찰 공고\n신청서를 제출'),'')
        self.assertEqual(skip('물품구매입찰유의서.hwp','입찰보증금은 입찰금액의 5% 이상'),'표준 입찰유의서')
        self.assertEqual(skip('물품구매입찰유의서.hwp','입찰 마감 2026.11.20 까지'),'')
        self.assertEqual(skip('물품구매입찰유의서.hwp','추정가격 1,234,000,000원'),'')
        self.assertEqual(skip('물품구매입찰유의서.hwp'),'')  # 본문 없이는 표준 여부를 모른다

    def test_folder_skips_forms_and_keeps_status(self):
        (self.root/'입찰공고.txt').write_text('서버 구축 입찰 공고. 제출 마감 2026-11-20 15:00. 참가자격은 사업자등록증 보유 업체.')
        (self.root/'참가신청서.txt').write_text('참가 신청서 양식 성명 ____ 서명 ____')
        (self.root/'물품구매입찰유의서.txt').write_text('입찰보증금 납부 방법과 낙찰자 결정 방법에 관한 일반 규정입니다. 모든 입찰자는 이를 준수해야 합니다.')
        result=parser.process_folder_result(self.root)
        self.assertEqual(result['status'],'성공');self.assertIn('제출 마감',result['text']);self.assertNotIn('성명',result['text']);self.assertNotIn('일반 규정',result['text'])
        skipped={f['file']:f['skip'] for f in result['files'] if f['status']=='생략'}
        self.assertEqual(skipped,{'참가신청서.txt':'신청·서약 양식','물품구매입찰유의서.txt':'표준 입찰유의서'})

    def test_folder_with_only_forms_is_not_analyzed(self):
        (self.root/'참가신청서.txt').write_text('참가 신청서 양식 성명 ____')
        result=parser.process_folder_result(self.root)
        self.assertEqual((result['status'],result['text']),('실패',''));self.assertIn('분석 대상 문서 없음',result['errors'][0])

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

    def test_http_filename_latin1_korean_decode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            korean_str = '[KB국민은행]_Oracle社_공고.pdf'
            mojibake = korean_str.encode('utf-8').decode('latin1')
            response = Mock(headers={'Content-Disposition': f'attachment; filename="{mojibake}"'})
            response.iter_content.return_value = [b'%PDF-1.5 dummy content']
            saved = Path(crawler.handle_download_and_extract_zip(response, folder, 'default.pdf'))
            self.assertEqual(saved.name, korean_str)
            self.assertTrue(saved.exists())

    def test_crawler_session_creates_curl_cffi_or_requests(self):
        with crawler._session() as session:
            self.assertIn('ko-KR', session.headers.get('Accept-Language', ''))
            if crawler._HAS_CURL_CFFI:
                from curl_cffi import requests as cffi_requests
                self.assertIsInstance(session, cffi_requests.Session)

    def test_crawler_session_fallback_when_cffi_disabled(self):
        with patch.object(crawler, '_HAS_CURL_CFFI', False):
            with crawler._session() as session:
                self.assertIsInstance(session, requests.Session)
                self.assertIn('ko-KR', session.headers.get('Accept-Language', ''))
                self.assertEqual(session.headers.get('User-Agent'), 'Mozilla/5.0')

    def test_crawler_request_retry_after_header_backoff(self):
        resp_429 = Mock(status_code=429, headers={'Retry-After': '2.5'})
        resp_200 = Mock(status_code=200, text='ok')
        session = Mock()
        session.request.side_effect = [resp_429, resp_200]

        sleeps = []
        with patch.object(crawler.time, 'sleep', side_effect=sleeps.append):
            resp = crawler._request(session, 'GET', 'https://example.com/test')

        self.assertEqual(resp, resp_200)
        self.assertIn(2.5, sleeps)
        resp_429.close.assert_called_once()

    def test_crawler_request_exponential_backoff_on_500(self):
        resp_500 = Mock(status_code=500, headers={})
        resp_200 = Mock(status_code=200, text='ok')
        session = Mock()
        session.request.side_effect = [resp_500, resp_200]

        sleeps = []
        with patch.object(crawler.time, 'sleep', side_effect=sleeps.append):
            resp = crawler._request(session, 'GET', 'https://example.com/test')

        self.assertEqual(resp, resp_200)
        self.assertIn(1.5, sleeps)
        resp_500.close.assert_called_once()


class BudgetAnalysisTests(unittest.TestCase):
    def test_empty_and_bad_json(self):
        self.assertEqual(analysis.analyze_bid_with_ollama('서버','')['analysis_status'],'실패')
        with self.assertRaises(ValueError):analysis.validate_analysis({'summary':'불완전'})
        with self.assertRaises(Exception):llm.parse_json('잘못된 JSON')

    def test_parse_json_resilience(self):
        # 1. 서두/결미 설명문과 ```json 코드 블록 포함 응답
        res1 = llm.parse_json('네, 요청하신 입찰 분석 결과입니다:\n```json\n{"summary": "보안 장비 구축"}\n```\n참고 바랍니다.')
        self.assertEqual(res1, {"summary": "보안 장비 구축"})

        # 2. 백틱 없는 문장형 응답 속 JSON 객체 슬라이싱
        res2 = llm.parse_json('분석 결과는 다음과 같습니다: {"summary": "방화벽 교체"} 이상입니다.')
        self.assertEqual(res2, {"summary": "방화벽 교체"})

        # 3. 딕셔너리가 아닌 리스트 응답은 LLMError
        with self.assertRaises(llm.LLMError):
            llm.parse_json('```json\n["리스트", "응답"]\n```')

    def test_unknown_tags_are_dropped_not_fatal(self):
        result=dict(analysis.SCHEMA,summary='서버 구축',fit_reason='서버',tags=['인프라','없는분야'],solution_tags=['서버','없는항목'],work_tags=['구축','없는업무'],evidence=[])
        checked=analysis.validate_analysis(result)
        self.assertEqual((checked['tags'],checked['solution_tags'],checked['work_tags']),(['인프라'],['서버'],['구축']))
        with self.assertRaises(ValueError):analysis.validate_analysis(dict(result,tags='인프라'))

    def test_ground_ignores_whitespace_and_markdown_only(self):
        text='| 제출 마감 | **2026-11-20\n15:00** |'
        ev=lambda q:{'field':'deadline','quote':q,'location':'p.1'}
        result=analysis._ground(dict(analysis.SCHEMA,deadline='2026-11-20 15:00',evidence=[ev('제출 마감 2026-11-20 15:00'),ev('마감 2026-12-01')]),text)
        self.assertEqual([e['quote'] for e in result['evidence']],['제출 마감 2026-11-20 15:00']);self.assertEqual(result['deadline'],'2026-11-20 15:00')
        self.assertEqual(analysis._ground(dict(analysis.SCHEMA,deadline='x',evidence=[ev('마감 2026-12-01')]),text)['deadline'],'확인 불가')

    def test_tail_is_processed_and_cache_reused(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            result=dict(analysis.SCHEMA,summary='서버 구축',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[])
            call.side_effect=lambda prompt,*a,**k:json.dumps({'facts':[]} if '항목:' in prompt else result,ensure_ascii=False)
            text='앞부분\n'*3000+'후반 요구사항: 특수자격증'
            analysis.analyze_bid_with_ollama('서버',text)
            self.assertTrue(any('특수자격증' in c.args[0] for c in call.call_args_list))
            count=call.call_count
            analysis.analyze_bid_with_ollama('서버',text)
            self.assertEqual(call.call_count,count)

    def test_failed_chunk_is_skipped_but_all_failed_is_failure(self):
        ok=dict(analysis.SCHEMA,summary='서버 구축',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[])
        text='가'*9000+'나'*100
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            call.side_effect=lambda prompt,*a,**k:json.dumps({'facts':[]} if '항목:' in prompt else ok,ensure_ascii=False) if '나나' in prompt else '깨진 응답'
            r=analysis.analyze_bid_with_ollama('서버',text)
            self.assertEqual(r['analysis_status'],'성공');self.assertIn('일부 구간 분석 실패(1/2)',r['cautions'])
            call.side_effect=lambda *a,**k:'깨진 응답'
            self.assertEqual(analysis.analyze_bid_with_ollama('서버',text+'다')['analysis_status'],'실패')

    def test_fieldwise_facts_are_grounded_and_format_checked(self):
        text='제출 마감 2026-11-20 15:00\n입찰보증금은 입찰금액의 5% 이상\n추정가격 5억원\n참가자격 사업자등록증 보유'
        facts=[{'field':'deadline','fact':'2026-11-20 15:00 마감','quote':'제출 마감 2026-11-20 15:00'},
               {'field':'deadline','fact':'2026-12-01 마감','quote':'지어낸 인용 2026-12-01'},
               {'field':'budget','fact':'입찰보증금 5%','quote':'입찰보증금은 입찰금액의 5% 이상'},
               {'field':'budget','fact':'추정가격 5억원','quote':'추정가격 5억원'},
               {'field':'qualifications','fact':'사업자등록증 보유','quote':''},
               {'field':'duration','fact':'기간 없음','quote':'참가자격'},
               {'field':'evaluation','fact':'배점 70점','quote':'참가자격'}]
        base=dict(analysis.SCHEMA,summary='서버',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[],duration='6개월')
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            call.side_effect=lambda prompt,*a,**k:json.dumps({'facts':facts} if '항목:' in prompt else base,ensure_ascii=False)
            result=analysis.analyze_bid_with_ollama('서버',text)
        self.assertEqual(result['deadline'],'2026-11-20 15:00 마감')           # 지어낸 인용의 사실은 버림
        self.assertEqual(result['budget'],'추정가격 5억원')                     # 금액 형식이 아닌 값은 버림
        self.assertEqual(result['qualifications'],'근거 미확인: 사업자등록증 보유')
        self.assertEqual(result['duration'],'확인 불가')                        # 숫자가 없는 기간 값은 버림
        self.assertEqual(result['evaluation'],'배점 70점')                     # 형식 검사 대상이 아닌 항목은 인용만 맞으면 유지
        where=f'원문 문자 1-{len(text)}'
        self.assertEqual([(e['field'],e['location']) for e in result['evidence']],[('deadline',where),('budget',where),('evaluation',where)])

    def test_fieldwise_failure_keeps_chunk_values(self):
        base=dict(analysis.SCHEMA,summary='서버',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[],duration='6개월')
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            call.side_effect=lambda prompt,*a,**k:'잘못된 응답' if '항목:' in prompt else json.dumps(base,ensure_ascii=False)
            result=analysis.analyze_bid_with_ollama('서버','본문')
        self.assertEqual((result['analysis_status'],result['duration']),('성공','근거 미확인: 6개월'))

    def test_fieldwise_empty_group_keeps_chunk_values(self):
        base=dict(analysis.SCHEMA,summary='서버',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[],duration='6개월')
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            call.side_effect=lambda prompt,*a,**k:json.dumps({'facts':[]} if '항목:' in prompt else base,ensure_ascii=False)
            result=analysis.analyze_bid_with_ollama('서버','본문')
        self.assertEqual(result['duration'],'근거 미확인: 6개월')

    def test_failed_fact_group_keeps_chunk_values_and_warns(self):
        base=dict(analysis.SCHEMA,summary='서버',fit_reason='서버',tags=['인프라'],solution_tags=['서버'],work_tags=['구축'],evidence=[],duration='6개월')
        def fail_items(prompt,*a,**k):
            if '항목:' in prompt: raise RuntimeError('timeout')
            return json.dumps(base,ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp,patch.object(analysis,'CACHE_DIR',Path(tmp)),patch.object(analysis,'generate_llm_response') as call:
            call.side_effect=fail_items
            result=analysis.analyze_bid_with_ollama('서버','본문')
        self.assertEqual((result['analysis_status'],result['duration']),('성공','근거 미확인: 6개월'))
        self.assertIn('항목 추출',result['cautions'])

    def test_passages_pick_keyword_blocks_in_order(self):
        text=''.join(f'일반 문장 {i}\n' for i in range(400))+'제출 마감 2026-11-20\n'+''.join(f'다른 문장 {i}\n' for i in range(400))+'추정가격 5억원\n'
        picked=analysis._passages(text,{'마감':3,'추정':3})
        self.assertLessEqual(sum(len(t) for _,_,t in picked),analysis.PASSAGE_BUDGET)
        joined=''.join(t for _,_,t in picked);self.assertIn('2026-11-20',joined);self.assertIn('5억원',joined)
        self.assertEqual([s for s,_,_ in picked],sorted(s for s,_,_ in picked))
        self.assertTrue(all(text[s:e]==t for s,e,t in picked))

    def test_unquoted_conditions_are_unknown(self):
        result=dict(analysis.SCHEMA,deadline='2026-10-30',evidence=[{'field':'deadline','quote':'지어낸 인용','location':'p.1'}])
        self.assertEqual(analysis._ground(result,'원문')['evidence'],[])
        self.assertEqual(result['deadline'],'확인 불가')

    def test_uncited_value_is_marked_and_merge_prefers_verified(self):
        ev={'field':'deadline','quote':'마감 2026-11-20','location':'p.1'}
        first=analysis._ground(dict(analysis.SCHEMA,deadline='2026-11-20',budget='5억원',evidence=[ev]),'마감 2026-11-20')
        self.assertEqual((first['deadline'],first['budget']),('2026-11-20','근거 미확인: 5억원'))
        second=analysis._ground(dict(analysis.SCHEMA,deadline='2026-11-21',budget='확인 불가',evidence=[]),'후반')
        merged=analysis._merge([dict(first,tags=['인프라']),dict(second,fit_score=70,tags=['보안'])])
        self.assertEqual(merged['deadline'],'2026-11-20');self.assertEqual(merged['budget'],'근거 미확인: 5억원')
        self.assertEqual((merged['fit_score'],merged['evidence']),(70,[ev]));self.assertEqual(set(merged['tags']),{'인프라','보안'})

    def test_openrouter_failure_falls_back_to_local(self):
        with patch.object(llm, 'get_secret', return_value='fixture'), patch.object(llm, 'call_ollama', return_value='local'), patch.object(llm.requests, 'post', side_effect=llm.LLMError('OpenRouter error')):
            result = llm.generate_llm_response('prompt', llm.engine_configs())
        self.assertEqual(result, 'local')

    def test_question_answer_rejects_invented_quotes_and_handles_failure(self):
        with patch.object(analysis, 'generate_llm_response', return_value='{"answer":"허위 마감일","evidence":[{"quote":"없는 원문","location":"p.1"}]}'):
            answer = analysis.answer_bid_question('사업', '실제 문서 본문', '마감일?')
        self.assertNotIn('허위 마감일', answer)
        with patch.object(analysis, 'generate_llm_response', side_effect=RuntimeError('synthetic failure')):
            answer = analysis.answer_bid_question('사업', '실제 문서 본문', '마감일?')
        self.assertIn('처리 실패', answer)

    def test_question_answer_filters_top_chunks_on_long_document(self):
        # 4개 청크(>3개) 이상의 긴 문서에서 질문 키워드가 포함된 상위 관련 청크만 선별 호출
        chunk1 = "일반 소개문입니다. " * 800
        chunk2 = "마감일시 안내: 제안서 접수 마감은 2026-11-20 17:00까지입니다. " * 400
        chunk3 = "기타 사항입니다. " * 800
        chunk4 = "첨부 서식 안내입니다. " * 800
        long_doc = chunk1 + chunk2 + chunk3 + chunk4

        calls = []
        def mock_generate(prompt, *args, **kwargs):
            calls.append(prompt)
            if "2026-11-20" in prompt:
                return '{"answer":"마감일시는 2026-11-20 17:00입니다.","evidence":[{"quote":"2026-11-20 17:00까지입니다.","location":"p.2"}]}'
            return '{"answer":"확인 불가","evidence":[]}'

        with patch.object(analysis, 'generate_llm_response', side_effect=mock_generate):
            answer = analysis.answer_bid_question('사업', long_doc, '마감일시 언제인가요?')
        self.assertIn("2026-11-20 17:00", answer)
        self.assertLessEqual(len(calls), 3)


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
            self.assertFalse([b for b in app.button if b.label == '팝업 상세보기'])  # 행을 고르기 전에는 편집 UI가 없다
            app.session_state['bid_table'] = {'selection': {'rows': [0], 'columns': []}}
            app.run(timeout=20)
            self.assertFalse(app.exception)
            next(b for b in app.button if b.label == '팝업 상세보기').click()
            app.run(timeout=20)
            self.assertFalse(app.exception)

    def test_cloud_retry_on_429(self):
        busy = Mock(status_code=429, headers={'Retry-After':'0'})
        okay = Mock(status_code=200)
        okay.json.return_value = {'choices':[{'message':{'content':'{"ok":true}'}}]}
        with patch.object(llm.requests, 'post', side_effect=[busy, okay]), patch.object(llm.time, 'sleep'):
            res = llm.call_openrouter('시험', 'synthetic-key', max_tokens=100)
        self.assertEqual(res, '{"ok":true}')

    def test_cloud_retry_on_429_with_http_date_retry_after(self):
        # HTTP-Date 포맷의 Retry-After 헤더 반환 시 ValueError 크래시 없이 1초 기본값으로 안전 재시도
        busy = Mock(status_code=429, headers={'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'})
        okay = Mock(status_code=200)
        okay.json.return_value = {'choices': [{'message': {'content': '{"ok":true}'}}]}
        with patch.object(llm.requests, 'post', side_effect=[busy, okay]), patch.object(llm.time, 'sleep') as mock_sleep:
            res = llm.call_openrouter('시험', 'synthetic-key', max_tokens=100)
        self.assertEqual(res, '{"ok":true}')
        mock_sleep.assert_called_once_with(1.0)

    def test_openrouter_400_fatal_no_retry(self):
        fatal = Mock(status_code=400)
        with patch.object(llm.requests, 'post', return_value=fatal):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('프롬프트', 'test-key', max_tokens=50)
        self.assertIn('OpenRouter HTTP 400', str(ctx.exception))

    def test_generate_llm_response_preserves_exception(self):
        configs = [{'provider': 'Local Ollama', 'model': 'llama3'}]
        with patch.object(llm, 'call_ollama', side_effect=RuntimeError('Connection refused to Ollama')):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate_llm_response('prompt', configs)
            self.assertIn('Local Ollama: Connection refused to Ollama', str(ctx.exception))

        unsupported_configs = [{'provider': 'NVIDIA NIM', 'model': 'meta/llama-3.1-8b', 'api_key': ''}]
        with self.assertRaises(llm.LLMError) as ctx2:
            llm.generate_llm_response('prompt', unsupported_configs)
        self.assertIn('NVIDIA NIM: 미지원 공급자: NVIDIA NIM', str(ctx2.exception))

    def test_cascading_multi_engine_fallback_preserves_all_errors(self):
        configs = [
            {'provider': 'OpenRouter', 'model': 'deepseek', 'api_key': 'k1'},
            {'provider': 'Local Ollama', 'model': 'llama3'}
        ]
        with patch.object(llm, 'call_openrouter', side_effect=RuntimeError('OpenRouter rate limit')), \
             patch.object(llm, 'call_ollama', side_effect=RuntimeError('Ollama dead')):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate_llm_response('prompt', configs)
            err = str(ctx.exception)
            self.assertIn('OpenRouter: OpenRouter rate limit', err)
            self.assertIn('Local Ollama: Ollama dead', err)

    def test_field_format_rejection(self):
        text = "사업기간 및 사업예산 1억원 관련 공고"
        raw_facts = [
            {"field": "budget", "fact": "협의에 따름", "quote": "협의에 따름"},
            {"field": "duration", "fact": "추후 결정", "quote": "추후 결정"},
            {"field": "deadline", "fact": "별도 통보", "quote": "별도 통보"},
            {"field": "budget", "fact": "사업예산 1억원", "quote": "1억원"},
        ]
        with patch.object(analysis, "_cached_facts", return_value=raw_facts):
            facts = analysis._extract_facts(text, ["budget", "duration", "deadline"], {}, [])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["field"], "budget")
        self.assertEqual(facts[0]["fact"], "사업예산 1억원")

    def test_extract_facts_short_quote_in_text(self):
        text = '사업기간은 6개월이며 총 사업예산은 1억원이다.'
        raw_facts = [
            {'field': 'duration', 'fact': '사업기간 6개월', 'quote': '6개월'},
            {'field': 'budget', 'fact': '사업예산 1억원', 'quote': '1억원'}
        ]
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, ['duration', 'budget'], {'개월': 1, '억원': 1}, [])
        self.assertEqual(len(facts), 2)
        self.assertTrue(facts[0]['verified'])
        self.assertEqual(facts[0]['quote'], '6개월')
        self.assertTrue(facts[1]['verified'])
        self.assertEqual(facts[1]['quote'], '1억원')

    def test_extract_facts_seen_not_poisoned_by_invalid_quote(self):
        text = '사업기간은 6개월이다.'
        raw_facts = [
            {'field': 'duration', 'fact': '사업기간 6개월', 'quote': '거짓기간'},
            {'field': 'duration', 'fact': '사업기간 6개월', 'quote': '6개월'}
        ]
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, ['duration'], {'개월': 1}, [])
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]['verified'])
        self.assertEqual(facts[0]['quote'], '6개월')


class AnalysisProgressUITests(unittest.TestCase):
    def test_step_callback_in_process_folder_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.txt').write_text('테스트 입찰 문서 내용입니다. 본문 테스트.', encoding='utf-8')
            calls = []
            def callback(msg):
                calls.append(msg)
            result = parser.process_folder_result(folder, step_callback=callback)
            self.assertEqual(result['status'], '성공')
            self.assertTrue(any('test.txt' in c for c in calls))

    def test_step_callback_in_analyze_bid(self):
        doc_text = '[제안요청서]\n사업예산은 1억원이며 수행기간은 6개월이다.'
        calls = []
        def callback(msg):
            calls.append(msg)
        mock_resp = {
            'summary': '테스트 사업', 'budget': '1억원', 'duration': '6개월',
            'evaluation': '', 'deadline': '', 'submit_type': '', 'required_docs': '',
            'qualifications': '', 'cautions': '', 'tags': ['통신'], 'solution_tags': [],
            'work_tags': [], 'fit_score': 90, 'fit_reason': '적합', 'evidence': [],
            '_model': 'mock-model'
        }
        with patch.object(analysis, '_cached_json', return_value=mock_resp), \
             patch.object(analysis, '_apply_facts', return_value=(dict(mock_resp), 0)):
            res = analysis.analyze_bid_with_ollama('테스트 공고', doc_text, step_callback=callback)
            self.assertEqual(res['analysis_status'], '성공')
            self.assertTrue(any('요건 분석' in c for c in calls))

    def test_step_callback_in_process_bids(self):
        calls = []
        def step_cb(item, msg):
            calls.append((item.get('제목'), msg))

        mock_item = {'게시물ID': 'test_p_1', '수집사이트': 'KB국민은행', '제목': '차세대 네트워크 구축', '폴더경로': '/dummy'}
        with patch.object(pipeline, 'process_folder_result', return_value={'text': '문서 내용', 'status': '성공', 'errors': [], 'files': []}), \
             patch.object(pipeline, 'analyze_bid_with_ollama', return_value={
                 'summary': '요약', 'fit_score': 95, 'fit_reason': '적합', 'deadline': '',
                 'submit_type': '', 'required_docs': '', 'qualifications': '', 'analysis_status': '성공',
                 'model': 'mock', 'cache_key': 'k1', 'evidence': [], 'tags': ['통신'],
                 'solution_tags': [], 'work_tags': []
             }), \
             patch.object(pipeline, 'save_bid'):
            processed, stats = pipeline.process_bids([mock_item], None, [], step_callback=step_cb)
            self.assertEqual(len(processed), 1)
            self.assertTrue(any('추출 시작' in msg for _, msg in calls))
            self.assertTrue(any('분석 시작' in msg or '완료' in msg for _, msg in calls))

    def test_app_run_crawl_status_container(self):
        from streamlit.testing.v1 import AppTest
        mock_item = {'게시물ID': 'test_crawl_1', '수집사이트': 'KB국민은행', '제목': '대용량 RFP 테스트 공고', '폴더경로': '/dummy'}
        with patch.object(crawler, 'crawl_and_download_bids_by_date', return_value=[mock_item]), \
             patch.object(pipeline, 'process_bids', return_value=([mock_item], {'success': 1})), \
             patch.object(llm, 'get_secret', return_value=''):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
            self.assertFalse(app.exception)
            crawl_btn = next(b for b in app.sidebar.button if b.label == "수집 시작")
            crawl_btn.click()
            app.run(timeout=30)
            self.assertFalse(app.exception)


class FinancialCustomScraperTests(unittest.TestCase):
    def test_shinhands_list_bids_paging_and_parsing(self):
        page1_html = """
        <tbody>
            <tr>
                <td>82</td>
                <td class="subject"><a href="/insight/business-notice?page=0&cmd=view&id=297">신한베트남은행 카드 Master 브랜드 RBA 구축</a></td>
                <td>2025.01.23 ~ 2025.01.31</td>
                <td>2025.01.23</td>
                <td>1,234</td>
            <tr>
        </tbody>
        """
        page2_html = """
        <tbody>
            <tr>
                <td>81</td>
                <td class="subject"><a href="/insight/business-notice?page=2&cmd=view&id=280">소그룹사 베어메탈 수탁시스템 DB서버 도입</a></td>
                <td>2024.11.01 ~ 2024.11.08</td>
                <td>2024.11.01</td>
                <td>567</td>
            <tr>
        </tbody>
        """
        resp1 = Mock(text=page1_html, status_code=200)
        resp2 = Mock(text=page2_html, status_code=200)
        empty_resp = Mock(text="<tbody></tbody>", status_code=200)

        with patch.object(crawler, '_request', side_effect=[resp1, resp2, empty_resp]):
            bids = crawler.list_bids(
                'https://www.shinhands.co.kr/insight/disclosure',
                date(2024, 1, 1),
                date(2025, 12, 31),
                site_name='신한DS 사업공시'
            )
            self.assertEqual(len(bids), 2)
            self.assertEqual(bids[0]['게시물ID'], '297')
            self.assertEqual(bids[0]['계열사/조직'], '신한은행')
            self.assertEqual(bids[0]['원본URL'], 'https://www.shinhands.co.kr/insight/business-notice?cmd=view&id=297')
            self.assertEqual(bids[1]['게시물ID'], '280')
            self.assertEqual(bids[1]['계열사/조직'], '신한DS')

    def test_shinhands_attachments_extraction(self):
        detail_html = """
        <div class="fileList">
            <div><a class="btn-board-file-download" data-db="1" data-id="287" data-no="0">입찰공고문_RBA구축.docx</a></div>
        </div>
        """
        files = crawler._attachments(detail_html, 'https://www.shinhands.co.kr/insight/business-notice?cmd=view&id=287')
        self.assertEqual(len(files), 1)
        method, url, payload, name = files[0]
        self.assertEqual(method, 'GET')
        self.assertIn('db=1&id=287&no=0', url)
        self.assertIsNone(payload)
        self.assertEqual(name, '입찰공고문_RBA구축.docx')

    def test_hanati_list_bids_paging_and_parsing(self):
        login_html = '<meta name="_csrf" content="token123" />'
        page1_html = """
        <tbody>
            <tr>
                <td>1</td>
                <td><a data-id="im01O42H" data-no="im01O42H" onclick="javascript:noticeLink(this)">[입찰공고] 하나증권 노후 개발스토리지 교체</a></td>
                <td>일반경쟁</td>
                <td>담당자</td>
                <td>2026-11-05</td>
            </tr>
        </tbody>
        """
        empty_html = "<tbody></tbody>"
        resp_login = Mock(text=login_html, status_code=200)
        resp_p1 = Mock(text=page1_html, status_code=200)
        resp_empty = Mock(text=empty_html, status_code=200)

        with patch.object(crawler, '_request', side_effect=[resp_login, resp_p1, resp_empty]):
            bids = crawler.list_bids(
                'https://www.hanati.co.kr',
                date(2026, 1, 1),
                date(2026, 12, 31),
                site_name='하나금융티아이 공지사항'
            )
            self.assertEqual(len(bids), 1)
            self.assertEqual(bids[0]['게시물ID'], 'im01O42H')
            self.assertEqual(bids[0]['계열사/조직'], '하나증권')
            self.assertEqual(bids[0]['원본URL'], 'https://tipartner.hanati.co.kr/sp/mn/bidding/bidNoticeLink.do?post_no=im01O42H')

    def test_hanati_attachments_extraction(self):
        detail_html = """
        <ul id="att_list">
            <li><a data-arg="att999" onclick="downloadAttFile(this)">3. 입찰세부내역.pdf</a></li>
        </ul>
        <input type="hidden" name="_csrf" value="detail_token_456" />
        """
        files = crawler._attachments(detail_html, 'https://tipartner.hanati.co.kr/sp/mn/bidding/bidNoticeLink.do?post_no=abc')
        self.assertEqual(len(files), 1)
        method, url, payload, name = files[0]
        self.assertEqual(method, 'POST')
        self.assertEqual(url, 'https://tipartner.hanati.co.kr/sp/mn/bidding/upload/download.do')
        self.assertEqual(payload, {'id': 'att999', '_csrf': 'detail_token_456'})
        self.assertEqual(name, '3. 입찰세부내역.pdf')

    def test_hanati_collect_item_dispatches_post(self):
        item = {
            '게시물ID': 'post_hana_1',
            '수집사이트': '하나금융티아이 공지사항',
            '제목': '하나은행 클라우드 인프라 구축',
            '원본URL': 'https://tipartner.hanati.co.kr/sp/mn/bidding/bidNoticeLink.do?post_no=post_hana_1'
        }
        login_html = '<meta name="_csrf" content="token_login" />'
        detail_html = """
        <html>
            <body>
                [입찰공고] 하나은행 클라우드 인프라 구축 공고문 내용입니다.
                <input type="hidden" name="_csrf" value="token_login" />
            </body>
        </html>
        """
        resp_login = Mock(text=login_html, status_code=200)
        resp_detail = Mock(text=detail_html, status_code=200)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(crawler, '_request', side_effect=[resp_login, resp_detail]) as mock_req, \
             patch.object(crawler, 'classify_local', return_value={'certain': True, 'tags': ['인프라'], 'solution_tags': [], 'work_tags': [], 'reason': '테스트'}):
            res = crawler._collect_item(item, tmp, ['인프라'], local_classifier=True, force_refresh=False)
            self.assertIsNotNone(res)
            # Second call should be POST with post_no and _csrf
            call2 = mock_req.call_args_list[1]
            self.assertEqual(call2[0][1], 'POST')
            self.assertEqual(call2[1].get('data'), {'post_no': 'post_hana_1', '_csrf': 'token_login'})


if __name__ == '__main__':unittest.main()
