let state={projects:[],tasks:[],runs:[]}, csrf='', selectedRun=null, selectedTab='log', lastRun=null, logLines=[], cursor=0, polling=false;
const $=id=>document.getElementById(id);
const labels={todo:'대기',running:'실행 중',verified:'소스·검사 통과',reviewed:'소스 검수 통과',failed:'검증 실패',blocked:'확인 필요',cancelled:'사용자 중단',interrupted:'종료로 중단',demo_done:'데모 완료',no_changes:'변경 없음'};
const stages={queued:'준비 대기',preflight:'경로 확인',worker:'Codex 수정',tests:'자동 검사',reviewer:'독립 검수'};
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge=s=>`<span class="badge ${['verified','reviewed'].includes(s)?'good':s==='running'?'running':s==='demo_done'?'demo':s==='todo'?'':'issue'}">${escape(labels[s]||s)}</span>`;
function toast(s){$('toast').textContent=s;$('toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.add('hidden'),6500)}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});const data=await r.json().catch(()=>{throw Error('서버 응답을 읽을 수 없습니다. 연결 상태를 확인하세요.')});if(!r.ok)throw Error(data.error||'요청 실패');return data}
function page(name){document.querySelectorAll('.page').forEach(e=>e.classList.toggle('hidden',e.id!==`page-${name}`));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===name));$('breadcrumb').textContent='워크스페이스 / '+({overview:'작업 현황',projects:'프로젝트',history:'실행 기록',guide:'사용 안내'}[name])}
function projectName(id){return state.projects.find(p=>p.id===id)?.name||'프로젝트'}
function card(t){const r=state.runs.find(r=>r.task_id===t.id);return `<article class="task-card"><div class="project-name">${escape(projectName(t.project_id))}</div><h3>${escape(t.title)}</h3><p>${escape(t.prompt)}</p><details><summary>지시·완료 기준 전체 보기</summary><p>${escape(t.prompt)}</p><p>${escape(t.acceptance)}</p></details>${badge(t.status)}<div class="card-actions">${r?`<button class="secondary" data-run="${r.id}">결과 보기</button>`:''}<button class="primary" data-start="${t.id}" ${state.active||!state.system.codex?'disabled':''}>Codex 실행</button><button class="secondary" data-demo="${t.id}" ${state.active?'disabled':''}>데모</button></div></article>`}
function render(){
 $('system-status').textContent=`Codex ${state.system.codex?'설치 확인':'미설치'} · Python ${state.system.python}`;
 $('stat-total').textContent=state.tasks.length;$('stat-running').textContent=state.tasks.filter(t=>t.status==='running').length;
 $('stat-done').textContent=state.tasks.filter(t=>['verified','reviewed'].includes(t.status)).length;
 $('stat-issue').textContent=state.tasks.filter(t=>['failed','blocked','cancelled','interrupted','no_changes'].includes(t.status)).length;
 const filter=$('project-filter').value;
 $('project-filter').innerHTML='<option value="">모든 프로젝트</option>'+state.projects.map(p=>`<option value="${p.id}">${escape(p.name)}</option>`).join('');
 $('project-filter').value=filter;
 const query=$('task-search').value.trim().toLowerCase(), status=$('status-filter').value;
 const tasks=state.tasks.filter(t=>(!filter||t.project_id===filter)&&(!status||t.status===status)&&(!query||`${t.title} ${t.prompt}`.toLowerCase().includes(query)));
 const columns=[['대기',t=>t.status==='todo'],['진행 · 확인 필요',t=>!['todo','verified','reviewed','demo_done'].includes(t.status)],['검수 · 데모 결과',t=>['verified','reviewed','demo_done'].includes(t.status)]];
 $('task-board').innerHTML=columns.map(([name,fn])=>{const list=tasks.filter(fn);return `<section class="column"><div class="column-title">${name}<span class="count">${list.length}</span></div>${list.map(card).join('')||'<p class="empty-column">등록된 작업이 없습니다</p>'}</section>`}).join('');
 $('welcome').classList.toggle('hidden',state.tasks.length>0);
 $('project-list').innerHTML=state.projects.map(p=>`<article class="project-card"><h3>${escape(p.name)}</h3><p class="path">${escape(p.path)}</p><dl><dt>수정 허용 범위</dt><dd>${escape(p.config.allowed.join('\n'))}</dd><dt>검사 명령</dt><dd>${p.config.commands.length?escape(JSON.stringify(p.config.commands,null,2)):'없음 · 자동 검사 없이 검수만 수행'}</dd><dt>모델</dt><dd>${escape(p.config.model||'CLI 기본 설정')}</dd><dt>규칙</dt><dd>${escape(p.config.rules||'미지정')}</dd><dt>재수정 / 시간</dt><dd>${p.config.retries}회 / ${p.config.timeout}초</dd></dl><button class="secondary" data-edit="${p.id}">설정 수정</button></article>`).join('')||'<p>연결된 프로젝트가 없습니다. 프로젝트 연결 버튼으로 시작하세요.</p>';
 $('history-list').innerHTML=state.runs.map(r=>`<article class="history-row">${badge(r.status)}<div class="title"><h3>${escape(state.tasks.find(t=>t.id===r.task_id)?.title||r.task_id)}</h3><small>${escape(new Date(r.created).toLocaleString('ko-KR'))} · ${r.mode==='demo'?'데모':'Codex'} · 시도 ${r.attempt}</small></div><button class="secondary" data-run="${r.id}">상세 보기</button></article>`).join('')||'<p>아직 실행 기록이 없습니다.</p>';
}
async function refresh(){const next=await api('/api/state');const changed=JSON.stringify(next)!==JSON.stringify(state);state=next;csrf=state.csrf;if(changed)render()}
function projectDialog(id){const form=$('project-form');form.reset();const p=state.projects.find(p=>p.id===id);form.elements.id.value=id||'';$('project-form-title').textContent=p?'프로젝트 설정':'프로젝트 연결';if(p){form.elements.name.value=p.name;form.elements.path.value=p.path;for(const key of ['retries','timeout','rules','model'])form.elements[key].value=p.config[key]??'';form.elements.allowed.value=p.config.allowed.join('\n');form.elements.commands.value=JSON.stringify(p.config.commands,null,2)}form.querySelector('.form-error').textContent='';$('project-dialog').showModal()}
function taskDialog(){if(!state.projects.length){toast('먼저 프로젝트를 연결해 주세요.');return projectDialog()}$('task-form').reset();$('task-project').innerHTML=state.projects.map(p=>`<option value="${p.id}">${escape(p.name)}</option>`).join('');$('task-form').querySelector('.form-error').textContent='';$('task-project').value=$('project-filter').value||state.projects[0].id;taskInfo();taskLengths();$('task-dialog').showModal()}
async function start(id,mode){if(mode==='codex'&&!confirm('Codex가 별도 작업 폴더에서 코드를 수정하고 등록된 검사 명령을 실행합니다. 계정 사용량이 소비됩니다. 실행할까요?'))return;try{const r=await api('/api/start',{task_id:id,mode});await refresh();await showRun(r.id)}catch(e){toast(e.message)}}
async function showRun(id){selectedRun=id;cursor=0;logLines=[];selectedTab='log';document.querySelectorAll('.detail-tabs button').forEach(b=>b.classList.toggle('selected',b.id==='tab-log'));if(!$('run-dialog').open)$('run-dialog').showModal();await updateRun()}
async function updateRun(){if(!selectedRun||!$('run-dialog').open)return;const requested=selectedRun;const r=await api(`/api/runs/${requested}?after=${cursor}`);if(selectedRun!==requested)return;lastRun=r;if(r.events.length){cursor=r.events.at(-1).seq;logLines.push(...r.events.map(e=>`[${new Date(e.created).toLocaleTimeString('ko-KR')}] ${e.kind}\n${e.message}`));logLines=logLines.slice(-1500)}
 $('run-title').textContent=state.tasks.find(t=>t.id===r.task_id)?.title||'실행 상세';$('run-meta').innerHTML=badge(r.status)+` <span class="help">${r.mode==='demo'?'데모 모드':'Codex 모드'} · 시도 ${r.attempt}</span>`;
 $('run-stages').innerHTML=Object.entries(stages).filter(([k])=>k!=='queued').map(([k,v])=>`<span class="stage ${r.stage===k?'current':''}">${v}</span>`).join('');
 $('run-summary').textContent=r.summary||'작업을 진행하고 있습니다.';$('run-workspace').textContent=r.workspace?`작업 폴더: ${r.workspace}`:'데모에서는 별도 소스 폴더를 만들지 않습니다.';
 $('stop-run').classList.toggle('hidden',r.status!=='running');$('download-report').href=`/api/runs/${r.id}/report`;
 if(selectedTab==='log')$('run-output').textContent=logLines.join('\n\n')||'로그를 기다리는 중입니다.';
 if(selectedTab==='result')$('run-output').textContent=JSON.stringify(r.result,null,2);
}
document.querySelectorAll('.nav').forEach(e=>e.onclick=()=>page(e.dataset.page));
document.querySelectorAll('.close-dialog').forEach(e=>e.onclick=()=>e.closest('dialog').close());
$('run-dialog').addEventListener('close',()=>{selectedRun=null});
$('new-project').onclick=()=>projectDialog();$('welcome-project').onclick=()=>projectDialog();$('new-task').onclick=taskDialog;$('project-filter').onchange=render;
$('apply-template').onclick=()=>{
 const kind=$('task-template').value,target=$('task-target').value.trim();
 if(!kind||!target){toast('작업 종류와 대상 파일·메뉴를 입력해 주세요.');return}
 const form=$('task-form');
 if((form.elements.prompt.value||form.elements.acceptance.value)&&!confirm('작성 중인 작업 제목·지시·완료 기준을 선택한 양식으로 바꿀까요?'))return;
 const rules='기존 시스템 구조·SQL ID·namespace·파라미터·업무 동작을 유지하세요. DB2 물성은 제외합니다. 자격 증명을 읽거나 출력하지 마세요. 실제 DB 및 외부 시스템에 쓰지 마세요. 허용 파일 범위를 벗어나야 하면 변경하지 말고 필요한 사항을 보고하세요.';
 const templates={
 analysis:{title:'메뉴·SQL 분석',prompt:'분석 담당으로 대상 메뉴의 조회·등록·수정·삭제 흐름과 연결 XML/SQL ID를 확인하세요. 근거 파일과 확인이 필요한 질문을 정리하세요. 소스는 수정하지 말고 harness-reports/analysis.md에 보고서를 작성하세요.',acceptance:'근거가 확인된 기능·SQL 목록, 미확인 항목, 질문 목록이 보고서에 있어야 합니다. 소스 변경 없음. 보고서 경로만 수정 허용 범위에 추가해야 합니다.'},
 conversion:{title:'PostgreSQL 문법 전환',prompt:'수정 담당으로 먼저 대상 SQL과 전환 필요성을 분석한 후, 필요한 MSSQL 문법만 PostgreSQL 문법으로 변경하세요. 이미 전환된 문법은 재수정하지 마세요. 자료형·NULL·날짜·문자열·반환 컬럼명 차이를 점검하세요. 등록된 검사를 실행하고 최종 응답에 변경 내용과 근거·남은 질문을 정리하세요.',acceptance:'변경은 지정한 SQL 문법에 한정합니다. XML 파싱 및 SQL ID·파라미터 보존을 확인하고 등록된 검사를 통과해야 합니다. 실제 DB·화면 검증을 수행하지 않았다면 명시하세요.'},
 audit:{title:'변경 결과 검수',prompt:'검수 담당으로 대상 파일을 읽고 SQL 문법·동적 조건·파라미터·반환값 위험을 확인하세요. 소스는 수정하지 말고 harness-reports/audit.md에 근거 위치, 문제점, 후속 테스트를 기록하세요. 이전 버전과 비교할 수 없다면 동등성 확인 완료라고 쓰지 마세요.',acceptance:'발견 사항마다 근거와 영향·확인 방법을 기록합니다. 문제가 없더라도 검수 범위와 한계를 명시합니다. 소스 변경 없음. 보고서 경로만 수정 허용 범위에 추가해야 합니다.'}
 };
 const t=templates[kind];form.elements.title.value=`${t.title} · ${target}`;form.elements.prompt.value=`대상: ${target}\n\n${t.prompt}\n\n작업 규칙:\n${rules}`;form.elements.acceptance.value=t.acceptance;
 taskLengths();toast('지시 초안을 채웠습니다. 프로젝트 허용 파일과 검사 명령도 확인하세요.');
};
$('demo-create').onclick=async()=>{try{await api('/api/demo',{});await refresh();toast('샘플을 준비했습니다. 작업 카드의 데모로 흐름을 확인하세요.')}catch(e){toast(e.message)}};
document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.run)await showRun(b.dataset.run);if(b.dataset.start)await start(b.dataset.start,'codex');if(b.dataset.demo)await start(b.dataset.demo,'demo');if(b.dataset.edit)projectDialog(b.dataset.edit)});
$('project-form').onsubmit=async e=>{e.preventDefault();const form=e.target;try{const data=Object.fromEntries(new FormData(form));data.allowed=data.allowed.split('\n').map(s=>s.trim()).filter(Boolean);data.commands=JSON.parse(data.commands);data.retries=Number(data.retries);data.timeout=Number(data.timeout);await api(data.id?`/api/projects/${data.id}`:'/api/projects',data);$('project-dialog').close();await refresh();toast('프로젝트 설정을 저장했습니다.')}catch(e){form.querySelector('.form-error').textContent=e.message}};
$('task-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/tasks',Object.fromEntries(new FormData(e.target)));$('task-dialog').close();await refresh();toast('작업을 등록했습니다. 실행은 작업 카드에서 별도로 시작하세요.')}catch(err){e.target.querySelector('.form-error').textContent=err.message}};
for(const name of ['log','diff','result'])$('tab-'+name).onclick=async()=>{selectedTab=name;document.querySelectorAll('.detail-tabs button').forEach(b=>b.classList.toggle('selected',b.id==='tab-'+name));if(name==='diff'){try{$('run-output').textContent=(await api(`/api/runs/${selectedRun}/diff`)).diff}catch(e){toast(e.message)}}else await updateRun()};
$('stop-run').onclick=async()=>{if(!confirm('현재 실행을 중단할까요? 변경 내용은 작업 폴더에 남습니다.'))return;try{await api('/api/stop',{run_id:selectedRun});toast('중단을 요청했습니다.');await refresh();await updateRun()}catch(e){toast(e.message)}};
async function poll(){if(polling)return;polling=true;try{await refresh();await updateRun();$('connection-note').textContent='마지막 확인: '+new Date().toLocaleTimeString('ko-KR');$('system-status').textContent=`Codex ${state.system.codex?'설치 확인':'미설치'} · Python ${state.system.python}`;}catch(e){$('system-status').textContent='서버 연결 끊김';$('connection-note').textContent='연결을 확인하고 새로고침하세요. 표시된 내용은 마지막으로 받은 상태입니다.';}finally{polling=false}}
poll();setInterval(()=>{if(!document.hidden)poll()},2000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll()});

function taskInfo(){const p=state.projects.find(p=>p.id===$('task-project').value);$('task-project-info').textContent=p?`허용 범위: ${p.config.allowed.join(', ')} · 검사 ${p.config.commands.length}개 · 재수정 ${p.config.retries}회`:''}
function taskLengths(){const f=$('task-form').elements;$('task-lengths').textContent=`제목 ${f.title.value.length}/200 · 지시 ${f.prompt.value.length}/20000 · 완료 기준 ${f.acceptance.value.length}/12000`}
$('task-project').onchange=taskInfo;$('task-form').addEventListener('input',taskLengths);
$('task-search').oninput=render;$('status-filter').onchange=render;
$('reset-filters').onclick=()=>{$('task-search').value='';$('status-filter').value='';$('project-filter').value='';render()};
$('refresh-now').onclick=poll;
$('copy-output').onclick=async()=>{try{await navigator.clipboard.writeText($('run-output').textContent);toast('내용을 복사했습니다.')}catch(e){toast('복사 권한이 없습니다. 내용을 선택해 복사해 주세요.')}};
for(const form of document.querySelectorAll('form')){const handler=form.onsubmit;if(!handler)continue;form.onsubmit=async e=>{if(form.dataset.saving){e.preventDefault();return}form.dataset.saving='1';const submit=form.querySelector('.modal-actions .primary');submit.disabled=true;try{await handler(e)}finally{delete form.dataset.saving;submit.disabled=false}}}
