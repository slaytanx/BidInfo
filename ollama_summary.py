import requests
import json
import re

def analyze_bid_with_ollama(
    title: str, 
    doc_text: str, 
    pri_keywords: str = "통신", 
    sec_keywords: str = "네트워크, 보안", 
    model_name: str = "gemma4:e4b-mlx"
) -> dict:
    """
    Ollama 로컬 LLM을 사용하여:
    1) 사업 요약
    2) 동적 1차/2차 키워드 기준 적합도 점수(0~100점) 및 이유
    3) 입찰 핵심 체크리스트 (마감일시, 제출방식, 필수서류, 자격요건) 추출
    """
    url = "http://localhost:11434/api/generate"
    truncated_text = doc_text[:4000] if doc_text else "본문 내용 없음 (제목만 참조)"
    
    prompt = f"""
당신은 ICT/IT 입찰 전문 평가위원입니다. 다음 입찰 공고 제목 및 첨부문서 내용을 분석해주세요.

[우리 회사 평가 기준]:
- 1차 핵심 분야 (가중치 높음): {pri_keywords}
- 2차 관련 분야 (가중치 보통): {sec_keywords}

[분석 대상 공고]:
- 공고 제목: {title}
- 문서 본문:
{truncated_text}

[작성 지침]:
1. [사업 요약]: 이 공고가 무슨 사업인지 2문장으로 간결하게 요약하세요.
2. [적합도 점수]: 우리 회사의 1차({pri_keywords}) 및 2차({sec_keywords}) 분야 연관성을 바탕으로 0점~100점 점수를 부여하세요. (1차 관련: 80~100점, 2차 관련: 50~79점, 무관: 0~30점)
3. [적합 사유]: 점수 산출 이유를 1문장으로 요약하세요.
4. [체크리스트 - 제출마감일시]: 서류/입찰 제출 마감 일시를 추출하세요. (없을 경우 '상세 서류 참조')
5. [체크리스트 - 제출방식]: 전자제출, 직접방문, 우편 등 제출 방식을 추출하세요.
6. [체크리스트 - 필수제출서류]: 제출해야 하는 주요 서류 목록을 콤마(,)로 구분하여 요약하세요.
7. [체크리스트 - 참가자격요건]: 핵심 자격 요건을 1문장으로 요약하세요.

[응답 양식]: 반드시 아래 형식으로만 작성하세요.
사업요약: <요약내용>
적합도점수: <숫자만>
적합사유: <사유내용>
제출마감일시: <마감일시>
제출방식: <제출방식>
필수제출서류: <서류목록>
참가자격요건: <자격요건>
    """
    
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False
    }
    
    summary = "요약 실패"
    fit_score = 0
    fit_reason = "평가 실패"
    deadline = "상세 서류 참조"
    submit_type = "전자/방문"
    required_docs = "제안서 등"
    qualifications = "상세 서류 참조"

    try:
        response = requests.post(url, json=payload, timeout=60)
        if response.status_code == 200:
            res_text = response.json().get("response", "").strip()
            
            summary_match = re.search(r"사업요약:\s*(.+?)(?=\n적합도점수:|\n적합사유:|$)", res_text, re.DOTALL)
            score_match = re.search(r"적합도점수:\s*(\d+)", res_text)
            reason_match = re.search(r"적합사유:\s*(.+?)(?=\n제출마감일시:|$)", res_text)
            deadline_match = re.search(r"제출마감일시:\s*(.+?)(?=\n제출방식:|$)", res_text)
            sub_type_match = re.search(r"제출방식:\s*(.+?)(?=\n필수제출서류:|$)", res_text)
            docs_match = re.search(r"필수제출서류:\s*(.+?)(?=\n참가자격요건:|$)", res_text)
            qual_match = re.search(r"참가자격요건:\s*(.+)", res_text)

            if summary_match: summary = summary_match.group(1).strip()
            if score_match: fit_score = int(score_match.group(1))
            if reason_match: fit_reason = reason_match.group(1).strip()
            if deadline_match: deadline = deadline_match.group(1).strip()
            if sub_type_match: submit_type = sub_type_match.group(1).strip()
            if docs_match: required_docs = docs_match.group(1).strip()
            if qual_match: qualifications = qual_match.group(1).strip()

    except Exception as e:
        summary = f"[분석 오류: {e}]"

    return {
        "summary": summary,
        "fit_score": fit_score,
        "fit_reason": fit_reason,
        "deadline": deadline,
        "submit_type": submit_type,
        "required_docs": required_docs,
        "qualifications": qualifications
    }

def generate_rfp_one_pager(title: str, doc_text: str, model_name: str = "gemma4:e4b-mlx") -> str:
    """
    제안요청서(RFP) 1장 분석 리포트 요약 생성
    (평가배점, 권장목차, 예상예산/기간, 핵심 유의사항)
    """
    if not doc_text or not doc_text.strip():
        return f"⚠️ **[{title}]** 공고는 첨부 서류(HWP, PDF)가 수집되지 않아 RFP 요약 리포트를 생성할 수 없습니다."

    url = "http://localhost:11434/api/generate"
    truncated_text = doc_text[:4500]

    prompt = f"""
당신은 최고의 IT 입찰 제안 컨설턴트입니다.
아래 첨부 서류 본문을 분석하여 제안팀이 바로 활용할 수 있는 **[제안요청서(RFP) 1장 요약 리포트]**를 작성해주세요.

[입찰 공고 정보]:
- 공고 제목: {title}
- 첨부 서류 본문:
{truncated_text}

[작성 항목]:
1. 💰 **예상 사업예산 및 수행기간**: (문서에 나온 정량 정보)
2. ⚖️ **평가 배점 비율**: (예: 기술평가 80점 : 가격평가 20점 등)
3. 📝 **제안서 권장 목차 (3~4개 핵심 단락)**
4. 🚨 **핵심 유의사항 및 체크 포인트**: (위약금, 필수 조건, 제출 방식 등)

깔끔한 마크다운 가독성 양식으로 작성해 주세요.
    """

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        if response.status_code == 200:
            return response.json().get("response", "").strip()
        return f"[RFP 리포트 생성 실패 코드: {response.status_code}]"
    except Exception as e:
        return f"[RFP 리포트 오류: {e}]"

def answer_bid_question(title: str, doc_text: str, question: str, model_name: str = "gemma4:e4b-mlx") -> str:
    """RAG 기반 질문 답변"""
    if not doc_text or not doc_text.strip():
        return f"⚠️ **[{title}] 공고는 수집된 첨부 서류(HWP, PDF 등)가 없습니다.**\n\n공고 제목만 존재하므로 파일 내부의 구체적인 제출 마감시간, 자격요건, 제출 서류 목록 등은 확인할 수 없습니다. 해당 기관 입찰 시스템에서 직접 서류를 확인해 주세요."

    url = "http://localhost:11434/api/generate"
    truncated_text = doc_text[:4000]

    prompt = f"""
당신은 엄격하고 정직한 입찰 문서 분석 AI입니다.
아래에 제공된 [실제 첨부 문서 본문] 내용에 기반해서만 사용자의 질문에 답변하십시오.

[입찰 공고 정보]:
- 공고 제목: {title}
- 실제 첨부 문서 본문:
{truncated_text}

[사용자 질문]: {question}

[답변 엄격 준수 지침]:
1. 오직 위 [실제 첨부 문서 본문]에 나와 있는 사실만 가지고 답변하세요.
2. 문서 본문에 나와 있지 않은 내용이나 추측은 절대로 지어내지 말고, "제시된 첨부 문서상에는 해당 내용이 명시되어 있지 않습니다"라고 솔직하게 답변하세요.
3. 한국어로 깔끔하고 명확하게 정리해서 답변하세요.
    """

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        if response.status_code == 200:
            return response.json().get("response", "").strip()
        return f"[Ollama 답변 실패 코드: {response.status_code}]"
    except Exception as e:
        return f"[질의응답 오류: {e}]"
