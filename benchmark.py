"""운영 문서·자격증명을 fixture로 쓰지 않는 한글 RFP 12건 모델 비교."""
import argparse
import json
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from llm_manager import CLOUD_MODELS, LOCAL_MODELS, RunBudget, get_secret
from ollama_summary import analyze_bid_with_ollama, PROMPT_VERSION


def rfp_cases():
    subjects = [('방화벽 교체','보안'),('스위치 구축','네트워크'),('서버 증설','서버'),('백업 스토리지','스토리지'),
                ('모바일 앱 개발','개발'),('전산장비 유지보수','유지보수'),('보안 관제 운영','운영'),('전용회선 서비스','통신'),
                ('클라우드 전환','클라우드'),('데이터베이스 구축','데이터/DB'),('인공지능 챗봇','AI'),('서버 유지보수','서버')]
    cases = []
    for index, (subject, tag) in enumerate(subjects, 1):
        deadline = f'2026-11-{index:02d} 15:00'
        filler = ('일반 안내: 이 문서는 평가용 합성 RFP이며 운영 자료가 아닙니다.\n' * 160) if index in (10,12) else ''
        text = (f'[파일: 합성RFP{index}.txt]\n[p.1]\n사업명: {subject}\n사업예산: 100,000,000원\n수행기간: 6개월\n'
                f'사업 범위: {subject}에 필요한 장비와 업무를 수행한다.\n{filler}'
                f'[p.2]\n제출마감일시: {deadline}\n제출방식: 전자제출\n필수제출서류: 제안서, 사업자등록증\n'
                '참가자격요건: 소프트웨어사업자 신고 확인서를 보유한 업체\n평가배점: 기술 80점, 가격 20점\n')
        from taxonomy import legacy_classification
        mapped=legacy_classification([tag])
        cases.append({'title': subject, 'text': text, 'tag': (mapped['tags'] or ['인프라'])[0], 'deadline': deadline})
    return cases


def run_benchmark(models=None, budget_limit=1, progress=None, output=None):
    models = models or LOCAL_MODELS + CLOUD_MODELS
    budget = RunBudget(budget_limit)
    report = {'date': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), 'prompt_version': PROMPT_VERSION,
              'dataset': '한글 합성 RFP 12건; 실제 RFP 정확도를 보장하지 않음', 'results': []}
    for model in models:
        if model in CLOUD_MODELS and not get_secret('OPENROUTER_API_KEY'):
            report['results'].append({'model': model, 'status': 'API 키 미연결', 'cases': 0})
            continue
        config = {'provider': 'OpenRouter' if model in CLOUD_MODELS else 'Local Ollama', 'model': model, 'budget': budget}
        before_cost = budget.snapshot()['spent_or_reserved_usd']
        latencies, successes, accurate, tag_hits = [], 0, 0, 0
        for index, case in enumerate(rfp_cases(), 1):
            start = time.monotonic()
            result = analyze_bid_with_ollama(case['title'], case['text'], engine_configs=[config])
            latencies.append(time.monotonic()-start)
            success = result['analysis_status'] == '성공'
            successes += success
            correct = success and case['deadline'] in result['deadline'] and '전자' in result['submit_type'] and '제안서' in result['required_docs'] and '사업자등록증' in result['required_docs'] and '소프트웨어' in result['qualifications']
            accurate += correct
            tag_hits += success and case['tag'] in result['tags']
            if progress:
                progress(model, index, 12)
        stats = {'model': model, 'status': '완료', 'cases': 12, 'median_seconds': round(sorted(latencies)[6],2),
                 'total_seconds': round(sum(latencies),2), 'json_success_rate': round(successes/12,3),
                 'required_fields_accuracy': round(accurate/12,3), 'tag_recall': round(tag_hits/12,3),
                 'cost_usd': round(budget.snapshot()['spent_or_reserved_usd']-before_cost, 6),
                 'prompt_version': PROMPT_VERSION}
        report['results'].append(stats)
        if output:
            _write_report(report, output)
    report['budget'] = budget.snapshot()
    if output:
        _write_report(report, output)
    return report


def _write_report(report, output):
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text()).get('results', [])
        except ValueError:
            pass
    measured = {r['model'] for r in report['results']}
    saved = dict(report, results=[r for r in existing if r['model'] not in measured] + report['results'])
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', default=LOCAL_MODELS + CLOUD_MODELS)
    parser.add_argument('--budget', type=float, default=1)
    args = parser.parse_args()
    report = run_benchmark(args.models, args.budget, lambda m,n,t: print(f'{m}: {n}/{t}', flush=True), Path(__file__).parent/'data/model_benchmark.json')
    print(json.dumps(report, ensure_ascii=False, indent=2))
