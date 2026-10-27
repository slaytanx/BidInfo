"""분류 사전 관리와 판단 보류 공고의 수동 확정·선택 수집 화면."""
import json
import streamlit as st
from taxonomy import DOMAINS, DEFAULT_DOMAINS, SOURCES, load_taxonomy, taxonomy_options, save_taxonomy_item
from db import load_all_bids, set_manual_classification
from pipeline import process_confirmed


def show_taxonomy_editor():
    with st.expander('분류 사전 관리', expanded=False):
        rows=load_taxonomy(False)
        st.caption('상위 분야는 인프라·보안·컨설팅·개발입니다. 세부 항목과 업무의 동의어를 추가·수정하거나 비활성화할 수 있습니다.')
        st.dataframe([{'구분':r['kind'],'분야':r['domain'],'항목':r['name'],'동의어':', '.join(r['aliases']),'사용':bool(r['enabled'])} for r in rows],width='stretch',hide_index=True)
        choices={'새 항목':None, **{f"{r['id']} · {r['domain'] or '공통'} / {r['name']}":r for r in rows if r['kind']!='분야'}}
        label=st.selectbox('편집할 사전 항목',list(choices),key='taxonomy_edit_choice')
        row=choices[label]
        if row:
            kind,domain,name=row['kind'],row['domain'],row['name']
            st.caption(f'{kind} / {domain or "공통"} / {name}')
        else:
            kind=st.selectbox('추가할 구분',['세부 항목','업무'],key='taxonomy_new_kind')
            domain=st.selectbox('소속 분야',DEFAULT_DOMAINS,key='taxonomy_new_domain') if kind=='세부 항목' else ''
            name=''
        with st.form('taxonomy_entry_'+str(row['id'] if row else 'new')):
            if not row:name=st.text_input('새 항목 이름')
            aliases=st.text_area('키워드·동의어 (쉼표 또는 줄바꿈으로 구분)',value=', '.join(row['aliases']) if row else '')
            enabled=st.checkbox('이 항목 사용',value=bool(row['enabled']) if row else True)
            if st.form_submit_button('분류 사전 저장'):
                try:
                    save_taxonomy_item(kind,domain,name,aliases.replace('\n',',').split(','),enabled,row['id'] if row else None)
                    st.success('사전을 저장했습니다. 새 수집·재분류부터 적용됩니다.')
                    st.rerun()
                except (ValueError, __import__('sqlite3').IntegrityError) as error:
                    st.error(str(error))
        st.download_button('분류 사전 JSON 받기',json.dumps({'items':rows,'sources':SOURCES},ensure_ascii=False,indent=2),file_name='bid_taxonomy.json',mime='application/json')
        for source in SOURCES.values():st.caption(f"[{source['title']}]({source['url']})")


def show_review_queue(base_dir,engines):
    rows=load_all_bids()
    st.subheader('판단 보류 공고 검토')
    st.caption('보류 공고는 제목·상세 정보만 저장합니다. 분야를 직접 확정한 뒤 아래에서 선택한 건만 첨부를 받을 수 있습니다.')
    states=st.multiselect('검토할 분류 상태',['판단 보류','수집 대기','수동 분류','수집 제외','자동'],default=['판단 보류'],key='review_states')
    reviewed=[r for r in rows if r['classification_status'] in states]
    if reviewed:
        choices={r['bid_id']:r for r in reviewed}
        selected=st.selectbox('분류할 공고',list(choices),format_func=lambda value:f"[{choices[value]['site_name']}] {choices[value]['title']}",key='review_bid')
        row=choices[selected]
        st.markdown(f"[{row['title']}]({row['origin_url']})")
        st.info(row['classification_reason'] or '분류 근거 부족')
        if row['notice_text']:
            with st.expander('수집한 상세 내용 확인'):st.text(row['notice_text'])
        current_domains=json.loads(row['tags'] or '[]')
        domains=st.multiselect('수동 분야',DOMAINS,default=[d for d in current_domains if d in DOMAINS],key='manual_domains_'+selected)
        solution_options=taxonomy_options('세부 항목',domains)
        previous_solutions=json.loads(row['solution_tags'] or '[]')
        solutions=st.multiselect('수동 세부 항목',solution_options,default=[s for s in previous_solutions if s in solution_options],key='manual_solutions_'+selected+'_'+','.join(domains))
        work_options=taxonomy_options('업무')
        work=st.multiselect('수동 업무',work_options,default=[w for w in json.loads(row['work_tags'] or '[]') if w in work_options],key='manual_work_'+selected)
        accept,reject=st.columns(2)
        if accept.button('수집 대상으로 확정',key='approve_'+selected,type='primary'):
            try:
                set_manual_classification(selected,domains,solutions,work,True)
                st.rerun()
            except ValueError as error:st.warning(str(error))
        if reject.button('수집 제외로 지정',key='exclude_'+selected):
            set_manual_classification(selected,domains,solutions,work,False);st.rerun()
    else:st.info('선택한 상태의 공고가 없습니다.')
    st.subheader('확정한 공고만 추가 수집·분석')
    if 'last_confirmed_result' in st.session_state:st.json(st.session_state['last_confirmed_result'])
    waiting=[r for r in rows if r['classification_status']=='수집 대기' and json.loads(r['manual_classification'] or '{}').get('collect')]
    if not waiting:
        st.caption('새로 확정했거나 재시도가 필요한 공고가 없습니다.');return
    by_id={r['bid_id']:r for r in waiting}
    selected_ids=st.multiselect('추가 수집할 확정 공고',list(by_id),format_func=lambda value:f"[{by_id[value]['site_name']}] {by_id[value]['title']}",key='confirmed_bid_ids')
    if st.button('선택한 확정 공고만 첨부 수집·분석',type='primary',disabled=not selected_ids,key='collect_confirmed'):
        progress=st.progress(0.0)
        try:
            with st.spinner('선택한 공고의 첨부를 수집하고 분석합니다...'):
                result=process_confirmed(selected_ids,base_dir,engines,lambda n,t,r:progress.progress(n/t))
            st.session_state['last_confirmed_result']=result
            st.rerun()
        except (ValueError,RuntimeError) as error:st.error(str(error))
