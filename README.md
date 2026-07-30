# 🏛️ 입찰공고 자동 크롤링 & Ollama 로컬 AI 사업 분석 시스템

본 프로젝트는 **Scrapling**을 이용하여 입찰 공고 목록 및 첨부파일을 수집하고, 게시물별 하위 폴더 자동 정리, HWP/PDF 문서 텍스트 추출, 그리고 **로컬 Ollama LLM**을 이용한 사업 요약을 거쳐 CSV로 저장하는 시스템입니다.

## 📂 저장 위치
`/Users/ygyoo/Documents/Code/BidInfo`

## 🛠️ 주요 기능
1. **Scrapling 수집 및 게시물별 폴더 자동 생성**: `data/[계열사]_[등록일]_[게시물명]/` 하위 폴더에 파일 저장.
2. **ZIP 파일 자동 압축 해제**: 다운로드받은 추가 첨부파일 `.zip` 한글 깨짐 없이 자동 해제.
3. **HWP / PDF 텍스트 추출**: 별도 유료 API 없이 로컬 파이썬 모듈(`olefile`, `pypdf`)로 텍스트 자동 추출.
4. **Ollama 로컬 LLM 사업 요약**: 로컬에 동작하는 Ollama (`qwen2.5` / `llama3` 등)를 호출하여 2~3문장 사업 요약 작성.
5. **CSV 생성**: `사업요약` 및 `계열사/조직` 컬럼이 포함된 최종 CSV 추출.

## 🚀 실행 가이드

### 1. 패키지 설치
```bash
cd /Users/ygyoo/Documents/Code/BidInfo
uv venv
uv add scrapling olefile pypdf pandas requests
```

### 2. Ollama 로컬 모델 준비 (터미널)
```bash
ollama pull qwen2.5
```

### 3. 프로그램 실행
```bash
uv run python main.py
```
