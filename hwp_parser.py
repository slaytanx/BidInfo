import os
import re
import zlib
import zipfile
import olefile
import xml.etree.ElementTree as ET
from pypdf import PdfReader

def extract_text_from_hwp(hwp_path: str) -> str:
    """HWP 5.0 파일에서 본문 텍스트 추출"""
    if not olefile.isOleFile(hwp_path):
        return ""

    text_chunks = []
    try:
        ole = olefile.OleFileIO(hwp_path)
        file_dir = ole.listdir()
        sections = [dirs for dirs in file_dir if dirs[0] == "BodyText"]
        
        for section in sections:
            data = ole.openstream(section).read()
            uncompressed_data = zlib.decompress(data, -15)
            
            i = 0
            section_text = []
            while i < len(uncompressed_data):
                char_code = uncompressed_data[i] | (uncompressed_data[i+1] << 8)
                if 32 <= char_code <= 65533 or char_code in (9, 10, 13):
                    section_text.append(chr(char_code))
                i += 2
            text_chunks.append("".join(section_text))

        ole.close()
    except Exception as e:
        print(f"HWP 파싱 오류 ({hwp_path}): {e}")
        
    return "\n".join(text_chunks)


def extract_text_from_pdf(pdf_path: str) -> str:
    """PDF 파일에서 본문 텍스트 추출"""
    text_chunks = []
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_chunks.append(t)
    except Exception as e:
        print(f"PDF 파싱 오류 ({pdf_path}): {e}")
        
    return "\n".join(text_chunks)


def extract_text_from_docx(docx_path: str) -> str:
    """DOCX 파일에서 본문 텍스트 추출 (표준 zip/xml 지원)"""
    try:
        with zipfile.ZipFile(docx_path, 'r') as zip_ref:
            xml_content = zip_ref.read('word/document.xml')
            tree = ET.fromstring(xml_content)
            texts = [node.text for node in tree.iter() if node.tag.endswith('}t') and node.text]
            return "\n".join(texts)
    except Exception as e:
        print(f"DOCX 파싱 오류 ({docx_path}): {e}")
        return ""


def extract_text_from_hwpx(hwpx_path: str) -> str:
    """HWPX 파일에서 본문 텍스트 추출"""
    try:
        texts = []
        with zipfile.ZipFile(hwpx_path, 'r') as zip_ref:
            section_files = [f for f in zip_ref.namelist() if f.startswith('Contents/section')]
            for sec_file in section_files:
                xml_content = zip_ref.read(sec_file)
                tree = ET.fromstring(xml_content)
                for elem in tree.iter():
                    if elem.text and elem.text.strip():
                        texts.append(elem.text.strip())
        return "\n".join(texts)
    except Exception as e:
        print(f"HWPX 파싱 오류 ({hwpx_path}): {e}")
        return ""


def extract_text_from_txt(txt_path: str) -> str:
    """TXT 파일에서 본문 텍스트 추출"""
    for encoding in ['utf-8', 'cp949', 'euc-kr']:
        try:
            with open(txt_path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return ""


def extract_zip(zip_path: str, extract_to: str):
    """ZIP 파일 자동 압축 해제 및 한글 파일명 깨짐 보정"""
    if not zip_path.lower().endswith('.zip'):
        return

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.infolist():
                try:
                    filename = member.filename.encode('cp437').decode('cp949')
                except (UnicodeEncodeError, UnicodeDecodeError):
                    filename = member.filename

                target_path = os.path.join(extract_to, filename)
                if member.is_dir():
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zip_ref.open(member) as source, open(target_path, "wb") as target:
                        target.write(source.read())
    except Exception as e:
        print(f"ZIP 압축 해제 오류 ({zip_path}): {e}")


def find_actual_folder_with_files(folder_path: str) -> str:
    """
    지정된 folder_path에 파일이 비어있을 경우,
    data 디렉토리 내에서 같은 공고명의 실제 파일이 보관된 폴더 경로를 지능 탐색합니다.
    """
    if os.path.exists(folder_path):
        # 내부에 문서 파일(pdf, hwp, docx 등)이 존재하는지 확인
        files = [f for f in os.listdir(folder_path) if any(f.lower().endswith(ext) for ext in ['.pdf', '.hwp', '.hwpx', '.docx', '.txt', '.zip'])]
        if files:
            return folder_path

    # 지능형 검색: folder_path의 공고명 부분 추출
    base_name = os.path.basename(folder_path)
    clean_keyword = re.sub(r'\[.*?\]|_\d{4}-\d{2}-\d{2}_', '', base_name).strip()[:15]

    data_dir = os.path.dirname(os.path.abspath(folder_path)) if os.path.isabs(folder_path) else "data"
    if not os.path.exists(data_dir):
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    if os.path.exists(data_dir):
        for candidate in os.listdir(data_dir):
            candidate_path = os.path.join(data_dir, candidate)
            if os.path.isdir(candidate_path) and clean_keyword and clean_keyword in candidate:
                c_files = [f for f in os.listdir(candidate_path) if any(f.lower().endswith(ext) for ext in ['.pdf', '.hwp', '.hwpx', '.docx', '.txt', '.zip'])]
                if c_files:
                    return candidate_path

    return folder_path


def process_folder_documents(folder_path: str) -> str:
    """
    폴더 내 모든 ZIP을 풀고, HWP, HWPX, PDF, DOCX, TXT 파일들의 텍스트를 하나로 통합
    (파일 탐색 지능형 Fallback 지원)
    """
    target_folder = find_actual_folder_with_files(folder_path)
    
    if not os.path.exists(target_folder):
        return ""

    # 1. ZIP 파일들 먼저 압축 해제
    for root, _, files in os.walk(target_folder):
        for file in files:
            if file.lower().endswith('.zip'):
                zip_full_path = os.path.join(root, file)
                extract_zip(zip_full_path, root)

    # 2. 문서 텍스트 추출 및 합치기
    combined_texts = []
    for root, _, files in os.walk(target_folder):
        for file in files:
            file_lower = file.lower()
            full_path = os.path.join(root, file)
            
            if file_lower.endswith('.hwp'):
                t = extract_text_from_hwp(full_path)
                if t.strip():
                    combined_texts.append(f"--- [파일명: {file}] ---\n{t}")
            elif file_lower.endswith('.hwpx'):
                t = extract_text_from_hwpx(full_path)
                if t.strip():
                    combined_texts.append(f"--- [파일명: {file}] ---\n{t}")
            elif file_lower.endswith('.pdf'):
                t = extract_text_from_pdf(full_path)
                if t.strip():
                    combined_texts.append(f"--- [파일명: {file}] ---\n{t}")
            elif file_lower.endswith('.docx'):
                t = extract_text_from_docx(full_path)
                if t.strip():
                    combined_texts.append(f"--- [파일명: {file}] ---\n{t}")
            elif file_lower.endswith('.txt'):
                t = extract_text_from_txt(full_path)
                if t.strip():
                    combined_texts.append(f"--- [파일명: {file}] ---\n{t}")

    return "\n\n".join(combined_texts)
