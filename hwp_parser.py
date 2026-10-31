"""형식을 확인하고 위치 근거를 보존하는 문서 추출기."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
import xml.etree.ElementTree as ET
import olefile
import pandas as pd

PARSER_VERSION = '6'
CACHE_DIR = Path(__file__).parent / 'data/cache/documents'
MAX_FILE_BYTES = 150 * 1024 * 1024
MAX_ZIP_BYTES = 500 * 1024 * 1024


def _hwp_paragraph(data):
    words, pos = [], 0
    while pos + 1 < len(data):
        code = struct.unpack_from('<H', data, pos)[0]
        if code in (9, 10, 13):
            words.append('\t' if code == 9 else '\n')
        elif code >= 32:
            words.append(chr(code))
        # HWP 확장 제어는 8 WORD이며 문단 끝 등은 단일 WORD다.
        pos += 16 if code in (*range(1, 10), 11, 12, *range(14, 24)) else 2
    return ''.join(words).encode('utf-16-le', errors='surrogatepass').decode('utf-16-le', errors='replace')


def parse_hwp_records(data):
    paragraphs, pos = [], 0
    while pos + 4 <= len(data):
        header = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        tag, size = header & 1023, header >> 20
        if size == 4095:
            if pos + 4 > len(data):
                raise ValueError('확장 레코드 길이가 잘렸습니다')
            size = struct.unpack_from('<I', data, pos)[0]
            pos += 4
        if pos + size > len(data):
            raise ValueError('본문 레코드가 잘렸습니다')
        if tag == 67:
            paragraphs.append(_hwp_paragraph(data[pos:pos + size]))
        pos += size
    return '\n'.join(paragraphs)


def _hwp_segments(path):
    segments, errors = [], []
    with olefile.OleFileIO(str(path)) as ole:
        header = ole.openstream('FileHeader').read()
        if len(header) < 40:
            raise ValueError('HWP 헤더 손상')
        flags = struct.unpack_from('<I', header, 36)[0]
        if flags & 2:
            raise ValueError('암호화된 HWP: 원본에서 암호를 해제해 주세요')
        sections = sorted((s for s in ole.listdir() if s[0] == 'BodyText'), key=lambda s: int(re.search(r'\d+', s[-1]).group()))
        if not sections:
            raise ValueError('HWP 본문 없음: 배포용 또는 미지원 문서')
        for section in sections:
            try:
                data = ole.openstream(section).read()
                if flags & 1:
                    inflater = zlib.decompressobj(-15)
                    data = inflater.decompress(data, MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES or inflater.unconsumed_tail:
                        raise ValueError('HWP 압축 해제 제한 초과')
                segments.append({'location': section[-1], 'text': parse_hwp_records(data)})
            except (ValueError, zlib.error, OSError) as exc:
                errors.append(f'{section[-1]}: {exc}')
    return segments, errors, 'HWP 레코드'


def _tag(node):
    return node.tag.split('}')[-1]


def _md_table(tbl):
    # shortcut: 병합 셀(colSpan)은 펼치지 않아 열이 밀릴 수 있음, 실제 공고에서 문제되면 cellSpan 반영
    rows = [[' '.join(''.join(''.join(t.itertext()) for t in tc.iter() if _tag(t) == 't').split()).replace('|', '\\|')
             for tc in tr if _tag(tc) == 'tc'] for tr in tbl if _tag(tr) == 'tr']
    rows = [r for r in rows if r]
    if not rows:
        return ''
    width = max(map(len, rows))
    lines = ['| ' + ' | '.join(r + [''] * (width - len(r))) + ' |' for r in rows]
    lines.insert(1, '|' + ' --- |' * width)
    return '\n'.join(lines)


def _hwpx_blocks(node, blocks, buffer):
    """문단과 표를 원문 순서대로 모은다. 표 안 문단은 셀 텍스트로만 쓴다."""
    def flush():
        blocks.append(''.join(buffer).strip())
        buffer.clear()

    for child in node:
        tag = _tag(child)
        if tag == 'tbl':
            flush()
            blocks.append(_md_table(child))
        elif tag == 't':
            buffer.append(''.join(child.itertext()))
        else:
            _hwpx_blocks(child, blocks, buffer)
            if tag == 'p':
                flush()


def _hwpx_segments(path):
    segments = []
    with zipfile.ZipFile(path) as archive:
        names = sorted((n for n in archive.namelist() if re.fullmatch(r'Contents/section\d+\.xml', n)), key=lambda n: int(re.search(r'\d+', n).group()))
        for name in names:
            blocks = []
            _hwpx_blocks(ET.fromstring(archive.read(name)), blocks, [])
            segments.append({'location': name, 'text': '\n\n'.join(b for b in blocks if b)})
    return segments, [], 'HWPX 마크다운'


def _pandoc_segments(path, kind):
    if not shutil.which('pandoc'):
        raise RuntimeError('pandoc 미설치')
    proc = subprocess.run(['pandoc', '-f', kind, '-t', 'gfm', '--wrap=none', str(path)], capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode:
        raise ValueError(f'pandoc 변환 실패: {proc.stderr.strip()[:200]}')
    return [{'location': '본문', 'text': proc.stdout}], [], 'pandoc'


def _excel_segments(path, kind):
    # 머리글 행이 제목·병합 셀인 내역서가 많아 header=None으로 원본 행을 그대로 둔다.
    sheets = pd.read_excel(path, sheet_name=None, header=None, engine='openpyxl' if kind == 'xlsx' else 'xlrd')
    segments = []
    for name, frame in sheets.items():
        frame = frame.dropna(how='all').dropna(axis=1, how='all')
        if not frame.empty:
            segments.append({'location': f'시트: {name}', 'text': '```csv\n' + frame.to_csv(index=False, header=False) + '```'})
    return segments, [], 'Excel CSV'


def _quality(text):
    return len(re.findall(r'[가-힣A-Za-z0-9]', text)) >= 30


def _pdf_segments(path, ocr=True):
    import pymupdf
    import pymupdf4llm
    ocr = ocr and bool(shutil.which('tesseract'))
    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass and not doc.authenticate(''):
            raise ValueError('암호화된 PDF')
        scanned = [not _quality(page.get_text()) for page in doc]
        chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, use_ocr=ocr, ocr_language='kor+eng')
    segments, errors = [], []
    for chunk in chunks:
        number = chunk['metadata']['page_number']
        if not _quality(chunk['text']):
            errors.append(f'p.{number}: 텍스트 부족; 원본 확인 필요')
        segments.append({'location': f'p.{number}', 'text': chunk['text'], 'method': 'OCR' if ocr and scanned[number - 1] else 'PDF 텍스트'})
    return segments, errors, 'PDF + OCR' if ocr and any(scanned) else 'PDF 텍스트'


def detect_format(path):
    path = Path(path)
    with path.open('rb') as file:
        magic = file.read(8)
    if magic.startswith(b'%PDF'):
        return 'pdf'
    if magic.startswith(b'\xd0\xcf\x11\xe0'):
        with olefile.OleFileIO(str(path)) as ole:
            return 'hwp' if ole.exists('FileHeader') else 'xls' if ole.exists('Workbook') else 'doc'
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if 'word/document.xml' in names:
                return 'docx'
            if any(re.fullmatch(r'Contents/section\d+\.xml', n) for n in names):
                return 'hwpx'
            if 'xl/workbook.xml' in names:
                return 'xlsx'
            if 'ppt/presentation.xml' in names:
                return 'pptx'
        return 'zip'
    suffix = path.suffix.lower()
    return 'txt' if suffix == '.txt' else 'html' if suffix in ('.html', '.htm') else 'unsupported'


def extract_document(path, ocr=True, use_cache=True):
    path = Path(path)
    result = {'file': path.name, 'status': '실패', 'method': '', 'segments': [], 'errors': [], 'text': '', 'cache_hit': False}
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('파일 크기 제한 초과')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = hashlib.sha256(f'{digest}:{PARSER_VERSION}:{ocr}'.encode()).hexdigest()
        cache = CACHE_DIR / f'{key}.json'
        if use_cache and cache.exists():
            try:
                saved = json.loads(cache.read_text())
                if saved.get('status') == '성공' and isinstance(saved.get('text'), str):
                    saved.update(file=path.name, cache_hit=True)
                    return saved
            except (OSError, ValueError):
                pass
        kind = detect_format(path)
        if kind in ('hwpx', 'docx', 'xlsx', 'pptx'):
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > MAX_ZIP_BYTES:
                    raise ValueError('문서 압축 해제 크기 제한 초과')
        if kind == 'hwp':
            segments, errors, method = _hwp_segments(path)
        elif kind == 'pdf':
            segments, errors, method = _pdf_segments(path, ocr)
        elif kind == 'hwpx':
            segments, errors, method = _hwpx_segments(path)
        elif kind in ('docx', 'pptx', 'html'):
            segments, errors, method = _pandoc_segments(path, kind)
        elif kind in ('xlsx', 'xls'):
            segments, errors, method = _excel_segments(path, kind)
        elif kind == 'doc':
            raise ValueError('구형 DOC: DOCX로 변환해 주세요')
        elif kind == 'txt':
            segments, errors, method = [{'location': '본문', 'text': extract_text_from_txt(str(path))}], [], '텍스트'
        else:
            raise ValueError('미지원 형식 또는 문서가 아닌 다운로드 응답')
        text = '\n\n'.join(f"[{s['location']}]\n{s['text']}" for s in segments if s['text'].strip())
        status = '부분 성공' if text and errors else '성공' if text else '실패'
        result.update(status=status, segments=segments, errors=errors, method=method, text=text, hash=digest)
        if not text:
            result['errors'].append('추출 가능한 본문 없음')
        if use_cache and status == '성공':
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', dir=CACHE_DIR, delete=False, encoding='utf-8') as file:
                json.dump(result, file, ensure_ascii=False)
            os.replace(file.name, cache)
    except Exception as exc:
        result['errors'].append(str(exc))
    return result


def extract_zip(zip_path, extract_to):
    base = Path(extract_to).resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(m.file_size for m in members) > MAX_ZIP_BYTES:
            raise ValueError('ZIP 압축 해제 제한 초과')
        targets = []
        for member in members:
            name = member.filename
            if not member.flag_bits & 0x800:
                try:
                    name = name.encode('cp437').decode('cp949')
                except UnicodeError:
                    pass
            target = (base / name.replace('\\', '/')).resolve()
            if not target.is_relative_to(base) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ZIP 경로 이탈 또는 심볼릭 링크')
            if member.file_size > MAX_FILE_BYTES or member.file_size / max(member.compress_size, 1) > 1000:
                raise ValueError('ZIP 파일 크기/압축률 제한 초과')
            targets.append((member, target))
        for member, target in targets:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                crc = 0
                if target.is_file() and target.stat().st_size == member.file_size:
                    with target.open('rb') as existing:
                        for block in iter(lambda: existing.read(65536), b''):
                            crc = zlib.crc32(block, crc)
                    if crc == member.CRC:
                        continue
                temporary = None
                try:
                    with archive.open(member) as source, tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as dest:
                        temporary = Path(dest.name)
                        shutil.copyfileobj(source, dest)
                    os.replace(temporary, target)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)


def process_folder_result(folder_path, ocr=True):
    folder = Path(folder_path)
    if not folder.is_dir():
        return {'text': '', 'files': [], 'status': '실패', 'errors': ['첨부 폴더 없음']}
    errors, seen = [], set()
    for _ in range(3):
        archives = [p for p in folder.rglob('*') if p.is_file() and p not in seen and p.suffix.lower() == '.zip']
        if not archives:
            break
        for path in archives:
            seen.add(path)
            try:
                extract_zip(path, path.parent)
            except Exception as exc:
                errors.append(f'{path.name}: {exc}')
    if any(p not in seen for p in folder.rglob('*.zip')):
        errors.append('중첩 ZIP 깊이 제한 초과')
    results, digests = [], set()
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.suffix.lower() in ('.zip', '.json', '.db'):
            continue
        result = extract_document(path, ocr)
        if result.get('hash') in digests:
            continue
        if result.get('hash'):
            digests.add(result['hash'])
        results.append(result)
    text = '\n\n'.join(f"[파일: {r['file']}]\n{r['text']}" for r in results if r['text'])
    if not results:
        errors.append('읽을 첨부문서 없음: 원본 공고 확인 필요')
    failed = errors or any(r['status'] != '성공' for r in results)
    return {'text': text, 'files': results, 'errors': errors, 'status': '부분 성공' if text and failed else '성공' if text else '실패'}


def process_folder_documents(folder_path):
    return process_folder_result(folder_path)['text']


def extract_text_from_txt(path):
    data = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp949', 'euc-kr'):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError('텍스트 인코딩 확인 필요')
