"""화면과 같은 처리 흐름을 사용하는 로컬 CLI."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from classification import CATEGORIES, DEFAULT_CATEGORIES
from crawler import crawl_and_download_bids_by_date
from db import init_db, load_all_sites
from llm_manager import RunBudget, engine_configs, CLOUD_MODELS, LOCAL_MODEL
from pipeline import process_bids, backfill_existing, reanalyze_existing


def main():
    parser = argparse.ArgumentParser(description='입찰 수집·분류·문서 분석')
    parser.add_argument('--site', default='NH농협 입찰공고')
    parser.add_argument('--start', type=date.fromisoformat, default=date.today()-timedelta(days=7))
    parser.add_argument('--end', type=date.fromisoformat, default=date.today())
    parser.add_argument('--keyword', default='')
    parser.add_argument('--categories', default=','.join(DEFAULT_CATEGORIES))
    parser.add_argument('--solutions', default='')
    parser.add_argument('--work', default='')
    parser.add_argument('--include-uncertain', action='store_true')
    parser.add_argument('--confirmed-ids', nargs='+')
    parser.add_argument('--mode', choices=['혼합','로컬','OpenRouter'], default='혼합')
    parser.add_argument('--cloud-model', default=CLOUD_MODELS[0])
    parser.add_argument('--local-model', default=LOCAL_MODEL)
    parser.add_argument('--budget', type=float, default=1)
    parser.add_argument('--force-refresh', action='store_true')
    parser.add_argument('--backfill', action='store_true')
    parser.add_argument('--reanalyze', action='store_true')
    args = parser.parse_args()
    init_db()
    categories = [value.strip() for value in args.categories.split(',') if value.strip()]
    if any(c not in CATEGORIES for c in categories):
        parser.error('허용 분야: ' + ', '.join(CATEGORIES))
    solutions=[x.strip() for x in args.solutions.split(',') if x.strip()]
    work=[x.strip() for x in args.work.split(',') if x.strip()]
    from taxonomy import taxonomy_options
    if any(x not in taxonomy_options('세부 항목') for x in solutions) or any(x not in taxonomy_options('업무') for x in work):
        parser.error('세부 항목/업무 이름을 분류 사전에서 확인해 주세요')
    if args.backfill:
        print(json.dumps(backfill_existing(progress=lambda n,t,r: print(f'{n}/{t} 문서 재추출·분류')), ensure_ascii=False));return
    budget = RunBudget(args.budget)
    engines = engine_configs(args.mode, args.cloud_model, args.local_model, budget)
    if args.confirmed_ids:
        from pipeline import process_confirmed
        metrics=process_confirmed(args.confirmed_ids,str(Path(__file__).parent/'data'),engines)
    elif args.reanalyze:
        _, metrics = reanalyze_existing(engines, categories)
    else:
        sites = load_all_sites()
        if args.site not in sites:
            parser.error('등록된 사이트: ' + ', '.join(sites))
        metrics = {}
        items = crawl_and_download_bids_by_date(sites[args.site], str(Path(__file__).parent/'data'), args.start, args.end,
            args.keyword, args.site, categories, args.mode != 'OpenRouter', args.force_refresh, metrics,
            selected_solutions=solutions, selected_work=work, include_uncertain=args.include_uncertain)
        _, stats = process_bids(items, categories, engines, progress=lambda n,t,r: print(f'{n}/{t}: {r.get("analysis_status")}'))
        metrics.update(stats)
    metrics.update(budget.snapshot())
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
