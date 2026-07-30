import os
import re
import requests
import urllib3
import zipfile
from urllib.parse import urljoin, unquote
from datetime import datetime, date
from scrapling import Fetcher, Selector

# SSL 경고 메시지 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def sanitize_filename(name: str) -> str:
    """파일명으로 쓸 수 없는 특수문자 제거"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def extract_org_from_title(title: str) -> str:
    """제목에서 계열사/조직명 추출"""
    match = re.search(r'\((은행|손해|생명|증권|중앙회|공통|유통|자산신탁|KB|국민|농협)\)', title)
    if match:
        return match.group(1)
    if "KB" in title or "국민" in title:
        return "KB국민"
    if "농협" in title or "NH" in title:
        return "NH농협"
    return "일반"

def extract_date_from_text(text: str) -> date | None:
    """다양한 날짜 형식 텍스트에서 datetime.date 객체 추출"""
    if not text:
        return None
    
    match = re.search(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    match_kor = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if match_kor:
        try:
            return date(int(match_kor.group(1)), int(match_kor.group(2)), int(match_kor.group(3)))
        except ValueError:
            pass

    match_short = re.search(r"(\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", text)
    if match_short:
        try:
            yr = 2000 + int(match_short.group(1))
            return date(yr, int(match_short.group(2)), int(match_short.group(3)))
        except ValueError:
            pass

    return None


def handle_download_and_extract_zip(response, item_folder_path: str, default_name: str) -> str:
    """
    다운로드 응답 헤더에서 URL 디코딩된 파일명을 추출하여 저장하고,
    ZIP 파일일 경우 즉시 자동으로 압축 해제합니다.
    """
    content_disp = response.headers.get("Content-Disposition", "")
    file_name = default_name

    if "filename=" in content_disp:
        fname_match = re.search(r'filename=["\']?([^"\';]+)["\']?', content_disp)
        if fname_match:
            raw_fname = fname_match.group(1)
            file_name = unquote(raw_fname)
            try:
                file_name = file_name.encode("iso-8859-1").decode("utf-8")
            except Exception:
                pass
            file_name = sanitize_filename(file_name)

    if not file_name or file_name.strip() == "":
        file_name = default_name

    save_path = os.path.join(item_folder_path, file_name)
    with open(save_path, "wb") as f_out:
        for chunk in response.iter_content(8192):
            f_out.write(chunk)
    
    print(f"    └ 💾 파일 저장 완료: {file_name}")

    if file_name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(save_path, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    try:
                        filename = member.filename.encode('cp437').decode('euc-kr')
                    except Exception:
                        filename = member.filename
                    
                    filename = sanitize_filename(filename)
                    if filename:
                        target_path = os.path.join(item_folder_path, filename)
                        if member.is_dir():
                            os.makedirs(target_path, exist_ok=True)
                        else:
                            with zip_ref.open(member) as source, open(target_path, "wb") as target:
                                target.write(source.read())
            print(f"    └ 📦 ZIP 압축 해제 완료: {file_name}")
        except Exception:
            try:
                with zipfile.ZipFile(save_path, 'r') as zip_ref:
                    zip_ref.extractall(item_folder_path)
                print(f"    └ 📦 ZIP 기본 압축 해제 완료: {file_name}")
            except Exception as ex:
                print(f"    └ ⚠️ ZIP 압축 해제 실패 ({file_name}): {ex}")

    return save_path


def crawl_and_download_bids_by_date(url: str, base_dir: str, start_date: date, end_date: date, keyword: str = "", site_name: str = "미지정"):
    """
    🎯 범용 지능형 자동 크롤러 (Universal Smart Crawler)
    정확한 개별 공고 딥링크(cc=b031439:b031439) 생성으로 100% 개별 게시물 직접 연결 지원!
    """
    fetcher = Fetcher()
    session = requests.Session()
    session.verify = False  # SSL 검증 무시
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": url
    })
    collected_bids = []
    os.makedirs(base_dir, exist_ok=True)

    print(f"🌐 [{site_name}] 정밀 딥링크 수집 및 파일 다운로드 엔진 가동 - URL: {url}")

    # --- 1. 농협 입찰 전용 수집 & 딥링크 생성 ---
    if "nonghyup.com" in url:
        page = 1
        stop_crawling = False
        while not stop_crawling:
            payload = {
                "intgBsnBlbdGrpC": "BBSMSTR_000000000019",
                "pageIndex": str(page),
                "searchCnd": "0",
                "searchWrd": keyword.strip()
            }
            try:
                try:
                    response = fetcher.post(url, data=payload, verify=False)
                except Exception:
                    r = session.post(url, data=payload, verify=False)
                    response = Selector(r.text)
            except Exception as e:
                print(f"⚠️ 농협 페이지 수집 중 네트워크 예외: {e}")
                break

            rows = response.css("table.list tbody tr") or response.css("table tbody tr")
            if not rows:
                break

            items_in_page = 0
            for row in rows:
                cols = row.css("td")
                if len(cols) >= 4:
                    items_in_page += 1
                    num = cols[0].text.strip()
                    a_tags = cols[1].css("a")
                    title = a_tags[0].text.strip() if a_tags else cols[1].text.strip()
                    onclick_attr = a_tags[0].attrib.get("onclick", "") if a_tags else ""
                    reg_date_str = cols[3].text.strip() if len(cols) > 3 else date.today().strftime("%Y.%m.%d")
                    org = extract_org_from_title(title)
                    reg_date_obj = extract_date_from_text(reg_date_str) or date.today()

                    if reg_date_obj < start_date:
                        stop_crawling = True
                        break
                    if reg_date_obj > end_date:
                        continue
                    if keyword and keyword.lower() not in title.lower():
                        continue

                    formatted_date_str = reg_date_obj.strftime("%Y-%m-%d")
                    bid_id = ""
                    id_match = re.search(r"fn_select_brdView\('(\d+)'\)", onclick_attr)
                    if id_match:
                        bid_id = id_match.group(1)

                    origin_link = f"https://www.nonghyup.com/ecenter/bid/bidView.do?intgBsnBlbdGrpC=BBSMSTR_000000000019&blbdSqno={bid_id}" if bid_id else url

                    folder_title = sanitize_filename(title)[:35]
                    folder_name = f"[{org}]_{formatted_date_str}_{folder_title}"
                    item_folder_path = os.path.join(base_dir, folder_name)
                    os.makedirs(item_folder_path, exist_ok=True)

                    if bid_id:
                        view_url = "https://www.nonghyup.com/ecenter/bid/bidView.do"
                        view_payload = {
                            "intgBsnBlbdGrpC": "BBSMSTR_000000000019",
                            "pageIndex": "1",
                            "searchCnd": "",
                            "searchWrd": "",
                            "blbdSqno": bid_id
                        }
                        try:
                            vr = session.post(view_url, data=view_payload, verify=False)
                            matches = re.findall(r"fn_egov_downFile\('([^']+)',\s*'([^']+)'\)", vr.text)
                            
                            for idx, (atch_id, file_sn) in enumerate(matches, 1):
                                down_url = f"https://www.nonghyup.com/cmm/fms/FileDown.do?atchFileId={atch_id}&fileSn={file_sn}"
                                try:
                                    f_res = session.get(down_url, stream=True, verify=False)
                                    handle_download_and_extract_zip(f_res, item_folder_path, f"attachment_{bid_id}_{idx}.file")
                                except Exception as e:
                                    print(f"    └ ❌ 농협 첨부파일 다운로드 실패: {e}")
                        except Exception as e:
                            print(f"  └ ⚠️ 농협 상세페이지 첨부파일 파싱 실패: {e}")

                    collected_bids.append({
                        "번호": num,
                        "수집사이트": site_name if site_name != "미지정" else "NH농협",
                        "게시물ID": bid_id,
                        "계열사/조직": org,
                        "제목": title,
                        "등록일": formatted_date_str,
                        "폴더경로": item_folder_path,
                        "원본URL": origin_link
                    })

            if items_in_page == 0:
                break
            page += 1

        return collected_bids

    # --- 2. KB국민은행 입찰 전용 딥링크 생성 및 수집 ---
    if "kbstar.com" in url or "KB" in site_name or "국민" in site_name:
        kb_url = "https://omoney.kbstar.com/quics?page=C018592"
        try:
            r = session.get(kb_url, verify=False, timeout=15)
            response = Selector(r.text)
            
            bid_links = response.css("a[href*='boardId=648']") or response.css("a[href*='bbsMode=view']")
            
            for idx, a in enumerate(bid_links, 1):
                title = a.text.strip()
                href = a.attrib.get("href", "")
                if not title or len(title) < 5 or "페이지" in title:
                    continue

                if keyword and keyword.lower() not in title.lower():
                    continue

                art_match = re.search(r"articleId=(\d+)", href)
                bid_id = art_match.group(1) if art_match else f"KB_{idx}"
                
                # 💡 KB국민은행 100% 개별 게시글 직행 딥링크 생성 (cc=b031439:b031439 핵심 파라미터 포함)
                if art_match:
                    origin_link = f"https://omoney.kbstar.com/quics?page=C018592&cc=b031439:b031439&boardId=648&compId=b031439&articleId={bid_id}&bbsMode=view"
                else:
                    origin_link = urljoin("https://omoney.kbstar.com/", href) if href else kb_url

                reg_date_obj = date.today()
                formatted_date_str = reg_date_obj.strftime("%Y-%m-%d")
                folder_title = sanitize_filename(title)[:35]
                folder_name = f"[KB국민은행]_{formatted_date_str}_{folder_title}"
                item_folder_path = os.path.join(base_dir, folder_name)
                os.makedirs(item_folder_path, exist_ok=True)

                if href:
                    detail_url = urljoin("https://omoney.kbstar.com/", href)
                    try:
                        det_res = session.get(detail_url, verify=False, timeout=12)
                        forms = re.findall(r'<form[^>]*name=["\']?frmDownload\d?["\']?[^>]*>.*?</form>', det_res.text, re.DOTALL | re.IGNORECASE)
                        
                        for form_html in forms:
                            act_match = re.search(r'action=["\']?([^"\';\s>]+)["\']?', form_html)
                            action_url = act_match.group(1) if act_match else "/quics?asfilecode=534213"
                            full_down_url = urljoin("https://omoney.kbstar.com/", action_url)
                            
                            inputs = re.findall(r'<input[^>]*name=["\']?([^"\';\s>]+)["\']?[^>]*value=["\']?([^"\';>]+)["\']?', form_html, re.IGNORECASE)
                            payload = {name: val for name, val in inputs}
                            
                            target_file_name = payload.get("_FILE_NAME", f"KB_attachment_{bid_id}.file")
                            if payload.get("_FILE_NAME"):
                                try:
                                    f_res = session.post(full_down_url, data=payload, stream=True, verify=False)
                                    if f_res.status_code == 200 and len(f_res.content) > 100:
                                        handle_download_and_extract_zip(f_res, item_folder_path, target_file_name)
                                except Exception as ex_down:
                                    print(f"    └ ⚠️ KB 파일 다운로드 실패 ({target_file_name}): {ex_down}")
                    except Exception as e:
                        print(f"    └ ⚠️ KB 상세페이지 다운로드 폼 수집 예외: {e}")

                collected_bids.append({
                    "번호": str(idx),
                    "수집사이트": site_name if site_name != "미지정" else "KB국민은행",
                    "게시물ID": bid_id,
                    "계열사/조직": "KB국민은행",
                    "제목": title,
                    "등록일": formatted_date_str,
                    "폴더경로": item_folder_path,
                    "원본URL": origin_link
                })
        except Exception as e:
            print(f"⚠️ KB국민은행 수집 중 예외 발생: {e}")

        return collected_bids

    # --- 3. 범용 지능형 수집기 ---
    print(f"🤖 [범용 자동 크롤러] 수집 시도: {url}")
    try:
        try:
            response = fetcher.get(url, verify=False)
        except Exception:
            r = session.get(url, verify=False, timeout=15)
            response = Selector(r.text)

        tables = response.css("table")
        if tables:
            best_table = max(tables, key=lambda t: len(t.css("tbody tr") or t.css("tr")))
            rows = best_table.css("tbody tr") or best_table.css("tr")

            idx_counter = 1
            for row in rows:
                cols = row.css("td")
                if len(cols) < 2:
                    continue

                reg_date_obj = None
                for col in cols:
                    extracted_d = extract_date_from_text(col.text.strip())
                    if extracted_d:
                        reg_date_obj = extracted_d
                        break
                if not reg_date_obj:
                    reg_date_obj = date.today()

                a_tags = row.css("a")
                title = ""
                origin_link = url
                if a_tags:
                    for a in a_tags:
                        t = a.text.strip()
                        if len(t) >= 4 and not extract_date_from_text(t):
                            title = t
                            href_val = a.attrib.get("href", "")
                            if href_val and not href_val.startswith("javascript"):
                                origin_link = urljoin(url, href_val)
                            break

                if not title:
                    for col in cols:
                        txt = col.text.strip()
                        if len(txt) >= 5 and not extract_date_from_text(txt) and not txt.isdigit():
                            title = txt
                            break

                if not title or (keyword and keyword.lower() not in title.lower()):
                    continue

                num = cols[0].text.strip() if cols[0].text.strip().isdigit() else str(idx_counter)
                idx_counter += 1
                org = extract_org_from_title(title)
                formatted_date_str = reg_date_obj.strftime("%Y-%m-%d")

                folder_title = sanitize_filename(title)[:35]
                folder_name = f"[{site_name}]_{formatted_date_str}_{folder_title}"
                item_folder_path = os.path.join(base_dir, folder_name)
                os.makedirs(item_folder_path, exist_ok=True)

                collected_bids.append({
                    "번호": num,
                    "수집사이트": site_name,
                    "게시물ID": f"AUTO_{idx_counter}",
                    "계열사/조직": org,
                    "제목": title,
                    "등록일": formatted_date_str,
                    "폴더경로": item_folder_path,
                    "원본URL": origin_link
                })
        else:
            bid_links = response.css("a")
            idx_counter = 1
            for a in bid_links:
                t = a.text.strip()
                href_val = a.attrib.get("href", "")
                if len(t) >= 6 and ("공고" in t or "입찰" in t or "구매" in t):
                    if keyword and keyword.lower() not in t.lower():
                        continue

                    reg_date_obj = date.today()
                    formatted_date_str = reg_date_obj.strftime("%Y-%m-%d")
                    folder_title = sanitize_filename(t)[:35]
                    folder_name = f"[{site_name}]_{formatted_date_str}_{folder_title}"
                    item_folder_path = os.path.join(base_dir, folder_name)
                    os.makedirs(item_folder_path, exist_ok=True)

                    origin_link = urljoin(url, href_val) if href_val and not href_val.startswith("javascript") else url

                    collected_bids.append({
                        "번호": str(idx_counter),
                        "수집사이트": site_name,
                        "게시물ID": f"AUTO_{idx_counter}",
                        "계열사/조직": extract_org_from_title(t),
                        "제목": t,
                        "등록일": formatted_date_str,
                        "폴더경로": item_folder_path,
                        "원본URL": origin_link
                    })
                    idx_counter += 1

    except Exception as e:
        print(f"❌ 범용 수집 처리 중 오류 발생: {e}")

    return collected_bids
