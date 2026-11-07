"""입찰 공고 엑셀(.xlsx) 및 제안 검토 보고서(MD/HTML) 내보내기 모듈."""
import html
import io
import json
import re
from datetime import date
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from date_utils import calculate_d_day


def _safe_str(val, default=""):
    return default if val is None else str(val)


def _safe_escape(val, default=""):
    return html.escape(_safe_str(val, default))


def _safe_md_cell(val, default=""):
    """마크다운 테이블 셀에서 파이프(|)와 줄바꿈을 안전하게 치환한다."""
    s = _safe_str(val, default).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return s.replace("|", "\\|").strip()


def _safe_json_list(val):
    if not val:
        return []
    if isinstance(val, list):
        return val
    try:
        data = json.loads(val)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _extract_evidence_field(evidence_val, field_name):
    """evidence 목록에서 특정 필드의 인용문 또는 사실을 추출한다."""
    items = _safe_json_list(evidence_val)
    for it in items:
        if isinstance(it, dict) and it.get("field") == field_name and it.get("quote"):
            return str(it["quote"]).strip()
    return "확인 불가"


def generate_excel_export(bids_data: list[dict]) -> bytes:
    """수집된 입찰 공고 목록을 서식이 적용된 전문 엑셀(.xlsx) 바이너리로 변환한다."""
    wb = Workbook()
    ws = wb.active
    ws.title = "입찰공고_목록"
    ws.views.sheetView[0].showGridLines = True

    headers = [
        "번호", "찜", "진행상태", "D-Day", "수집사이트", "발주기관", "입찰 공고명",
        "적합도", "등록일", "마감일시", "제출방식", "추정예산", "수행기간",
        "분야", "세부 솔루션", "업무", "필수 제출 서류", "참가 자격 요건",
        "AI 사업 요약", "적합 사유", "원본 링크"
    ]

    header_font = Font(name="맑은 고딕", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")  # 네이비
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9")
    )

    header_border = Border(
        left=Side(style="thin", color="1B365D"),
        right=Side(style="thin", color="1B365D"),
        top=Side(style="thin", color="1B365D"),
        bottom=Side(style="medium", color="0F243E")
    )

    # 1. 헤더 작성
    ws.row_dimensions[1].height = 28
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = header_border

    # 2. 데이터 스타일 정의
    base_font = Font(name="맑은 고딕", size=9)
    bold_font = Font(name="맑은 고딕", size=9, bold=True)
    link_font = Font(name="맑은 고딕", size=9, color="0563C1", underline="single")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # D-Day 하이라이트 스타일
    fill_dday = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    font_dday = Font(name="맑은 고딕", size=9, bold=True, color="9C0006")

    fill_urgent = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    font_urgent = Font(name="맑은 고딕", size=9, bold=True, color="9C6500")

    font_closed = Font(name="맑은 고딕", size=9, color="8A8A8A")

    # 적합도 점수 스타일
    fill_high_fit = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    font_high_fit = Font(name="맑은 고딕", size=9, bold=True, color="006100")

    fill_mid_fit = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    font_mid_fit = Font(name="맑은 고딕", size=9, color="1F497D")

    # 3. 데이터 행 작성
    for row_idx, row in enumerate(bids_data, start=2):
        ws.row_dimensions[row_idx].height = 24

        d_day_str = row.get("D-Day") or calculate_d_day(row.get("deadline", "") or "")
        star_str = "⭐" if row.get("starred") == 1 else "☆"
        fit_val = row.get("fit_score")
        fit_num = int(fit_val) if fit_val is not None and str(fit_val).isdigit() else None

        budget_str = row.get("budget") or _extract_evidence_field(row.get("evidence"), "budget")
        duration_str = row.get("duration") or _extract_evidence_field(row.get("evidence"), "duration")

        tags_str = ", ".join(_safe_json_list(row.get("tags")))
        sol_str = ", ".join(_safe_json_list(row.get("solution_tags")))
        work_str = ", ".join(_safe_json_list(row.get("work_tags")))

        origin_link = (row.get("origin_url") or "").strip()
        has_valid_link = origin_link.startswith(("http://", "https://"))

        values = [
            row.get("num") or (row_idx - 1),
            star_str,
            row.get("status") or "검토중",
            d_day_str,
            row.get("site_name") or "미지정",
            row.get("org") or "미지정",
            row.get("title") or "제목 없음",
            fit_num if fit_num is not None else "미분석",
            row.get("reg_date") or "",
            row.get("deadline") or "확인 불가",
            row.get("submit_type") or "확인 불가",
            budget_str,
            duration_str,
            tags_str,
            sol_str,
            work_str,
            row.get("required_docs") or "확인 불가",
            row.get("qualifications") or "확인 불가",
            row.get("summary") or "미분석",
            row.get("fit_reason") or "미분석",
            "바로가기" if has_valid_link else "-"
        ]

        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = base_font
            cell.border = thin_border

            # 기본 정렬
            if col_idx in (1, 2, 3, 4, 5, 6, 8, 9, 11, 21):
                cell.alignment = align_center
            else:
                cell.alignment = align_left

            # D-Day 강조
            if col_idx == 4:
                if "D-DAY" in d_day_str:
                    cell.fill = fill_dday
                    cell.font = font_dday
                elif re.search(r"D-[0-7]\b", d_day_str):
                    cell.fill = fill_urgent
                    cell.font = font_urgent
                elif "마감" in d_day_str:
                    cell.font = font_closed

            # 적합도 강조
            if col_idx == 8 and isinstance(val, int):
                cell.number_format = '#,##0"점"'
                if val >= 80:
                    cell.fill = fill_high_fit
                    cell.font = font_high_fit
                elif val >= 60:
                    cell.fill = fill_mid_fit
                    cell.font = font_mid_fit

            # 원본 링크 하이퍼링크 처리
            if col_idx == 21 and has_valid_link:
                cell.hyperlink = origin_link
                cell.font = link_font

    # 4. 틀 고정 (헤더 상단 고정) 및 자동 필터
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # 5. 열 너비 자동 최적화
    width_rules = {
        1: 8,    # 번호
        2: 6,    # 찜
        3: 11,   # 진행상태
        4: 12,   # D-Day
        5: 16,   # 수집사이트
        6: 14,   # 발주기관
        7: 42,   # 입찰 공고명
        8: 10,   # 적합도
        9: 12,   # 등록일
        10: 22,  # 마감일시
        11: 14,  # 제출방식
        12: 18,  # 추정예산
        13: 16,  # 수행기간
        14: 16,  # 분야
        15: 18,  # 세부 솔루션
        16: 14,  # 업무
        17: 35,  # 필수 제출 서류
        18: 35,  # 참가 자격 요건
        19: 45,  # AI 사업 요약
        20: 35,  # 적합 사유
        21: 11,  # 원본 링크
    }

    for col_idx, width in width_rules.items():
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def generate_bid_review_markdown(bid: dict) -> str:
    """단일 입찰 공고의 핵심 제안 검토 보고서를 Markdown 문서로 생성한다."""
    title = _safe_str(bid.get("title"), "입찰공고")
    org = _safe_str(bid.get("org"), "미지정")
    site = _safe_str(bid.get("site_name"), "미지정")
    reg_date = _safe_str(bid.get("reg_date"), "")
    deadline = _safe_str(bid.get("deadline"), "확인 불가")
    d_day = _safe_str(bid.get("D-Day")) or calculate_d_day(deadline)
    submit_type = _safe_str(bid.get("submit_type"), "확인 불가")
    fit_score = bid.get("fit_score")
    fit_text = f"{int(fit_score)}점" if fit_score is not None and str(fit_score).isdigit() else "미분석"
    status = _safe_str(bid.get("status"), "검토중")
    origin_raw = _safe_str(bid.get("origin_url"), "").strip()
    if origin_raw.startswith(("http://", "https://")):
        origin_link_md = f"[{_safe_md_cell(title)}]({origin_raw})"
    else:
        origin_link_md = "링크 없음"

    budget = _safe_str(bid.get("budget")) or _extract_evidence_field(bid.get("evidence"), "budget")
    duration = _safe_str(bid.get("duration")) or _extract_evidence_field(bid.get("evidence"), "duration")
    evaluation = _safe_str(bid.get("evaluation")) or _extract_evidence_field(bid.get("evidence"), "evaluation")

    tags = ", ".join(_safe_json_list(bid.get("tags"))) or "미지정"
    solutions = ", ".join(_safe_json_list(bid.get("solution_tags"))) or "미지정"
    work = ", ".join(_safe_json_list(bid.get("work_tags"))) or "미지정"

    summary = _safe_str(bid.get("summary"), "미분석")
    fit_reason = _safe_str(bid.get("fit_reason"), "미분석")
    required_docs = _safe_str(bid.get("required_docs"), "확인 불가")
    qualifications = _safe_str(bid.get("qualifications"), "확인 불가")

    # 서류 목록 체크박스화
    doc_items = [d.strip() for d in re.split(r"[,;\n/]+", required_docs) if d.strip()]
    if not doc_items:
        doc_checklist = "- [ ] 상세 서류 참조"
    else:
        doc_checklist = "\n".join(f"- [ ] {item}" for item in doc_items)

    evidence_list = _safe_json_list(bid.get("evidence"))
    evidence_rows = []
    field_korean = {
        "deadline": "마감일시", "submit_type": "제출방식", "required_docs": "제출서류",
        "qualifications": "참가자격", "budget": "사업예산", "duration": "수행기간",
        "evaluation": "평가방식", "cautions": "유의사항"
    }
    for ev in evidence_list:
        if isinstance(ev, dict) and ev.get("quote"):
            fld = field_korean.get(ev.get("field", ""), ev.get("field", ""))
            loc = _safe_md_cell(ev.get("location", "원문"))
            q = _safe_md_cell(ev.get("quote", ""))
            evidence_rows.append(f"| {fld} | {q} | {loc} |")

    evidence_table = "\n".join(evidence_rows) if evidence_rows else "| - | 원문 인용 근거가 없습니다. | - |"

    md = f"""# [제안 검토 보고서] {_safe_md_cell(title)}

**작성일자**: {date.today()} | **진행상태**: `{status}` | **발주기관**: `{_safe_md_cell(org)}` ({_safe_md_cell(site)})

---

## 1. 사업 기본 개요

| 항목 | 내용 |
| :--- | :--- |
| **공고명** | {_safe_md_cell(title)} |
| **발주기관** | {_safe_md_cell(org)} ({_safe_md_cell(site)}) |
| **공고 등록일** | {_safe_md_cell(reg_date)} |
| **제출 마감일시** | **{_safe_md_cell(deadline)} ({_safe_md_cell(d_day)})** |
| **제출 방식** | {_safe_md_cell(submit_type)} |
| **추정 사업예산** | {_safe_md_cell(budget)} |
| **사업 수행기간** | {_safe_md_cell(duration)} |
| **기술/가격 평가** | {_safe_md_cell(evaluation)} |
| **원문 공고 링크** | {origin_link_md} |

---

## 2. 비즈니스 적합도 & AI 평가

* **적합도 점수**: **{fit_text}**
* **적합 판단 사유**: {fit_reason}
* **사업 분류**:
  * **분야**: `{tags}`
  * **세부 솔루션**: `{solutions}`
  * **업무 유형**: `{work}`

---

## 3. 사업 핵심 요약 (RFP Summary)

> {summary}

---

## 4. 입찰 참여 자격 & 필수 제출 서류 체크리스트

### 4.1. 핵심 참가 자격 요건
{qualifications}

### 4.2. 필수 제출 서류 체크리스트
{doc_checklist}

---

## 5. 원문 인용 핵심 근거 (Evidence Grounding)

| 검증 항목 | 원문 발췌 인용문 (Quote) | 출처 위치 |
| :--- | :--- | :--- |
{evidence_table}

---
*본 검토 보고서는 입찰정보 수집·분석 AI 시스템에 의해 자동 생성되었습니다.*
"""
    return md


def generate_bid_review_html(bid: dict) -> str:
    """브라우저 열람 및 A4 인쇄(PDF 저장)에 최적화된 단일 HTML 보고서를 생성한다."""
    title = _safe_escape(bid.get("title"), "입찰공고")
    org = _safe_escape(bid.get("org"), "미지정")
    site = _safe_escape(bid.get("site_name"), "미지정")
    reg_date = _safe_escape(bid.get("reg_date"), "")
    deadline = _safe_escape(bid.get("deadline"), "확인 불가")
    d_day = _safe_escape(bid.get("D-Day") or calculate_d_day(bid.get("deadline") or ""))
    submit_type = _safe_escape(bid.get("submit_type"), "확인 불가")
    fit_score = bid.get("fit_score")
    fit_text = f"{int(fit_score)}점" if fit_score is not None and str(fit_score).isdigit() else "미분석"
    status = _safe_escape(bid.get("status"), "검토중")
    origin_raw = _safe_str(bid.get("origin_url"), "").strip()
    if origin_raw.startswith(("http://", "https://")):
        origin_escaped = html.escape(origin_raw)
        origin_link_html = f'<a href="{origin_escaped}" target="_blank">{origin_escaped}</a>'
    else:
        origin_link_html = '<span style="color:#888;">링크 없음</span>'

    budget = _safe_escape(bid.get("budget") or _extract_evidence_field(bid.get("evidence"), "budget"))
    duration = _safe_escape(bid.get("duration") or _extract_evidence_field(bid.get("evidence"), "duration"))
    evaluation = _safe_escape(bid.get("evaluation") or _extract_evidence_field(bid.get("evidence"), "evaluation"))

    tags = _safe_escape(", ".join(_safe_json_list(bid.get("tags"))) or "미지정")
    solutions = _safe_escape(", ".join(_safe_json_list(bid.get("solution_tags"))) or "미지정")
    work = _safe_escape(", ".join(_safe_json_list(bid.get("work_tags"))) or "미지정")

    summary = _safe_escape(bid.get("summary"), "미분석")
    fit_reason = _safe_escape(bid.get("fit_reason"), "미분석")
    required_docs = _safe_str(bid.get("required_docs"), "확인 불가")
    qualifications = _safe_escape(bid.get("qualifications"), "확인 불가")

    doc_items = [d.strip() for d in re.split(r"[,;\n/]+", required_docs) if d.strip()]
    if not doc_items:
        checklist_html = "<li><input type='checkbox' disabled> 상세 서류 참조</li>"
    else:
        checklist_html = "".join(f"<li><input type='checkbox'> {html.escape(item)}</li>" for item in doc_items)

    evidence_list = _safe_json_list(bid.get("evidence"))
    field_korean = {
        "deadline": "마감일시", "submit_type": "제출방식", "required_docs": "제출서류",
        "qualifications": "참가자격", "budget": "사업예산", "duration": "수행기간",
        "evaluation": "평가방식", "cautions": "유의사항"
    }
    evidence_rows_html = ""
    for ev in evidence_list:
        if isinstance(ev, dict) and ev.get("quote"):
            fld = html.escape(field_korean.get(ev.get("field", ""), ev.get("field", "")))
            loc = html.escape(ev.get("location", "원문"))
            q = html.escape(ev.get("quote", "").replace("\n", " "))
            evidence_rows_html += f"<tr><td><strong>{fld}</strong></td><td>{q}</td><td class='loc'>{loc}</td></tr>"

    if not evidence_rows_html:
        evidence_rows_html = "<tr><td colspan='3' style='text-align:center; color:#888;'>원문 인용 근거가 없습니다.</td></tr>"

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>제안 검토 보고서 - {title}</title>
<style>
  @page {{ size: A4; margin: 15mm; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Malgun Gothic", "맑은 고딕", Roboto, sans-serif;
    color: #1a1a1a;
    line-height: 1.55;
    margin: 0;
    padding: 24px;
    background-color: #f8fafc;
  }}
  .container {{
    max-width: 900px;
    margin: 0 auto;
    background: #ffffff;
    padding: 36px 42px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.06);
  }}
  .header {{
    border-bottom: 2px solid #1f497d;
    padding-bottom: 16px;
    margin-bottom: 24px;
  }}
  .badge {{
    display: inline-block;
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 700;
    border-radius: 4px;
    background: #e2e8f0;
    color: #334155;
    margin-right: 6px;
  }}
  .badge-status {{ background: #dbeafe; color: #1e40af; }}
  .badge-dday {{ background: #fee2e2; color: #991b1b; }}
  .badge-score {{ background: #dcfce7; color: #166534; font-size: 13px; }}
  h1 {{
    font-size: 20px;
    color: #0f172a;
    margin: 10px 0 8px 0;
    line-height: 1.35;
  }}
  .meta {{
    font-size: 12px;
    color: #64748b;
  }}
  h2 {{
    font-size: 14px;
    color: #1f497d;
    border-left: 4px solid #1f497d;
    padding-left: 8px;
    margin: 24px 0 12px 0;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    margin-bottom: 12px;
  }}
  th, td {{
    padding: 8px 10px;
    border: 1px solid #e2e8f0;
    text-align: left;
    vertical-align: middle;
  }}
  th {{
    background-color: #f1f5f9;
    color: #334155;
    font-weight: 600;
    width: 18%;
  }}
  .quote-box {{
    background-color: #f8fafc;
    border: 1px solid #e2e8f0;
    border-left: 4px solid #3b82f6;
    padding: 12px 16px;
    font-size: 13px;
    border-radius: 4px;
    margin-bottom: 16px;
    color: #334155;
  }}
  ul.checklist {{
    list-style: none;
    padding-left: 0;
    font-size: 12.5px;
  }}
  ul.checklist li {{
    padding: 4px 0;
    display: flex;
    align-items: center;
  }}
  ul.checklist input[type="checkbox"] {{
    margin-right: 8px;
  }}
  .loc {{
    color: #64748b;
    font-size: 11px;
    white-space: nowrap;
    width: 18%;
  }}
  .footer {{
    margin-top: 32px;
    padding-top: 12px;
    border-top: 1px solid #e2e8f0;
    font-size: 11px;
    color: #94a3b8;
    text-align: right;
  }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .container {{ box-shadow: none; padding: 0; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <span class="badge badge-status">상태: {status}</span>
      <span class="badge badge-dday">{d_day}</span>
      <span class="badge badge-score">적합도: {fit_text}</span>
    </div>
    <h1>{title}</h1>
    <div class="meta">
      발주기관: <strong>{org}</strong> ({site}) &nbsp;|&nbsp; 공고등록일: {reg_date} &nbsp;|&nbsp; 보고서 생성일: {date.today()}
    </div>
  </div>

  <h2>1. 입찰 개요 및 기본 조건</h2>
  <table>
    <tr>
      <th>마감일시</th>
      <td><strong>{deadline} ({d_day})</strong></td>
      <th>제출방식</th>
      <td>{submit_type}</td>
    </tr>
    <tr>
      <th>추정예산</th>
      <td>{budget}</td>
      <th>수행기간</th>
      <td>{duration}</td>
    </tr>
    <tr>
      <th>평가방식</th>
      <td colspan="3">{evaluation}</td>
    </tr>
    <tr>
      <th>사업분야</th>
      <td>{tags}</td>
      <th>솔루션 / 업무</th>
      <td>{solutions} / {work}</td>
    </tr>
    <tr>
      <th>원본게시글</th>
      <td colspan="3">{origin_link_html}</td>
    </tr>
  </table>

  <h2>2. AI 비즈니스 분석 및 사업 요약</h2>
  <div class="quote-box">
    <strong>[사업 핵심 요약]</strong><br>
    {summary}
  </div>
  <table>
    <tr>
      <th>적합 판단 근거</th>
      <td>{fit_reason}</td>
    </tr>
    <tr>
      <th>핵심 참가 자격</th>
      <td>{qualifications}</td>
    </tr>
  </table>

  <h2>3. 필수 제출 서류 체크리스트</h2>
  <ul class="checklist">
    {checklist_html}
  </ul>

  <h2>4. 원문 인용 핵심 근거 (Evidence Grounding)</h2>
  <table>
    <thead>
      <tr style="background:#f1f5f9;">
        <th style="width:16%;">항목</th>
        <th>원문 발췌 인용문 (Quote)</th>
        <th class="loc">출처 위치</th>
      </tr>
    </thead>
    <tbody>
      {evidence_rows_html}
    </tbody>
  </table>

  <div class="footer">
    자동 생성: 입찰정보 수집·분석 AI 시스템 (BidInfo) &nbsp;|&nbsp; {date.today()}
  </div>
</div>
</body>
</html>
"""
    return html_content
