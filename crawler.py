"""목록·분류·첨부 수집을 분리한 은행별 공개 게시판 수집기."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import html
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin, urlparse, unquote
import requests
from classification import classify_rules, classify_local, should_collect
from date_utils import extract_date_from_text
from db import existing_bid_ids, manual_classifications
from hwp_parser import detect_format, extract_zip, MAX_FILE_BYTES

_HOST_LOCK = threading.Lock()
_LAST_REQUEST = {}


class CrawlError(RuntimeError):
    pass


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|\x00-\x1f]', '_', name).strip('. ')[:160] or 'attachment'


def extract_org_from_title(title):
    match = re.search(r'\((은행|손해|생명|증권|중앙회|공통|유통|자산신탁|KB|국민|농협)\)', title)
    return match.group(1) if match else 'KB국민은행' if '국민' in title else 'NH농협'


def _text(value):
    value = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', value, flags=re.S | re.I)
    return html.unescape(re.sub(r'<[^>]+>', ' ', value)).strip()


def _request(session, method, url, **kwargs):
    host = urlparse(url).hostname
    for attempt in range(2):
        with _HOST_LOCK:
            delay = max(0, .3 - (time.monotonic() - _LAST_REQUEST.get(host, 0)))
            if delay:
                time.sleep(delay)
            _LAST_REQUEST[host] = time.monotonic()
        try:
            response = session.request(method, url, timeout=(5, 25), **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                response.close(); time.sleep(1); continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if attempt:
                raise CrawlError(f'{host}: 네트워크 또는 HTTP 오류') from exc
    raise CrawlError('수집 재시도 실패')


def _session():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'ko-KR,ko;q=0.9'})
    return session


def _attrs(tag):
    return {key.lower(): html.unescape(a or b) for key, a, b in re.findall(r'([\w-]+)\s*=\s*(?:"([^"]*)"|\x27([^\x27]*)\x27)', tag)}


def _links(markup):
    for attributes, label in re.findall(r'<a\b([^>]*)>(.*?)</a>', markup, re.S | re.I):
        yield _attrs(attributes).get('href', ''), _text(label)


def _item(raw_id, number, title, registered, site, origin, org):
    return {'게시물ID': raw_id, '번호': number, '제목': title, '등록일': registered.isoformat(),
            '수집사이트': site, '원본URL': origin, '계열사/조직': org}


def list_bids(url, start_date, end_date, keyword='', site_name='미지정', max_pages=100, access=None):
    if start_date > end_date:
        raise ValueError('시작일이 종료일보다 늦습니다')
    found, seen = [], set()
    markup = ''
    is_nh = 'nonghyup.com' in urlparse(url).netloc
    is_nhit = 'nonghyupit.com' in urlparse(url).netloc
    is_kb = 'kbstar.com' in urlparse(url).netloc
    with _session() as session:
        for page in range(1, max_pages + 1):
            if is_nh:
                response = _request(session, 'POST', url, data={'intgBsnBlbdGrpC': 'BBSMSTR_000000000019', 'pageIndex': page, 'searchCnd': '0', 'searchWrd': keyword})
            elif is_nhit and page > 1:
                response = _request(session, 'POST', urljoin(url, '/bbs/nonghyupit/675/artclList.do'), data={'page': page})
            elif is_kb and page > 1 and markup:
                forms = re.findall(r'<form\b([^>]*)>(.*?)</form>', markup, re.S | re.I)
                paging = next(((a, b) for a, b in forms if _attrs(a).get('name', '').startswith('pageinput')), None)
                if not paging:
                    if access is not None:
                        access['pagination'] = '추가 페이지 양식 없음'
                    break
                attributes, body = paging
                inputs = [_attrs(t) for t in re.findall(r'<input\b([^>]*)>', body, re.I)]
                payload = {t['name']: t.get('value', '') for t in inputs if 'name' in t}
                payload['viewPage'] = str(page)
                payload['viewRows'] = '0'
                payload['bbsMode'] = 'list'
                action_url = _attrs(attributes).get('action', '')
                response = _request(session, 'POST', urljoin(url, action_url), data=payload)
            else:
                response = _request(session, 'GET', url)
            markup = response.text
            if access is not None:
                access['list'] = '공개 목록 접근 성공'
            page_ids, page_dates = [], []
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', markup, re.S | re.I):
                cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S | re.I)
                if len(cells) < 2:
                    continue
                title_cell = cells[1]
                anchors = list(_links(title_cell))
                if not anchors:
                    continue
                href, title = anchors[0]
                registered = next((d for cell in cells[2:] if (d := extract_date_from_text(_text(cell)))), None)
                if not registered or not title:
                    continue
                raw_id = ''
                if is_nh:
                    match = re.search(r"fn_select_brdView\(['\"](\d+)", title_cell)
                    if not match:
                        continue
                    raw_id = match.group(1)
                    origin = urljoin(url, '/ecenter/bid/bidView.do') + '?intgBsnBlbdGrpC=BBSMSTR_000000000019&blbdSqno=' + raw_id
                    org = extract_org_from_title(title)
                elif is_nhit:
                    match = re.search(r'/675/(\d+)/artclView', href)
                    if not match:
                        continue
                    raw_id = match.group(1); origin = urljoin(url, href); org = '농협정보시스템'
                elif is_kb:
                    match = re.search(r'articleId=(\d+)', href)
                    if not match:
                        continue
                    raw_id = match.group(1)
                    origin = f'https://omoney.kbstar.com/quics?page=C018592&cc=b031439:b031439&boardId=648&compId=b031439&articleId={raw_id}&bbsMode=view'
                    org = 'KB국민은행'
                else:
                    if not href or href.startswith(('javascript:', '#')):
                        continue
                    origin = urljoin(url, href); raw_id = hashlib.sha256(origin.encode()).hexdigest()[:20]; org = site_name
                page_ids.append(raw_id); page_dates.append(registered)
                if raw_id in seen:
                    continue
                seen.add(raw_id)
                if start_date <= registered <= end_date and (not keyword or keyword.lower() in title.lower()):
                    found.append(_item(raw_id, _text(cells[0]), title, registered, site_name, origin, org))
            signature = tuple(page_ids)
            if not page_ids:
                break
            if page > 1 and signature == previous:
                if access is not None:
                    access['pagination'] = '목록 반복 감지: 추가 페이지 확인 필요'
                break
            previous = signature
            # 공지 고정행이 있을 수 있어 페이지의 모든 날짜가 범위보다 과거일 때만 중단한다.
            if page_dates and max(page_dates) < start_date:
                break
            if not (is_nh or is_nhit or is_kb):
                break
        else:
            if access is not None:
                access['pagination'] = '최대 페이지 제한 도달'
    return found


def handle_download_and_extract_zip(response, item_folder_path, default_name):
    disposition = response.headers.get('Content-Disposition', '')
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I) or re.search(r'filename=["\x27]?([^"\x27;]+)', disposition, re.I)
    name = unquote(match.group(1)).strip() if match else default_name
    path = Path(item_folder_path) / sanitize_filename(name)
    part = path.with_name(path.name + '.part')
    total = 0
    try:
        with part.open('wb') as file:
            for chunk in response.iter_content(8192):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise ValueError('다운로드 크기 제한 초과')
                file.write(chunk)
        kind = detect_format(part)
        if kind == 'unsupported':
            raise ValueError('첨부 대신 HTML/미지원 데이터 수신')
        if path.suffix.lower() in ('.file', '.part') or not path.suffix:
            path = path.with_suffix('.' + kind)
        os.replace(part, path)
        if kind == 'zip':
            extract_zip(path, path.parent)
        return str(path)
    finally:
        part.unlink(missing_ok=True)
        response.close()


def _attachments(markup, origin):
    host = urlparse(origin).hostname or ''
    files = []
    if 'nonghyup.com' in host:
        for attach, serial in re.findall(r"fn_egov_downFile\('([^']+)',\s*'([^']+)'\)", markup):
            files.append(('GET', urljoin(origin, f'/cmm/fms/FileDown.do?atchFileId={attach}&fileSn={serial}'), None, f'{attach}_{serial}.file'))
    elif 'kbstar.com' in host:
        for attributes, body in re.findall(r'<form\b([^>]*)>(.*?)</form>', markup, re.S | re.I):
            attrs = _attrs(attributes)
            if not attrs.get('name', '').startswith('frmDownload'):
                continue
            inputs = [_attrs(t) for t in re.findall(r'<input\b([^>]*)>', body, re.I)]
            payload = {t['name']: t.get('value', '') for t in inputs if 'name' in t}
            if not payload.get('_FILE_NAME'):
                continue
            files.append(('POST', urljoin(origin, attrs.get('action', '/quics?asfilecode=534213')), payload, payload.get('_FILE_NAME', 'attachment.file')))
    else:
        for href, label in _links(markup):
            if re.search(r'(fileDown|download|fileDownload|\.pdf(?:\?|$)|\.hwp[x]?(?:\?|$)|\.zip(?:\?|$)|\.docx(?:\?|$))', href, re.I):
                files.append(('GET', urljoin(origin, href), None, label if re.search(r'\.(pdf|hwp|hwpx|zip|docx)$', label, re.I) else 'attachment.file'))
    return files


def _collect_item(item, base_dir, selected, local_classifier, force_refresh,
                  selected_solutions=None, selected_work=None, include_uncertain=False, manual=None):
    origin = item['원본URL']
    result = classify_rules(item['제목'])
    manual = manual or {}
    if manual and not manual.get('collect', True):
        return None
    if not manual and not should_collect(result, selected):
        return None
    item['download_status'] = '실패'
    item['download_count'] = 0
    item['access'] = {'list': '성공', 'detail': '미확인', 'attachments': '미확인'}
    folder = Path(base_dir) / f"{sanitize_filename(item['수집사이트'])}_{item['게시물ID']}_{sanitize_filename(item['제목'])[:40]}"
    item['폴더경로'] = str(folder)
    item['classification_status'] = '수동 분류' if manual else '자동'
    with _session() as session:
        try:
            response = _request(session, 'GET', origin)
            markup = response.text
            item['access']['detail'] = '공개 상세 접근 성공'
            detail_text = _text(markup)
            item['detail_text'] = detail_text
            if manual:
                result = manual
            elif local_classifier:
                result = classify_local(item['제목'], detail_text)
            else:
                result = classify_rules(item['제목'], detail_text)
            item['notice_text'] = detail_text[:16000]
            if not manual and not should_collect(result, selected, selected_solutions, selected_work, include_uncertain):
                if result.get('certain'):
                    return None
                gaps=[]
                for label,key,choices in [('분야','tags',selected),('세부 항목','solution_tags',selected_solutions),('업무','work_tags',selected_work)]:
                    if choices and not set(result.get(key,[])) & set(choices):
                        gaps.append(label+': '+', '.join(choices))
                item.update(classification_status='판단 보류', download_status='미수집', analysis_status='미분석',
                            classification_reason='선택 조건 확인 부족 ('+' / '.join(gaps)+'). '+result['reason'], tags=result['tags'],
                            solution_tags=result['solution_tags'], work_tags=result['work_tags'])
                return item
            folder.mkdir(parents=True, exist_ok=True)
            files = _attachments(markup, origin)
            failures = []
            for method, file_url, payload, name in files:
                # 게시판과 동일 기관의 공개 다운로드만 허용한다.
                if urlparse(file_url).hostname != urlparse(origin).hostname:
                    failures.append('다른 사이트 첨부: 원본 확인 필요'); continue
                try:
                    file_response = _request(session, method, file_url, data=payload, stream=True)
                    handle_download_and_extract_zip(file_response, folder, name)
                    item['download_count'] += 1
                except Exception as exc:
                    failures.append(str(exc))
            if files:
                item['download_status'] = '부분 성공' if failures else '성공'
                item['access']['attachments'] = item['download_status']
            else:
                external = [href for href, label in _links(markup) if 'first-epro' in href or 'biss.' in href]
                item['download_status'] = '외부 시스템 확인 필요' if external else '첨부 없음'
                item['access']['attachments'] = item['download_status']
            item['download_errors'] = failures
        except Exception as exc:
            item['download_errors'] = [str(exc)]
            item['access']['detail'] = '접근 실패'
            item['classification_status'] = '수집 대기' if manual else '판단 보류'
            item['analysis_status'] = '미분석'
    item['tags'] = result['tags']
    item['solution_tags'] = result.get('solution_tags', [])
    item['work_tags'] = result.get('work_tags', [])
    item['classification_reason'] = result['reason']
    return item


def crawl_and_download_bids_by_date(url, base_dir, start_date, end_date, keyword='', site_name='미지정', selected_categories=None, local_classifier=True, force_refresh=False, metrics=None,
                                    selected_solutions=None, selected_work=None, include_uncertain=False):
    from classification import DEFAULT_CATEGORIES
    selected = DEFAULT_CATEGORIES if selected_categories is None else selected_categories
    if not selected:
        return []
    started = time.monotonic()
    access = {}
    listed = list_bids(url, start_date, end_date, keyword, site_name, access=access)
    known = set() if force_refresh else existing_bid_ids(site_name, url)
    manual = manual_classifications(site_name, url)
    # 수동 확정 건은 별도 선택 실행으로만 처리한다. 일반 수집이 대기열을 소모하지 않는다.
    candidates = [i for i in listed if i['게시물ID'] not in known and i['게시물ID'] not in manual and should_collect(classify_rules(i['제목']), selected)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        collected = [r for r in pool.map(lambda i: _collect_item(i, base_dir, selected, local_classifier, force_refresh,
                         selected_solutions, selected_work, include_uncertain, manual.get(i['게시물ID'])), candidates) if r is not None]
    if metrics is not None:
        metrics.update(listed=len(listed), duplicate_skipped=sum(i['게시물ID'] in known for i in listed),
                       collected=sum(i.get('classification_status')!='판단 보류' for i in collected), downloaded_files=sum(i['download_count'] for i in collected),
                       pending_review=sum(i.get('classification_status')=='판단 보류' for i in collected),
                       crawl_seconds=round(time.monotonic()-started, 3), access=access)
    return collected


def collect_confirmed_bid(row, base_dir):
    """사용자가 확정한 한 공고만 처리한다. 목록·다른 공고는 조회하지 않는다."""
    manual = json.loads(row.get('manual_classification') or '{}')
    if not manual.get('collect') or row.get('classification_status') != '수집 대기':
        raise ValueError('수집 대상으로 확정한 대기 공고만 실행할 수 있습니다')
    item = {'게시물ID':row['bid_id'], '수집사이트':row['site_name'], '번호':row['num'],
            '제목':row['title'], '등록일':row['reg_date'], '계열사/조직':row['org'], '원본URL':row['origin_url']}
    item['manual_classification'] = manual
    return _collect_item(item,base_dir,manual['tags'],False,True,manual=manual)
