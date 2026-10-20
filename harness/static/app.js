let state={projects:[],tasks:[],runs:[]}, csrf='', selectedRun=null, selectedTab='log', lastRun=null, logLines=[], cursor=0, polling=false;
const $=id=>document.getElementById(id);
const labels={todo:'대기',running:'실행 중',verified:'소스·검사 통과',reviewed:'소스 검수 통과',failed:'검증 실패',blocked:'확인 필요',cancelled:'사용자 중단',interrupted:'종료로 중단',demo_done:'데모 완료',no_changes:'변경 없음'};
const stages={queued:'준비 대기',preflight:'경로 확인',worker:'Codex 수정',tests:'자동 검사',reviewer:'독립 검수'};
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge=s=>`<span class="badge ${['verified','reviewed'].includes(s)?'good':s==='running'?'running':s==='demo_done'?'demo':s==='todo'?'':'issue'}">${escape(labels[s]||s)}</span>`;
function toast(s){$('toast').textContent=s;$('toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.add('hidden'),6500)}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(data.error||'요청 실패');return data}
function page(name){document.querySelectorAll('.page').forEach(e=>e.classList.toggle('hidden',e.id!==`page-${name}`));document.querySelectorAll('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===name));$('breadcrumb').textContent='워크스페이스 / '+({overview:'작업 현황',projects:'프로젝트',history:'실행 기록',guide:'사용 안내'}[name])}
function projectName(id){return state.projects.find(p=>p.id===id)?.name||'프로젝트'}
function card(t){const r=state.runs.find(r=>r.task_id===t.id);return `<article class="task-card"><div class="project-name">${escape(projectName(t.project_id))}</div><h3>${escape(t.title)}</h3><p>${escape(t.prompt)}</p>${badge(t.status)}<div class="card-actions">${r?`<button class="secondary" data-run="${r.id}">결과 보기</button>`:''}<button class="primary" data-start="${t.id}" ${state.active?'disabled':''}>Codex 실행</button><button class="secondary" data-demo="${t.id}" ${state.active?'disabled':''}>데모</button></div></article>`}
function render(){
 $('system-status').textContent=`Codex ${state.system.codex?'연결 가능':'미설치'} · Python ${state.system.python}`;
 $('stat-total').textContent=state.tasks.length;$('stat-running').textContent=state.tasks.filter(t=>t.status==='running').length;
 $('stat-done').textContent=state.tasks.filter(t=>['verified','reviewed'].includes(t.status)).length;
 $('stat-issue').textContent=state.tasks.filter(t=>['failed','blocked','cancelled','interrupted','no_changes'].includes(t.status)).length;
 const filter=$('project-filter').value;
 $('project-filter').innerHTML='<option value="">모든 프로젝트</option>'+state.projects.map(p=>`<option value="${p.id}">${escape(p.name)}</option>`).join('');
 $('project-filter').value=filter;
 const tasks=state.tasks.filter(t=>!filter||t.project_id===filter);
 const columns=[['대기',t=>t.status==='todo'],['진행 · 확인 필요',t=>!['todo','verified','reviewed','demo_done'].includes(t.status)],['검수 · 데모 결과',t=>['verified','reviewed','demo_done'].includes(t.status)]];
 $('task-board').innerHTML=columns.map(([name,fn])=>{const list=tasks.filter(fn);return `<section class="column"><div class="column-title">${name}<span class="count">${list.length}</span></div>${list.map(card).join('')||'<p class="empty-column">등록된 작업이 없습니다</p>'}</section>`}).join('');
 $('welcome').classList.toggle('hidden',state.tasks.length>0);
 $('project-list').innerHTML=state.projects.map(p=>`<article class="project-card"><h3>${escape(p.name)}</h3><p class="path">${escape(p.path)}</p><dl><dt>수정 허용 범위</dt><dd>${escape(p.config.allowed.join('\n'))}</dd><dt>검사 명령</dt><dd>${p.config.commands.length}개</dd><dt>재수정 / 시간</dt><dd>${p.config.retries}회 / ${p.config.timeout}초</dd></dl><button class="secondary" data-edit="${p.id}">설정 수정</button></article>`).join('')||'<p>연결된 프로젝트가 없습니다. 프로젝트 연결 버튼으로 시작하세요.</p>';
 $('history-list').innerHTML=state.runs.map(r=>`<article class="history-row">${badge(r.status)}<div class="title"><h3>${escape(state.tasks.find(t=>t.id===r.task_id)?.title||r.task_id)}</h3><small>${escape(new Date(r.created).toLocaleString('ko-KR'))} · ${r.mode==='demo'?'데모':'Codex'} · 시도 ${r.attempt}</small></div><button class="secondary" data-run="${r.id}">상세 보기</button></article>`).join('')||'<p>아직 실행 기록이 없습니다.</p>';
}
async function refresh(){const next=await api('/api/state');const changed=JSON.stringify(next)!==JSON.stringify(state);state=next;csrf=state.csrf;if(changed)render()}
function projectDialog(id){const form=$('project-form');form.reset();const p=state.projects.find(p=>p.id===id);form.elements.id.value=id||'';$('project-form-title').textContent=p?'프로젝트 설정':'프로젝트 연결';if(p){form.elements.name.value=p.name;form.elements.path.value=p.path;for(const key of ['retries','timeout','rules','model'])form.elements[key].value=p.config[key]??'';form.elements.allowed.value=p.config.allowed.join('\n');form.elements.commands.value=JSON.stringify(p.config.commands,null,2)}form.querySelector('.form-error').textContent='';$('project-dialog').showModal()}
function taskDialog(){if(!state.projects.length){toast('먼저 프로젝트를 연결해 주세요.');return projectDialog()}$('task-form').reset();$('task-project').innerHTML=state.projects.map(p=>`<option value="${p.id}">${escape(p.name)}</option>`).join('');$('task-form').querySelector('.form-error').textContent='';$('task-dialog').showModal()}
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
$('demo-create').onclick=async()=>{try{await api('/api/demo',{});await refresh();toast('샘플을 준비했습니다. 작업 카드의 데모로 흐름을 확인하세요.')}catch(e){toast(e.message)}};
document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.run)await showRun(b.dataset.run);if(b.dataset.start)await start(b.dataset.start,'codex');if(b.dataset.demo)await start(b.dataset.demo,'demo');if(b.dataset.edit)projectDialog(b.dataset.edit)});
$('project-form').onsubmit=async e=>{e.preventDefault();const form=e.target;try{const data=Object.fromEntries(new FormData(form));data.allowed=data.allowed.split('\n').map(s=>s.trim()).filter(Boolean);data.commands=JSON.parse(data.commands);data.retries=Number(data.retries);data.timeout=Number(data.timeout);await api(data.id?`/api/projects/${data.id}`:'/api/projects',data);$('project-dialog').close();await refresh();toast('프로젝트 설정을 저장했습니다.')}catch(e){form.querySelector('.form-error').textContent=e.message}};
$('task-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/tasks',Object.fromEntries(new FormData(e.target)));$('task-dialog').close();await refresh();toast('작업을 등록했습니다.')}catch(err){e.target.querySelector('.form-error').textContent=err.message}};
for(const name of ['log','diff','result'])$('tab-'+name).onclick=async()=>{selectedTab=name;document.querySelectorAll('.detail-tabs button').forEach(b=>b.classList.toggle('selected',b.id==='tab-'+name));if(name==='diff'){try{$('run-output').textContent=(await api(`/api/runs/${selectedRun}/diff`)).diff}catch(e){toast(e.message)}}else await updateRun()};
$('stop-run').onclick=async()=>{try{await api('/api/stop',{run_id:selectedRun});toast('중단을 요청했습니다.');await refresh();await updateRun()}catch(e){toast(e.message)}};
async function poll(){if(polling)return;polling=true;try{await refresh();await updateRun()}catch(e){$('system-status').textContent='서버 연결 끊김';}finally{polling=false}}
poll();setInterval(poll,2000);
