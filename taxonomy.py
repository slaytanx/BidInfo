"""프로젝트용 분야·세부 항목·업무 분류 사전. 공식 분류를 그대로 대체하지 않는다."""
import hashlib
import json

DOMAINS = ['인프라', '보안', '컨설팅', '개발', '기타']
DEFAULT_DOMAINS = DOMAINS[:4]
SOURCES = {
 'infra': {'title': 'Red Hat 가상화·인프라 구성요소', 'url': 'https://developers.redhat.com/topics/virtualization'},
 'security': {'title': 'KISIA 정보보호산업 솔루션·서비스 분류 참고', 'url': 'https://kisia.or.kr/bucket/uploads/2024/05/16/kisia%20%EB%94%94%EB%A0%89%ED%86%A0%EB%A6%AC%EB%B6%81%20%EC%9B%B9%ED%8E%98%EC%9D%B4%EC%A7%80%20%EB%93%B1%EB%A1%9D%20%ED%9A%8C%EC%9B%90%EC%82%AC%20%EB%AA%A8%EC%A7%91%20%EA%B3%B5%EA%B3%A0%285.13%29_%EC%88%98%EC%A0%95%ED%9B%84.pdf'},
 'edr': {'title': 'Cisco EDR·NDR·방화벽·SIEM 용어', 'url': 'https://www.cisco.com/c/en/us/products/collateral/security/xdr/xdr-ds.html'},
 'nac': {'title': 'Cisco NAC 용어', 'url': 'https://www.cisco.com/site/us/en/learn/topics/security/what-is-network-access-control-nac.html'},
 'dlp': {'title': 'Cisco DLP 용어', 'url': 'https://www.cisco.com/site/us/en/learn/topics/security/what-is-data-loss-prevention-dlp.html'},
 'isp': {'title': 'NIA ISP·ISMP 수립 공통가이드', 'url': 'https://www.nia.or.kr/site/nia_kor/ex/bbs/View.do?bcIdx=28088&cbIdx=99835'},
 'isms': {'title': 'KISA ISMS-P 인증 제도', 'url': 'https://isms.kisa.or.kr/cert/aply/selectCertPrcdDetail.do'},
}

# 한국어·영문 약어·띄어쓰기 변형은 같은 이름으로 묶는다.
SOLUTION_SEEDS = {
 '인프라': {
  '서버': ['서버','server','x86','유닉스','unix','메인프레임','블레이드 서버','GPU 서버'],
  '스토리지': ['스토리지','storage','저장장치','디스크 어레이','SAN','NAS','올플래시','all-flash','NVMe'],
  '네트워크': ['네트워크','network','스위치','switch','라우터','router','L2','L3','L4','LAN','WAN','SD-WAN','SDN'],
  '로드밸런서': ['로드밸런서','부하분산','load balancer','ADC','SLB'],
  '무선망': ['무선랜','무선망','Wi-Fi','WLAN','무선 AP'],
  '통신/회선': ['통신','회선','전용선','인터넷','IP-PBX','IPPBX','PBX','VoIP','전화교환기'],
  '가상화': ['가상화','virtualization','VMware','vSphere','ESXi','Hyper-V','KVM','Proxmox'],
  'VDI': ['VDI','가상 데스크톱','데스크톱 가상화','가상PC'],
  'HCI': ['HCI','하이퍼컨버지드','hyperconverged'],
  '클라우드': ['클라우드','cloud','IaaS','PaaS','SaaS','AWS','Azure','GCP','프라이빗 클라우드'],
  '컨테이너': ['컨테이너','container','Kubernetes','K8s','OpenShift','Docker'],
  'OS': ['운영체제','OS','Linux','리눅스','Windows Server','RHEL','AIX'],
  'DBMS': ['DBMS','데이터베이스','DB','Oracle','오라클','PostgreSQL','MySQL','MSSQL','Tibero','티베로'],
  '미들웨어': ['미들웨어','middleware','WAS','웹서버','WebLogic','JEUS','Tomcat','Apache','Nginx'],
  '백업/복구': ['백업','복구시스템','backup','백업복구','Veeam','NetBackup'],
  'DR/이중화': ['재해복구','DR','이중화','고가용성','HA','재해 복구'],
  '인프라 모니터링': ['인프라 모니터링','성능관리','APM','NMS','EMS','ITSM','IT 인프라 관리'],
  '데이터센터 설비': ['데이터센터','전산센터','UPS','무정전전원','항온항습','랙','전산실'],
 },
 '보안': {
  '방화벽': ['방화벽','firewall','NGFW','차세대 방화벽'],
  'DDoS': ['DDoS','디도스','분산서비스거부','분산 서비스 거부','서비스거부 공격'],
  'IPS': ['IPS','침입방지','침입 방지','intrusion prevention'],
  'IDS': ['IDS','침입탐지','침입 탐지','intrusion detection'],
  'EDR': ['EDR','엔드포인트 탐지','endpoint detection'],
  'NDR': ['NDR','네트워크 탐지 및 대응','네트워크 탐지대응','network detection'],
  'XDR': ['XDR','확장 탐지 및 대응','extended detection'],
  '백신/EPP': ['백신','안티바이러스','antivirus','anti-virus','EPP','엔드포인트 보호','악성코드 차단'],
  'NAC': ['NAC','네트워크 접근제어','네트워크 접근 제어','network access control'],
  'WAF': ['WAF','웹방화벽','웹 방화벽','web application firewall'],
  'DLP': ['DLP','정보유출방지','정보 유출 방지','자료유출방지','data loss prevention','data leakage prevention'],
  'DRM': ['DRM','문서보안','문서 보안','문서암호화','문서 암호화'],
  '접근통제/PAM': ['접근통제','접근 통제','접근제어','접근 제어','PAM','특권계정','DB 접근제어','서버 접근제어'],
  'IAM/인증': ['IAM','통합계정관리','계정관리','SSO','MFA','다중요소 인증','다중인증','OTP','PKI','FIDO','인증시스템'],
  '암호화/키관리': ['암호화','암호관리','키관리','키 관리','HSM','KMS','encryption'],
  '망분리/망연계': ['망분리','망 분리','망연계','망 연계','자료전송시스템'],
  'VPN/ZTNA': ['VPN','SSL VPN','IPSec','ZTNA','제로트러스트','제로 트러스트'],
  'SASE/SSE': ['SASE','SSE','보안 서비스 에지','보안서비스에지'],
  'SIEM/로그관리': ['SIEM','통합보안관리','통합 보안 관리','보안로그','보안 로그','로그관리','로그 분석'],
  'SOAR': ['SOAR','보안 자동화','보안 오케스트레이션'],
  '메일/웹 보안': ['메일보안','메일 보안','스팸차단','스팸 차단','피싱차단','SWG','웹 보안'],
  '클라우드 보안': ['클라우드 보안','CNAPP','CSPM','CWPP','CASB','컨테이너 보안'],
  '취약점 관리': ['취약점','vulnerability','CVE','패치관리','패치 관리','SAST','DAST','SCA'],
  'APT/랜섬웨어': ['APT','지능형 위협','랜섬웨어','ransomware','샌드박스'],
  '개인정보보호': ['개인정보보호','개인정보 보호','개인정보 탐지','비식별','가명처리','마스킹'],
  'FDS': ['FDS','이상거래탐지','이상 거래 탐지','금융사기 탐지'],
 },
 '컨설팅': {
  'ISP/ISMP': ['ISP','ISMP','정보화전략계획','정보화 전략 계획','정보시스템 마스터플랜'],
  'ISMS-P/인증 컨설팅': ['ISMS-P','ISMS','PIMS','ISO 27001','정보보호 관리체계','인증 컨설팅'],
  '보안 컨설팅/진단': ['보안 컨설팅','정보보호 컨설팅','보안진단','보안 진단','보안감사','보안 감사'],
  '모의해킹': ['모의해킹','모의 해킹','침투테스트','침투 테스트','penetration test','pentest'],
  'IT 전략/아키텍처': ['IT 전략','정보화 전략','EA 컨설팅','아키텍처 컨설팅','BPR','PI 컨설팅'],
  '감리/PMO': ['감리','PMO','사업관리 컨설팅','품질관리 컨설팅'],
 },
 '개발': {
  '업무/금융 시스템': ['업무시스템','업무 시스템','금융시스템','금융 시스템','차세대 시스템','코어뱅킹','계정계','정보계'],
  '웹/모바일': ['웹개발','웹 개발','모바일','앱 개발','애플리케이션','어플리케이션','홈페이지','웹서비스'],
  'API/시스템 연계': ['API','시스템 연계','인터페이스 개발','EAI','ESB','API Gateway','API 게이트웨이'],
  '데이터/분석': ['데이터 분석','데이터 플랫폼','데이터웨어하우스','데이터 웨어하우스','빅데이터','ETL','DW','BI'],
  'AI/자동화': ['AI','인공지능','생성형','LLM','머신러닝','챗봇','콜봇','AICC','RPA','업무자동화'],
  'DevOps/개발도구': ['DevOps','CI/CD','개발도구','개발 도구','형상관리','GitLab','Jenkins'],
  'SW 개발/개선': ['소프트웨어 개발','SW 개발','S/W 개발','프로그램 개발','소프트웨어 개선','프로그램 개선'],
 },
}
WORK_SEEDS = {
 '구축': ['구축','설치','구현'], '재구축': ['재구축','재 구축'], '고도화': ['고도화','업그레이드','upgrade','개선'],
 '도입/구매': ['도입','구매','구입','조달','라이선스','license','갱신'],
 '교체/증설': ['교체','증설','확장','증대'], '유지보수': ['유지보수','유지 보수','유지관리','유지 관리','정비','하자보수'],
 '운영': ['운영','아웃소싱','위탁','운영대행','운영 대행'], '관제': ['관제','보안관제','보안 관제','SOC','통합관제'],
 '진단/점검': ['진단','점검','평가','감사','모의해킹','모의 해킹'],
 '설계/기획': ['설계','기획','전략계획','전략 계획','ISP','ISMP'],
 '전환/이전': ['전환','이전','이관','마이그레이션','migration','클라우드 전환'],
 '개발': ['개발','development'], '검증/시험': ['검증','시험','테스트','test'], '교육': ['교육','훈련','training'],
}
DOMAIN_ALIASES = {'인프라':['인프라','전산장비','IT 자원','IT자원'], '보안':['보안','정보보호','정보 보호'],
                  '컨설팅':['컨설팅','consulting'], '개발':['개발','소프트웨어 개발'], '기타':[]}
LEGACY = {'네트워크':('인프라','네트워크',''), '서버':('인프라','서버',''), '스토리지':('인프라','스토리지',''),
          '통신':('인프라','통신/회선',''), '클라우드':('인프라','클라우드',''), '데이터/DB':('인프라','DBMS',''),
          'AI':('개발','AI/자동화',''), '유지보수':('','','유지보수'), '운영':('','','운영')}


def legacy_classification(tags):
    domains, solutions, work = [], [], []
    for tag in tags:
        if tag in DOMAINS:
            domains.append(tag)
        elif tag in LEGACY:
            domain, solution, task = LEGACY[tag]
            if domain: domains.append(domain)
            if solution: solutions.append(solution)
            if task: work.append(task)
    return {'tags':list(dict.fromkeys(domains)), 'solution_tags':list(dict.fromkeys(solutions)), 'work_tags':list(dict.fromkeys(work))}


def seed_taxonomy(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS taxonomy (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]', enabled INTEGER NOT NULL DEFAULT 1,
        sources TEXT NOT NULL DEFAULT '[]', UNIQUE(kind,domain,name))''')
    entries = [('분야',d,d,a,[]) for d,a in DOMAIN_ALIASES.items()]
    for domain, groups in SOLUTION_SEEDS.items():
        refs = ['infra'] if domain=='인프라' else ['security','edr'] if domain=='보안' else ['isp','isms'] if domain=='컨설팅' else []
        entries += [('세부 항목',domain,name,aliases,refs) for name,aliases in groups.items()]
    entries += [('업무','',name,aliases,[]) for name,aliases in WORK_SEEDS.items()]
    conn.executemany('INSERT OR IGNORE INTO taxonomy(kind,domain,name,aliases,sources) VALUES (?,?,?,?,?)',
                     [(k,d,n,json.dumps(a,ensure_ascii=False),json.dumps(s)) for k,d,n,a,s in entries])


def load_taxonomy(enabled_only=True):
    from db import init_db, get_db_connection
    init_db()
    with get_db_connection() as conn:
        conn.row_factory = __import__('sqlite3').Row
        rows = [dict(row) for row in conn.execute('SELECT * FROM taxonomy' + (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY id')]
    for row in rows:
        row['aliases'] = json.loads(row['aliases']);row['sources'] = json.loads(row['sources'])
    return rows


def taxonomy_options(kind, domains=None):
    return list(dict.fromkeys(r['name'] for r in load_taxonomy() if r['kind']==kind and (not domains or r['domain'] in domains or not r['domain'])))


def taxonomy_signature():
    rows=load_taxonomy()
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]


def save_taxonomy_item(kind, domain, name, aliases, enabled=True, item_id=None):
    from db import init_db, get_db_connection
    init_db()
    if kind not in ('세부 항목','업무') or not name.strip() or len(name)>80:
        raise ValueError('세부 항목 또는 업무 이름을 확인해 주세요')
    if kind=='세부 항목' and domain not in DEFAULT_DOMAINS:
        raise ValueError('상위 분야를 선택해 주세요')
    domain = domain if kind=='세부 항목' else ''
    aliases = list(dict.fromkeys(a.strip() for a in aliases if a.strip()))
    if not aliases or len(aliases)>100 or any(len(a)>120 for a in aliases):
        raise ValueError('동의어는 1~100개, 각 120자 이내로 입력해 주세요')
    with get_db_connection() as conn:
        if item_id is None:
            if conn.execute('SELECT 1 FROM taxonomy WHERE kind=? AND name=?',(kind,name.strip())).fetchone():
                raise ValueError('같은 이름이 이미 있습니다. 기존 항목의 동의어를 수정해 주세요')
            conn.execute('INSERT INTO taxonomy(kind,domain,name,aliases,enabled) VALUES (?,?,?,?,?)', (kind,domain,name.strip(),json.dumps(aliases,ensure_ascii=False),int(enabled)))
        else:
            # 이름과 소속은 고정해 기존 공고·선택 조건의 의미를 보존한다.
            cursor=conn.execute('UPDATE taxonomy SET aliases=?,enabled=? WHERE id=? AND kind=? AND domain=? AND name=?',
                (json.dumps(aliases,ensure_ascii=False),int(enabled),item_id,kind,domain,name))
            if not cursor.rowcount: raise ValueError('분류 항목을 찾을 수 없습니다')
        conn.commit()
