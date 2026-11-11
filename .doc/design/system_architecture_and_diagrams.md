# BidInfo 시스템 구성 및 플로우 다이어그램 명세서

최신 업데이트: 2026-11-09
문서 목적: BidInfo(입찰정보 수집·분석 시스템)의 소스코드 및 명세서 최신화 내역을 반영하여, 5계층 아키텍처, 엔드투엔드 파이프라인, 지능형 파서 분기, LLM 사실 검증 및 Q&A 워크플로우, 상태 전이 머신, 데이터베이스 ERD 및 리포트 모델을 체계화하여 정의합니다.

---

## 1. 전체 시스템 구성도 (System Architecture & Component Map)

BidInfo 시스템은 프론트엔드 UI(Streamlit), 파이프라인 오케스트레이션 및 트래픽 제어, 수집 및 다중 포맷 파싱 엔진, AI 분류 및 듀얼 LLM 분석 엔진, SQLite/R2 스토리지 레이어로 구성된 5계층 아키텍처를 가집니다.

```mermaid
flowchart TD
    subgraph UI_Layer ["1. 프론트엔드 UI 레이어 (Streamlit Multi-Tab)"]
        UI_Main["메인 대시보드 (app.py)<br/>• 공고 목록/필터링<br/>• 분석 요약/체크리스트<br/>• 서류 Q&A 챗봇<br/>• 분류 검토 탭"]
        UI_Export["보고서 내보내기 (report_export.py)<br/>• 21개 컬럼 스타일 엑셀 (.xlsx)<br/>• 메모리 캐싱 (cached_filtered_excel)<br/>• 제안 검토 보고서 (.md / .html)"]
        UI_Admin["관리자 콘솔 (admin_console.py)<br/>• 모델/제공자 설정<br/>• R2 동기화 / 캐시 관리<br/>• 재처리 및 벤치마크"]
        UI_Taxonomy["분류 사전 관리자 (taxonomy_ui.py)<br/>• 3축 키워드/동의어 편집<br/>• 수동 검토 대기열"]
    end

    subgraph Orchestration_Layer ["2. 파이프라인 및 트래픽 제어 레이어"]
        Pipeline["파이프라인 오케스트레이터 (pipeline.py)<br/>• 멀티스레드 배치 작업 제어<br/>• 실시간 st.status UI 콜백"]
        RateLimiter["도메인별 트래픽 제어기 (crawler.py)<br/>• 호스트당 최소 0.3초 대기<br/>• 지수 백오프 & Retry-After 헤더 대응"]
        StampCache["캐시 무효화 핑거프린트 (db_stamp)<br/>• DB/WAL 파일 mtime_ns & size 결합 추적"]
    end

    subgraph Ingestion_Parsing_Layer ["3. 수집 및 파싱 엔진"]
        Crawler["금융권 웹 크롤러 (crawler.py)<br/>• curl_cffi Chrome124 TLS 위장<br/>• 금융 5개사 전용 핸들러<br/>• 한글 파일명 복원 (decode_http_filename)<br/>• 확정 공고 단독 수집 (collect_confirmed_bid)<br/>• 안전 ZIP 해제 (Zip-Bomb/Traversal 방지)"]
        Parser["다중 포맷 파싱 엔진 (hwp_parser.py)<br/>• HWP 5.x OLE / HWPX Open Ant XML<br/>• PyMuPDF 구조화 마크다운 + 표 복원<br/>• DOCX, XLSX, Pandoc (PPTX/HTML)<br/>• 단순 서약서 제외 & RFP 보호 화이트리스트"]
        OCR["선별적 OCR 엔진<br/>• Tesseract OCR (kor+eng)<br/>• 저밀도 스캔 페이지만 선별 처리"]
    end

    subgraph AI_Analysis_Layer ["4. AI 분류 및 심층 분석 엔진"]
        Classifier["분류기 (classification.py)<br/>• 3축 규칙 분류 (classify_rules)<br/>• 명백한 비IT 조기 배제<br/>• 로컬 Ollama 1차 사전 분류"]
        TaxonomyMgr["3축 택소노미 엔진 (taxonomy.py)<br/>• 분야 / 세부 솔루션 / 업무 유형<br/>• 축 내 OR, 축 간 AND 매칭"]
        LLMManager["LLM 매니저 (llm_manager.py)<br/>• 동시성 세마포어 (Local: 1, Cloud: 2)<br/>• OpenRouter 429 Retry-After 지연<br/>• parse_json Preamble/중괄호 슬라이싱 내결함성"]
        SummaryEngine["심층 분석 & Q&A 엔진 (ollama_summary.py)<br/>• 9,000자 청킹 및 3대 그룹 발췌<br/>• 원문 인용 일치 검증 (_ground)<br/>• Q&A 질문 키워드 상위 3개 청크 선별"]
        DateEngine["날짜 엔진 (date_utils.py)<br/>• 연말 마감일 익년 연도 롤오버 보정<br/>• D-Day 자동 산출"]
    end

    subgraph Storage_Layer ["5. 데이터 및 인프라 레이어"]
        DB[("SQLite WAL 데이터베이스 (db.py)<br/>• bids (공고 및 분석 데이터)<br/>• sites (수집 대상 사이트)<br/>• settings / taxonomy")]
        DocCache[("디스크 문서 캐시<br/>• data/cache/documents/<br/>• SHA-256(내용+파서버전+OCR)")]
        AnalysisCache[("디스크 분석 캐시<br/>• data/cache/analysis/<br/>• SHA-256(프롬프트+모델+버전)")]
        R2Storage[("Cloudflare R2 오브젝트 스토리지<br/>• r2_storage.py (S3 호환)<br/>• SQLite 스냅샷 및 첨부 백업")]
    end

    UI_Main --> Pipeline
    UI_Main --> UI_Export
    UI_Admin --> DB
    UI_Admin --> R2Storage
    UI_Taxonomy --> TaxonomyMgr
    UI_Export --> DB

    Pipeline --> Crawler
    Pipeline --> RateLimiter
    Crawler --> Parser
    Parser --> OCR
    Parser --> DocCache

    Pipeline --> Classifier
    Classifier --> TaxonomyMgr
    Pipeline --> SummaryEngine
    SummaryEngine --> LLMManager
    SummaryEngine --> AnalysisCache
    UI_Main --> DateEngine

    Pipeline --> DB
    DB --> StampCache
    StampCache -.-> UI_Main
    DB -.-> R2Storage
```

---

## 2. 수집 및 전처리 파이프라인 흐름도 (End-to-End Pipeline & Data Flow)

웹 목록 스크래핑부터 3대 조건 중복 판별, 조기 필터링, 상세/첨부 수집, 텍스트 파싱, LLM 심층 분석, 사실 검증 및 WAL 적재와 보고서 내보내기까지의 실행 순서입니다.

```mermaid
flowchart TD
    Start(["수집 시작 (UI / CLI)"]) --> LoadSites["수집 대상 사이트 및 설정 로드"]
    LoadSites --> FetchList["게시판 목록 스크래핑 (curl_cffi TLS 위장)"]
    
    FetchList --> DedupCheck{"3대 완료 조건 확인 (existing_bid_ids)<br/>분석=성공 & 추출=성공 & 다운로드=성공"}
    DedupCheck -- "이미 완료된 공고" --> SkipDedup["수집 스킵 (기존 데이터 보존)"]
    DedupCheck -- "신규 또는 미완료" --> DateCheck{"등록일 범위 확인<br/>(start_date ~ end_date)"}
    
    DateCheck -- "범위 밖" --> SkipDate["조기 스킵"]
    DateCheck -- "범위 내" --> RuleClassify["1차 규칙 기반 분류 (classify_rules)"]
    
    RuleClassify --> NonITCheck{"명백한 비IT 공고 여부<br/>(조경, 식자재, 청소 등)"}
    NonITCheck -- "비IT 공고" --> SaveNonIT["'수집 제외' 상태로 메타데이터만 저장"]
    NonITCheck -- "IT 관련 공고" --> CertainCheck{"분류 확실성 판단"}
    
    CertainCheck -- "불확실 (판단 보류)" --> SavePending["'판단 보류' 상태로 메타데이터만 저장<br/>(사용자 수동 검토 대기열)"]
    CertainCheck -- "확실 (수집 대상)" --> FetchDetail["상세 페이지 본문 및 첨부파일 다운로드<br/>(Latin-1 파일명 복원 & 안전 ZIP 해제)"]
    
    FetchDetail --> ParseDoc["문서 텍스트 추출 (hwp_parser.py)<br/>• HWP/HWPX/PDF/DOCX/XLSX 변환<br/>• 행정서식 제외 & 제안요청서 유지"]
    ParseDoc --> HasValidText{"유효 텍스트 추출 성공?"}
    
    HasValidText -- "추출 실패/문서 없음" --> MarkFail["'실패' 기록 및 원본 확인 유도"]
    HasValidText -- "성공" --> ChunkSplit["9,000자 단위 청킹 및 3대 그룹 문맥 발췌"]
    
    ChunkSplit --> LLMAnalyze["LLM 다중 질의 (llm_manager.py)<br/>• 1차: OpenRouter (세마포어 2, Retry-After)<br/>• 폴백: Local Ollama (세마포어 1)"]
    LLMAnalyze --> RobustParse["parse_json 내결함성 파싱<br/>• 마크다운 블록 제거<br/>• 서두 Preamble 회피 최외각 중괄호 슬라이싱"]
    
    RobustParse --> FactGrounding["원문 인용 일치 검증 (_ground)<br/>• 사실-인용문 대조<br/>• 미일치 시 '근거 미확인' / '확인 불가' 처리"]
    
    FactGrounding --> MergeFacts["요약, 적합도(0~100), 태그 통합 병합<br/>(일시 오류 시 기존 정상 요약 보존)"]
    MergeFacts --> SaveDB[("SQLite WAL 데이터베이스 저장<br/>(bids 테이블 원자적 업데이트)")]
    
    SaveDB --> TriggerStamp["db_stamp 캐시 무효화 핑거프린트 갱신"]
    TriggerStamp --> ReportReady["보고서 즉시 내보내기 지원<br/>• 스타일 엑셀 (cached_filtered_excel)<br/>• 제안 검토 보고서 (MD / HTML)"]
    ReportReady --> Complete(["파이프라인 완료"])
```

---

## 3. 지능형 문서 파싱 및 OCR 분기 구조도 (Document Parser Engine)

다양한 공공/금융 첨부파일 포맷을 안전하게 텍스트 및 Markdown 표로 복원하고, 행정 서식을 조기에 필터링하는 파서의 분기 흐름입니다.

```mermaid
flowchart TD
    DocIn["첨부파일 인입 (PDF, HWP, HWPX, DOCX, XLSX 등)"] --> CheckHash["SHA-256 디스크 캐시 확인<br/>data/cache/documents/"]
    CheckHash -- "캐시 히트" --> ReturnCache["캐시된 추출 텍스트 반환"]
    CheckHash -- "캐시 미스" --> CheckAdminFilter{"행정 서식 제외 검사<br/>(skip_reason)"}
    
    CheckAdminFilter -- "화이트리스트 일치<br/>(공고, 제안요청, RFP, 사양)" --> DetectFormat["파일 시그니처 및 확장자 판별"]
    CheckAdminFilter -- "단순 서식<br/>(청렴계약서, 입찰참가신청서 등)" --> SkipDoc["'생략' 처리 (토큰 낭비 방지)"]
    
    DetectFormat --> CaseFormat{"파일 포맷 분기"}
    
    CaseFormat -- "HWP 5.x" --> HWP_OLE["OLE 바이트스트림 파싱<br/>• zlib 압축 해제<br/>• TABLE/LIST_HEADER 복원<br/>• Markdown 표 변환"]
    CaseFormat -- "HWPX" --> HWPX_XML["Open Ant XML (zipfile)<br/>• 문단 순서 보존 추출<br/>• 표 태그 Markdown 변환"]
    CaseFormat -- "PDF" --> PDF_Parse["PyMuPDF 구조화 텍스트 추출<br/>• 표 구조 마크다운 복원"]
    CaseFormat -- "DOCX" --> DOCX_Parse["Word XML 순차 문단 추출<br/>• mc:Fallback 중복 제거<br/>• 셀 문단 연속성 보존"]
    CaseFormat -- "XLSX / XLS" --> Excel_Parse["openpyxl / pandas<br/>• 시트별 CSV 변환 병합"]
    CaseFormat -- "PPTX / HTML / RTF" --> Pandoc_Parse["Pandoc gfm 변환"]
    CaseFormat -- "구형 OLE DOC" --> RejectOLE["안전 거부 ('DOCX 변환 필요')"]
    
    PDF_Parse --> OCR_Check{"페이지별 텍스트 밀도 검사"}
    OCR_Check -- "스캔본 판정 (텍스트 부족)" --> Tesseract["Tesseract OCR 엔진<br/>(kor+eng 언어팩)"]
    OCR_Check -- "충분한 텍스트" --> TextNormalize["텍스트 정규화 및 정리"]
    Tesseract --> TextNormalize
    
    HWP_OLE --> TextNormalize
    HWPX_XML --> TextNormalize
    DOCX_Parse --> TextNormalize
    Excel_Parse --> TextNormalize
    Pandoc_Parse --> TextNormalize
    
    TextNormalize --> WriteCache["디스크 캐시 저장"]
    WriteCache --> DocOut["정규화된 Markdown 텍스트 반환"]
```

---

## 4. LLM 심층 분석 및 사실 인용 검증 / Q&A 워크플로우

환각(Hallucination)을 원천 차단하기 위해 9,000자 청크 분할, 3대 정보 그룹 발췌 질의, 원문 인용 검증(`_ground`) 및 대용량 문서 Q&A 질문 키워드 선별 질의를 수행하는 워크플로우입니다.

```mermaid
flowchart TD
    subgraph Analysis_Flow ["1. RFP 심층 분석 워크플로우"]
        RawText["추출된 RFP 전문 텍스트"] --> Chunking["9,000자 단위 청킹 (document_chunks)"]
        RawText --> GroupPassages["3대 정보 그룹 발췌 (_passages)<br/>• 그룹 1: 마감일, 제출방식<br/>• 그룹 2: 필수서류, 참가자격<br/>• 그룹 3: 예산, 사업기간, 평가배점"]
        
        GroupPassages --> LLMCall["LLM 디스패처<br/>• OpenRouter (세마포어 2, Retry-After)<br/>• Local Ollama (세마포어 1)"]
        LLMCall --> ParseRobust["parse_json 내결함성 디코딩<br/>• Markdown 코드블록 제거<br/>• 서두 Preamble 회피 최외각 중괄호 슬라이싱"]
        
        ParseRobust --> QuoteMatch{"원문 인용 대조 (_ground)<br/>(quote in raw_text)"}
        QuoteMatch -- "원문 일치" --> FormatCheck{"형식 유효성 정규식 검사<br/>• 마감일/기간: 날짜/숫자 포함<br/>• 예산: 금액 단위(원/억) 포함"}
        QuoteMatch -- "원문 불일치 (환각)" --> RejectFact["사실 기각 ('확인 불가' 처리)"]
        
        FormatCheck -- "형식 유효" --> AcceptFact["유효 사실 확정 및 Evidence 기록"]
        FormatCheck -- "모호한 서술" --> RejectFact
        
        AcceptFact --> MergeResults["청크별 요약 병합 & 중복 근거 제거"]
        MergeResults --> ScoreCalc["비즈니스 적합도 산출 (0~100점)"]
    end

    subgraph QA_Flow ["2. 대용량 문서 서류 Q&A 챗봇 워크플로우 (answer_bid_question)"]
        UserQ["사용자 질문 인입"] --> ExtractQWords["질문 내 의미 키워드 추출 (길이 ≥ 2)"]
        ExtractQWords --> RankChunks["청크별 키워드 빈도 채점 및 랭킹"]
        RankChunks --> PickTop3["상위 최대 3개 청크만 선별<br/>(토큰 낭비 및 지연 방지)"]
        
        PickTop3 --> QACall["선별 청크별 LLM 질의 (JSON 모드)"]
        QACall --> VerifyQAQuote{"청크 내 원문 인용 대조"}
        VerifyQAQuote -- "인용 일치" --> FormatQA["답변 및 원문 근거 매핑 반환"]
        VerifyQAQuote -- "인용 불일치" --> DropQA["답변 제외"]
    end
```

---

## 5. 3축 택소노미 및 분류 상태 전이 머신 (State Machine)

공고가 수집되어 분류 확정, 사용자 수동 수정 및 연도 롤오버 D-Day 계산에 이르는 라이프사이클 상태 머신입니다.

```mermaid
stateDiagram-v2
    [*] --> 웹공고발견: 크롤러 목록 스크래핑 (TLS 위장)

    웹공고발견 --> 수집제외: 1차 규칙 매칭 (명백한 비IT 공고)
    수집제외 --> [*]: 메타데이터만 보존

    웹공고발견 --> 자동: 3축 키워드 매칭 성공 (IT 확실)
    웹공고발견 --> 판단보류: 키워드 불명확 / 모호한 공고

    자동 --> 첨부수집및AI분석: 상세 페이지 및 파일 수집
    첨부수집및AI분석 --> 완료: 분석 성공 (DB 저장 및 캐시 무효화)

    판단보류 --> 사용자수동검토: UI '분류 검토' 탭 노출
    
    사용자수동검토 --> 수동분류: 사용자 직접 3축 태그 지정
    사용자수동검토 --> 수집대기: 첨부 확인 필요 공고로 전환
    사용자수동검토 --> 수집제외: 사용자 반려

    수동분류 --> 완료: 사용자 지정 값 영구 보존 (manual_classification)
    수집대기 --> 첨부수집및AI분석: 확정 건 단독 수집 (collect_confirmed_bid)

    완료 --> DDay산출: 연말 마감일 익년 롤오버 보정 (연말 고시 1~3월 마감 -> year+1)
    DDay산출 --> 보고서출력: 실무용 엑셀 캐싱 & 제안 검토 보고서 내보내기
    보고서출력 --> [*]

    note right of 수동분류
        사용자가 수동으로 확정한 분류는
        이후 크롤러가 재실행되어도
        자동 결과로 덮어쓰지 않고
        최우선 보존됩니다.
    end note
```

---

## 6. 데이터베이스 ERD 및 스토리지/리포트 모델 (ERD & Storage)

SQLite WAL 데이터베이스의 주요 테이블 구조, 인덱스, 3대 완료 판정 조건, 디스크 캐시 및 실무용 리포트 내보내기 연동 관계도입니다.

```mermaid
erDiagram
    bids ||--o{ evidence : contains
    sites ||--o{ bids : collects
    settings ||--|| bids : configures
    taxonomy ||--o{ bids : classifies
    bids ||--o{ report_export : generates

    bids {
        TEXT bid_id PK "고유 공고 ID (source_key:source_id)"
        TEXT source_key "수집원 축약 식별자 (kb, nh, nhit, shinds, hanati)"
        TEXT source_id "수집원 원시 게시글 번호"
        TEXT site_name "수집원 사이트 표기명"
        TEXT org "발주 기관명 (금융 계열사)"
        TEXT title "공고 제목"
        TEXT reg_date "등록일 (YYYY-MM-DD)"
        TEXT deadline "제안서 제출 마감일시"
        TEXT submit_type "제출 방식"
        TEXT required_docs "필수 제출 서류"
        TEXT qualifications "참가 자격 요건"
        TEXT summary "AI 2문장 사업 요약"
        INTEGER fit_score "비즈니스 적합도 점수 (0~100)"
        TEXT fit_reason "적합도 산정 사유"
        TEXT tags "1축 분야 태그 (JSON Array)"
        TEXT solution_tags "2축 세부 솔루션 태그 (JSON Array)"
        TEXT work_tags "3축 업무 유형 태그 (JSON Array)"
        TEXT classification_status "분류 상태 (자동/수동분류/판단보류/수집제외)"
        TEXT manual_classification "사용자 수동 수정 값 (JSON Object)"
        TEXT extraction_status "첨부 추출 상태 (성공/부분성공/실패/생략)"
        TEXT analysis_status "AI 분석 상태"
        TEXT analysis_model "사용된 LLM 모델명"
        TEXT evidence "사실 추출 근거 인용문 (JSON Array)"
        INTEGER starred "사용자 관심 공고 (0 또는 1)"
        TEXT status "진행 상태 (검토중/진행예정/제안완료/포기)"
    }

    sites {
        TEXT site_name PK "사이트 표시명 (고유 식별)"
        TEXT site_url "수집 진입점 URL"
        TIMESTAMP created_at "등록 일시"
    }

    settings {
        TEXT key PK "설정 키 (last_cleanup, selected_sites 등)"
        TEXT value "설정 값 (문자열 또는 JSON)"
    }

    taxonomy {
        TEXT domain PK "1축 대분야"
        TEXT solution "2축 세부 솔루션"
        TEXT work "3축 업무 유형"
        TEXT synonyms "동의어 목록 (JSON Array)"
    }

    evidence {
        TEXT field "대상 필드 (deadline, budget 등)"
        TEXT quote "원문 인용 문자열"
        TEXT location "발췌 위치/파일명"
    }

    report_export {
        BYTES excel_binary "21개 컬럼 서식 적용 엑셀 (cached_filtered_excel)"
        TEXT markdown_doc "마크다운 표 파이프 이스케이프 보고서 (.md)"
        TEXT html_doc "A4 인쇄 최적화 제안 검토 보고서 (.html)"
    }
```
