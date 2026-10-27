"""화면과 CLI가 공유하는 수집 결과 처리 및 기존 공고 보강."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
from classification import classify_rules, classify_local, should_collect, CATEGORIES
from db import save_bid, load_all_bids, update_enrichment, finish_manual_collection
from hwp_parser import process_folder_result
from ollama_summary import analyze_bid_with_ollama


def extraction_details(result):
    return {'errors': result['errors'], 'files': [{k: v for k, v in f.items() if k not in ('text', 'segments')} for f in result['files']]}


def _process(item, selected, engines, primary, secondary):
    started = time.monotonic()
    if item.get('classification_status') in ('판단 보류','수집 대기'):
        item.setdefault('extraction_details', {})
        item['extraction_details'].update(source_access=item.get('access', {}), download_errors=item.get('download_errors', []), files=[])
        item['analysis_status'] = '미분석'
        return item
    documents = process_folder_result(item['폴더경로'])
    result = classify_rules(item['제목'])
    if not result['tags'] and documents['text']:
        result = classify_local(item['제목'], documents['text'])
    manual = item.get('manual_classification') or {}
    if isinstance(manual, str): manual=json.loads(manual)
    if manual: result=manual
    for key in ('tags','solution_tags','work_tags'):
        item[key] = list(dict.fromkeys(item.get(key, []) + result.get(key, []))) if not manual else list(manual.get(key, []))
    item['classification_reason'] = result['reason']
    item['extraction_status'] = documents['status']
    item['extraction_details'] = extraction_details(documents)
    item['extraction_details']['source_access'] = item.get('access', {})
    item['extraction_details']['download_errors'] = item.get('download_errors', [])
    if selected is not None and not should_collect(dict(result, tags=item['tags']), selected):
        item['analysis_status'] = '분야 제외'
        return item
    response = analyze_bid_with_ollama(item['제목'], documents['text'], primary, secondary, engine_configs=engines)
    mapping = {'summary': '사업요약', 'fit_score': '적합도점수', 'fit_reason': '적합사유', 'deadline': '제출마감일시', 'submit_type': '제출방식', 'required_docs': '필수제출서류', 'qualifications': '참가자격요건'}
    item.update({target: response[key] for key, target in mapping.items()})
    item.update(analysis_status=response['analysis_status'], analysis_model=response['model'], analysis_key=response['cache_key'], evidence=response['evidence'])
    if not manual:
        for key in ('tags','solution_tags','work_tags'):
            item[key] = list(dict.fromkeys(item[key] + response.get(key, [])))
    if documents['status'] == '부분 성공' and item['analysis_status'] == '성공':
        item['analysis_status'] = '부분 성공'
    item['processing_seconds'] = round(time.monotonic()-started, 3)
    return item


def process_bids(items, selected, engines, primary='통신', secondary='네트워크, 보안', progress=None):
    start = time.monotonic()
    processed = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for item in pool.map(lambda i: _process(dict(i), selected, engines, primary, secondary), items):
            save_bid(item)
            processed.append(item)
            if progress:
                progress(len(processed), len(items), item)
    return processed, {'processing_seconds': round(time.monotonic()-start, 3),
                       'document_cache_hits': sum(f.get('cache_hit', False) for i in processed for f in i['extraction_details']['files']),
                       'success': sum(i.get('analysis_status') == '성공' for i in processed),
                       'partial': sum(i.get('analysis_status') == '부분 성공' for i in processed),
                       'failed': sum(i.get('analysis_status') == '실패' for i in processed)}


def backfill_existing(progress=None, use_llm=True):
    rows = load_all_bids()
    stats = {'total': len(rows), 'success': 0, 'partial': 0, 'failed': 0}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index, (row, documents) in enumerate(zip(rows, pool.map(lambda r: process_folder_result(r['folder_path']), rows)), 1):
            result = classify_rules(row['title'])
            if use_llm and not result['certain'] and (documents['text'] or not result['tags']):
                result = classify_local(row['title'], documents['text'])
            update_enrichment(row['bid_id'], tags=result['tags'] or ['기타'], solution_tags=result['solution_tags'], work_tags=result['work_tags'], classification_reason=result['reason'],
                              extraction_status=documents['status'], extraction_details=extraction_details(documents))
            name = {'성공': 'success', '부분 성공': 'partial', '실패': 'failed'}[documents['status']]
            stats[name] += 1
            if progress:
                progress(index, len(rows), row)
    return stats


def reanalyze_existing(engines, selected=None, progress=None):
    items = [{'게시물ID': r['bid_id'], '수집사이트': r['site_name'], '번호': r['num'], '계열사/조직': r['org'],
              '제목': r['title'], '등록일': r['reg_date'], '폴더경로': r['folder_path'], '원본URL': r['origin_url'],
              'tags': json.loads(r['tags'] or '[]'), 'solution_tags':json.loads(r['solution_tags'] or '[]'), 'work_tags':json.loads(r['work_tags'] or '[]'),
              'manual_classification':json.loads(r['manual_classification'] or '{}'), 'classification_status':r['classification_status'],
              'download_status': r['download_status']} for r in load_all_bids() if r['classification_status'] not in ('판단 보류','수집 제외','수집 대기')]
    if selected is not None:
        items = [i for i in items if not i['tags'] or set(i['tags']) & set(selected)]
    return process_bids(items, selected, engines, progress=progress)


def process_confirmed(bid_ids, base_dir, engines, progress=None):
    from crawler import collect_confirmed_bid
    wanted = set(bid_ids)
    rows = [r for r in load_all_bids() if r['bid_id'] in wanted]
    if not rows or len(rows)!=len(wanted) or any(r['classification_status']!='수집 대기' or not json.loads(r['manual_classification'] or '{}').get('collect') for r in rows):
        raise ValueError('선택한 공고는 모두 수집 대기 상태여야 합니다')
    results=[]
    # 사용자가 선택한 대상만 다운로드한다. 완료한 다른 공고는 재실행하지 않는다.
    for index,row in enumerate(rows,1):
        item=collect_confirmed_bid(row,base_dir)
        processed,stats=process_bids([item],None,engines)
        result=processed[0]
        done=result.get('download_status')=='성공' and result.get('analysis_status')=='성공'
        finish_manual_collection(row['bid_id'],done)
        results.append(result)
        if progress:progress(index,len(rows),result)
    return {'selected':len(rows),'completed':sum(r.get('download_status')=='성공' and r.get('analysis_status')=='성공' for r in results),
            'retry_pending':sum(not (r.get('download_status')=='성공' and r.get('analysis_status')=='성공') for r in results)}
