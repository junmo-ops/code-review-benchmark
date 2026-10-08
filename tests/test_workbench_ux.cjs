const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const UX=require('../webui/static/presentation.js');
const c={status:'failed',report:{groups:[{cases:Array(5),scored:5,failed:0,timeouts:0},{cases:Array(5),scored:2,failed:3,timeouts:3}]}};
assert.equal(UX.experiment(c).label,'已结束 · 部分完成');assert.equal(UX.experiment(c).done,7);assert.equal(UX.experiment(c).timeouts,3);
assert.equal(UX.primary(undefined,null).label,'查看创建进度');
assert.equal(UX.caseStatus({status:'reviewing'},false),'已中断 · 需检查');assert.equal(UX.caseStatus({status:'pending'},false),'未执行');assert.equal(UX.caseStatus({status:'score_failed',error:'TimeoutError'},false),'评分超时');
const source=fs.readFileSync('webui/static/app.js','utf8'),nodes={},events={},pushed=[],opened=[];
assert.match(source,/r\?\.simulation&&r\.status==='simulation_complete'/);
const sandbox={URLSearchParams,location:{pathname:'/comparisons',search:'?experiment=compare-test&group=compare-test-g1&case=case1'},history:{pushState(_,__,path){pushed.push(path)},replaceState(_,__,path){pushed.push(path)}},document:{querySelectorAll(){return[]}},window:{addEventListener(e,f){events[e]=f}},$(s){return nodes[s]??={id:s.slice(1),open:false,close(){this.open=false}}},state:{comparisons:[{key:'compare-test'}],runs:[{key:'run1'}],jobs:[{key:'run1',id:'j1'}]},selected:null,comparisonSelected:null,evidenceSelected:null,toast(s){opened.push(['toast',s])},openRun(k,j){opened.push(['run',k,j])},openComparison(k,m){opened.push(['comparison',k,m])},openEvidence(k,g,c){opened.push(['evidence',k,g,c])},switchTab(t){opened.push(['tab',t])}};
vm.createContext(sandbox);vm.runInContext(source.slice(source.indexOf('const viewRoutes='),source.indexOf('function newRun')),sandbox);
sandbox.restoreRoute();assert.deepEqual(opened,[['comparison','compare-test','results'],['evidence','compare-test','compare-test-g1','case1']]);assert.equal(pushed.length,0);
opened.length=0;sandbox.location.pathname='/';sandbox.location.search='?run=run1&tab=log&job=j1';events.popstate();assert.deepEqual(opened,[['run','run1','j1'],['tab','log']]);
sandbox.writeRoute('models');assert.equal(pushed.at(-1),'/models');
const html=fs.readFileSync('webui/static/index.html','utf8');const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);
for(const match of source.matchAll(/\$\('#([\w-]+)'\)/g))assert.ok(ids.includes(match[1])||source.includes(`id="${match[1]}"`),`Missing DOM id ${match[1]}`);
console.log('Workbench partial results, case status, deep-link restoration, popstate and DOM references passed');
// Switching tools and suites must not submit unsupported case concurrency.
const planNodes={};
function planNode(s){return planNodes[s]??={value:'',hidden:false,disabled:false,textContent:'',querySelectorAll(){return[]}};}
const planContext={state:{access_role:'operator',profiles:[],catalog:[{id:'aacr',datasets:[{id:'pilot1.jsonl',count:1}]}]},$:planNode};
vm.createContext(planContext);
vm.runInContext(source.slice(source.indexOf('function updatePlans(){'),source.indexOf("for(const id of ['create-form'")),planContext);
planNode('#create-suite').value='aacr';planNode('#create-dataset').value='pilot1.jsonl';planNode('#create-reviewer').value='lcr';planNode('#create-concurrency').value='4';
planContext.updatePlans();assert.equal(planNode('#create-concurrency').disabled,false);assert.match(planNode('#create-plan').textContent,/案例并发 4/);
for(const [suite,reviewer] of [['aacr','server_agent'],['aacr','cline_skill'],['mcr','lcr'],['ccrab','lcr']]){
 planNode('#create-suite').value=suite;planNode('#create-reviewer').value=reviewer;planContext.updatePlans();assert.equal(planNode('#create-concurrency').disabled,true);assert.equal(planNode('#concurrency-field').hidden,true);
}
planNode('#create-suite').value='aacr';planNode('#create-reviewer').value='lcr';planContext.updatePlans();assert.equal(planNode('#create-concurrency').disabled,false);
console.log('Case concurrency visibility and execution plan passed');
