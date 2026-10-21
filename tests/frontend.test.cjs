const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function boot(){
 const html=fs.readFileSync(path.join(__dirname,'../harness/static/index.html'),'utf8');
 const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],{
  value:'',textContent:'',innerHTML:'',dataset:{},classList:{add(){},remove(){},toggle(){}},
  addEventListener(){},showModal(){this.open=true},reset(){},querySelector(){return {textContent:''}},
 }]));
 const fields={};for(const k of ['title','prompt','acceptance'])fields[k]={value:''};
 elements.get('task-form').elements=fields;
 const project={id:'p',name:'Example',config:{allowed:['a.xml'],commands:[],retries:1,timeout:600}};
 const state={projects:[project],tasks:[{id:'t',project_id:'p',title:'Find me',prompt:'hello',acceptance:'test',status:'todo'}],runs:[],system:{codex:true,python:'3.12'},active:null,csrf:'test'};
 const context=vm.createContext({document:{hidden:false,getElementById(id){assert.ok(elements.has(id),`missing HTML id ${id}`);return elements.get(id)},querySelectorAll(){return []},addEventListener(){}},
  fetch:async()=>({ok:true,json:async()=>state}),setTimeout(){},clearTimeout(){},setInterval(){},confirm:()=>true,console,Date});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../harness/static/app.js'),'utf8'),context);
 return {elements,context,state};
}
test('task search and status filters affect the rendered board',async()=>{
 const {elements:e,context:c}=boot();await vm.runInContext('refresh()',c);
 assert.match(e.get('task-board').innerHTML,/Find me/);
 e.get('task-search').value='missing';vm.runInContext('render()',c);
 assert.doesNotMatch(e.get('task-board').innerHTML,/Find me/);
 e.get('task-search').value='';e.get('status-filter').value='failed';vm.runInContext('render()',c);
 assert.doesNotMatch(e.get('task-board').innerHTML,/Find me/);
 e.get('reset-filters').onclick();assert.match(e.get('task-board').innerHTML,/Find me/);
});
test('templates preserve scope restrictions and update counters',()=>{
 const {elements:e}=boot();e.get('task-template').value='audit';e.get('task-target').value='a.xml';
 e.get('apply-template').onclick();
 assert.match(e.get('task-form').elements.prompt.value,/DB2/);
 assert.match(e.get('task-form').elements.prompt.value,/소스는 수정하지/);
 assert.match(e.get('task-lengths').textContent,/12000/);
});
test('HTML escapes task content and displays acceptance',async()=>{
 const {elements:e,context:c,state}=boot();state.tasks[0].title='<script>bad</script>';
 await vm.runInContext('refresh()',c);
 assert.doesNotMatch(e.get('task-board').innerHTML,/<script>/);
 assert.match(e.get('task-board').innerHTML,/&lt;script&gt;/);
 assert.match(e.get('task-board').innerHTML,/test/);
});
