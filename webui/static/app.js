'use strict';
const $ = s => document.querySelector(s);
const token = $('meta[name="dashboard-token"]').content;
let state = null, filter = '', selected = null, tab = 'overview', selectedJob = '', reportText = '', fileText = '', loading = false;
let assetEdit = null, assetDelete = null;
const labels = {simulation_complete:'模拟完成·不计分',remote_wait_timeout:'等待超时·可恢复查询',remote_needs_attention:'远端任务需检查',complete_no_findings:'未检出 · 未修复',pending:'待运行',partial:'部分完成',partial_scored:'部分评分',reviewed:'待评分',scored:'已评分',complete:'已完成',failed:'失败',invalid_answer:'答卷无效',queued:'排队中',running:'运行中',cancelling:'停止中',cancelled:'已停止',succeeded:'已完成',interrupted:'曾中断',unreadable:'需检查',unfinished_check_required:'需检查',recoverable_check_required:'可尝试恢复',exited:'已退出',platform_timeout:'超时'};
const UX=window.BenchUX;
const actionName=j=>j.action==='create'?(j.start_after_create?'创建并开始测评':'保存配置'):(actions[j.action]||j.action);
const stageName=s=>{const [stage,...rest]=(s||'').split(' · ');return ({init:'保存测评配置',check:'检查运行条件','prepare-repos':'检查案例仓库',review:'代码检视',eval:'结果评分',report:'生成报告',run:'单例检视、修复与测试',export:'生成归档'}[stage]||stage||'等待执行')+(rest.length?' · '+rest.join(' · '):'');};
const actions = {comparison:'模型对比实验',create:'创建批次',check:'检查准备',preview:'预览',review:'检视',eval:'评分',report:'生成报告',export:'生成归档',recover:'从已有输出恢复结果',workflow:'检视并评分','compare-martian':'Martian 对照'};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = v => Number.isFinite(v) ? v.toFixed(3) : '—';
const badge = status => `<span class="badge ${esc(status)}">${esc(labels[status] || status)}</span>`;
const stamp = s => s ? new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const inSuite = (id, chosen) => id===chosen || (chosen==='swe_prbench_llm'&&id==='swe_prbench');
const isExperimentGroup = key => state.comparisons?.some(c=>c.report.groups.some(g=>g.key===key));
const suiteName = id => state?.catalog.find(c=>c.id===id)?.name || (id==='swe_prbench' ? 'SWE-PRBench（旧协议）' : id);
function toast(s){$('#toast').textContent=s;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,5000)}
async function api(path, data, method){const options={headers:{'X-Dashboard-Token':token}};if(data){options.headers['Content-Type']='application/json';options.method=method||'POST';options.body=JSON.stringify(data)}else if(method)options.method=method;const response=await fetch(path,options);let body;try{body=await response.json()}catch{throw Error('页面服务暂不可用')}if(!response.ok)throw Error(body.error || '操作失败');return body}
function options(node,values,wanted){const old=wanted ?? node.value;node.innerHTML=values.map(v=>`<option value="${esc(v.value)}">${esc(v.label)}</option>`).join('');if(values.some(v=>v.value===old))node.value=old;}
function availableModel(p){return p.id!=='environment'||p.review_ready===true}
function profileOptions(node,wanted){options(node,state.profiles.filter(p=>availableModel(p)&&(!p.internal||p.id===wanted)).map(p=>({value:p.id,label:`${p.label} · ${p.review_model}`})),wanted)}
async function refresh(){if(loading)return;loading=true;try{const incoming=await api('/api/state');const changed=JSON.stringify(incoming)!==JSON.stringify(state);state=incoming;$('#connection').classList.remove('disconnected');$('#connection').innerHTML=incoming.queue_mode?'<i></i> 服务器已连接':'<i></i> 本地服务已连接';$('#error-banner').hidden=!(incoming.queue_mode&&!incoming.worker_ready);if(changed)render();else if(selected&&tab==='log')loadLog();if(!routeReady)restoreRoute();}catch(e){$('#connection').classList.add('disconnected');$('#connection').innerHTML='<i></i> 连接已断开';$('#error-banner').hidden=false;$('#error-banner').textContent=e.message+'。请确认页面服务仍在运行；已有日志保留在项目中。'}finally{loading=false}}
const caseUploadLimit=()=>state?.case_assets?.upload_max_bytes??2*1024*1024*1024;
const caseUploadLimitLabel=()=>state?.case_assets?.upload_max_label??'2 GiB';
let caseUploading=false,caseUploadFeedback='选择文件后，点击“确认导入”。',repositoryUploadFeedback='选择包含 Git 历史的仓库 ZIP 后点击“导入仓库”。',repositoryUploadProgress=null;
let caseImportTask=null,caseImportPolling=false;
const caseImportBusy=()=>['queued','running'].includes(caseImportTask?.status);

async function pollCaseImport(){
 if(caseImportPolling)return;
 caseImportPolling=true;
 try{
  const result=await api('/api/cases/imports');
  if(JSON.stringify(result.task)===JSON.stringify(caseImportTask))return;
  caseImportTask=result.task;
  if(caseImportTask)caseUploadFeedback=caseImportTask.message;
  if(state)renderCaseUpload();
  if(caseImportTask?.status==='succeeded')await refresh();
 }catch(error){
  if(caseImportBusy()){caseUploadFeedback='读取导入进度失败：'+error.message+'。刷新页面后可继续查看。';if(state)renderCaseUpload()}
 }finally{caseImportPolling=false}
}

function renderCaseImport(){
 const progress=$('#case-import-progress');progress.hidden=!caseImportBusy();
 if(!caseImportBusy())return;
 if(caseImportTask.total>0){progress.max=caseImportTask.total;progress.value=caseImportTask.completed}
 else progress.removeAttribute('value');
}

function parseUploadReply(status,text){
 let result;
 try{result=JSON.parse(text)}catch{throw new Error(`上传请求返回 HTTP ${status}，响应不是 JSON；请查看平台与应用日志`)}
 if(status<200||status>=300)throw new Error(result.error||`上传失败（HTTP ${status}）`);
 return result;
}
function renderServerRuntime(){
 if(!state.queue_mode)return;
 const store=state.run_storage||{},ephemeral=state.storage_mode==='ephemeral';
 const message=runtimeStorageMessage(store,ephemeral);
 $('#runtime-note').innerHTML=`<span class="dot"></span> 内网服务器<p>${esc(message.title)}</p>`;
 $('#runtime-guide').textContent=message.text+'关闭浏览器或重启 Web 服务不会停止 Worker。中断任务不会自动重跑。';
 $('#error-banner').hidden=state.worker_ready;
 $('#error-banner').textContent=state.recovery_required?'Worker 曾中断，队列已暂停；可以查看历史结果。确认旧任务已停止后恢复队列。':'Worker 尚未就绪；任务会保留在队列中。';
 $('#ack-recovery').hidden=!state.recovery_required||state.access_role==='viewer';
}
function runtimeStorageMessage(store,ephemeral){
 if(ephemeral)return {title:'试运行 · 重建后数据丢失',text:'请及时导出结果；容器重建会丢失批次、模型配置与进度。 '};
 if(store.status==='error')return {title:'COS 同步异常 · 请勿重建容器',text:(store.message||'COS 运行数据同步失败，请勿重建容器。')+' '};
 if(store.status==='pending')return {title:'等待保存到 COS · 请暂缓更新',text:'运行数据正在等待同步；显示同步完成后再更新服务。 '};
 return {title:'任务与运行记录持久保存',text:`运行记录和任务日志自动备份到 COS；最近检查：${stamp(store.last_sync_at)}。 `};
}
function render(){renderModels();renderComparisons();renderCaseUpload();
 renderServerRuntime();
 $('#platform').textContent=state.queue_mode?state.platform+' · 内网服务器':state.platform==='darwin'?'macOS · 本机工作空间':state.platform+' · 本机工作空间';
 $('#suite-grid').innerHTML=state.catalog.map((c,i)=>{const count=state.runs.filter(r=>!isExperimentGroup(r.key)&&inSuite(r.suite,c.id)).length;const datasets=c.datasets;const n=Math.max(0,...datasets.map(d=>d.count));return `<button class="suite-card ${filter===c.id?'selected':''}" data-suite="${c.id}" aria-pressed="${filter===c.id}"><div class="suite-top"><span class="suite-icon">${['⌘','≋','◇','↻','▷'][i]}</span><span class="pill">${c.role}</span></div><strong>${c.name}</strong><p>${c.description}</p><div class="suite-bottom"><span>${c.id==='mcr'?n+' PR · '+(datasets[0]?.rounds||0)+' 轮':n+' 个可选案例'}</span><span><b>${count}</b> 个批次 ↗</span></div></button>`}).join('');
 renderRuns();renderJobs();renderActive();if(selected)renderDetail();
 $('#updated').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
 $('#new-run').disabled=(!!state.case_assets&&!state.case_assets.ready)||state.access_role==='viewer'||(!!state.active&&!state.queue_mode);
}
function renderRuns(){const term=$('#search').value.trim().toLowerCase();const runs=state.runs.filter(r=>!isExperimentGroup(r.key)&&(!filter||inSuite(r.suite,filter))&&(!term||((r.display_name||r.name)+' '+r.model+' '+suiteName(r.suite)).toLowerCase().includes(term)));$('#run-count').textContent=runs.length;$('#filter-label').textContent=filter?suiteName(filter)+' · 点击卡片取消筛选':'全部案例集';$('#runs-body').innerHTML=runs.length?runs.map(r=>{const live=state.jobs.find(j=>j.id===state.active&&j.key===r.key);return `<tr><td><button class="run-title" data-open="${esc(r.key)}">${esc(r.display_name||r.name)}</button><div class="subline"><span class="suite-label">${esc(suiteName(r.suite))}</span>${stamp(r.created)}</div></td><td class="model-cell">${esc(r.model||'—')}<small>${esc(r.reviewer==='llm_direct'?'直接模型':r.reviewer||'—')} · ${esc(r.evaluator||'—')}</small></td><td><div class="progress-value"><b>${r.completed} / ${r.total}</b><span>${r.suite==='mcr'?'轮':'例'}</span></div><div class="progress-track"><progress value="${r.completed}" max="${r.total||1}"></progress></div></td><td>${badge(live?live.status:r.status)}</td><td class="f1-value">${r.suite==='ccrab'?(r.metrics?.test_pass_rate===1?'测试通过':r.metrics?.test_pass_rate===0?'测试未通过':'未执行测试'):Number.isFinite(r.metrics?.f1)?'F1 '+pct(r.metrics.f1):r.status==='failed'?'无有效评分':'尚未评分'}</td><td><button class="text-button" data-open="${esc(r.key)}">查看 →</button></td></tr>`}).join(''):'<tr><td colspan="6" class="empty-row">没有符合条件的批次。试试其他关键词，或新建一次测评。</td></tr>'}
function renderActive(){const job=state.jobs.find(j=>j.id===state.active);$('#active-task').hidden=!job;if(job)$('#active-task').innerHTML=`<div class="active-panel"><span class="spinner"></span><div class="active-info"><strong>${esc(actionName(job))} · ${esc(job.key.split('/').pop())}</strong><small>${esc(stageName(job.step)||job.message)} · ${job.completed_steps}/${job.total_steps} 步</small></div><button class="text-button" data-job="${job.id}">查看日志 →</button><button class="secondary" data-stop="${job.id}" ${job.status==='cancelling'||state.access_role==='viewer'?'disabled':''}>停止</button></div>`}
function renderJobs(){$('#job-list').innerHTML=state.jobs.length?state.jobs.map(j=>`<article class="panel job-card"><div><h3>${esc(actionName(j))} · ${esc(j.key)}</h3><p>${stamp(j.created_at)} · ${j.completed_steps}/${j.total_steps} 步 · ${esc(stageName(j.step))}</p><div class="job-message">${esc(j.message)}</div></div>${badge(j.status)}${state.queue_mode&&state.access_role!=='viewer'&&j.status==='queued'?`<button class="secondary" data-stop="${j.id}">取消排队</button>`:''}<button class="secondary" data-job="${j.id}">查看日志</button></article>`).join(''):'<article class="panel guide"><h2>还没有执行记录</h2><p>在页面创建或运行批次后，任务和日志会显示在这里。之前在终端执行的原始日志仍可从批次详情查看。</p></article>'}
const viewRoutes={runs:'/',jobs:'/jobs',experiments:'/experiments',comparisons:'/comparisons',models:'/models',cases:'/cases',repositories:'/repositories',guide:'/guide'};
let routeRestoring=false,routeReady=false;
function viewAtLocation(){return Object.keys(viewRoutes).find(v=>viewRoutes[v]===(location.pathname.replace(/\/$/,'')||'/'))||'runs'}
function writeRoute(view,params={},replace=false){
 if(routeRestoring)return;
 const q=new URLSearchParams(Object.entries(params).filter(([,v])=>v!=null&&v!==''));
 const path=viewRoutes[view]+(q.size?'?'+q:'');
 if(path!==location.pathname+location.search)history[replace?'replaceState':'pushState'](null,'',path);
}
function closeForRoute(dialog){if(dialog.open){dialog.routeClosing=true;if(dialog.id==='detail-dialog')selected=null;if(dialog.id==='comparison-detail')comparisonSelected=null;if(dialog.id==='evidence-dialog')evidenceSelected=null;dialog.close()}}
function consumeRouteClose(dialog){const closing=dialog.routeClosing;dialog.routeClosing=false;return closing}
function showView(view,{fromHistory=false}={}){
 if(!Object.hasOwn(viewRoutes,view))view='runs';
 for(const v of Object.keys(viewRoutes))$('#'+v+'-view').hidden=v!==view;
 document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('active',n.dataset.view===view));
 const title={runs:'单模型测评',jobs:'执行记录',models:'模型配置',cases:'案例集管理',repositories:'仓库管理',experiments:'多模型实验',comparisons:'结果对比',guide:'使用指南'}[view];
 $('#page-label').textContent=title;document.title=title+' · Review Lab';
 if(!fromHistory)writeRoute(view);
}
function restoreRoute(){
 routeRestoring=true;
 try{
  const view=viewAtLocation(),q=new URLSearchParams(location.search);showView(view,{fromHistory:true});
  for(const id of ['detail-dialog','comparison-detail','evidence-dialog'])closeForRoute($('#'+id));
  selected=null;comparisonSelected=null;evidenceSelected=null;
  if(!state){routeReady=false;return;}
  routeReady=true;
  if(q.has('experiment')){
   const key=q.get('experiment'),c=state.comparisons.find(c=>c.key===key);
   if(!c){toast('该实验不存在或已移除');return;}
   openComparison(key,view==='comparisons'?'results':'execution');
   if(q.has('case')&&q.has('group'))openEvidence(key,q.get('group'),q.get('case'));
  }else if(q.has('run')){
   const key=q.get('run');if(!state.runs.some(r=>r.key===key)&&!state.jobs.some(j=>j.key===key)){toast('该批次不存在或已移除');return;}
   const job=state.jobs.find(j=>j.id===q.get('job')&&j.key===key)?.id||'';
   openRun(key,job);switchTab(['overview','log','report','files'].includes(q.get('tab'))?q.get('tab'):'overview');
  }
 }finally{routeRestoring=false;}
}
window.addEventListener('popstate',restoreRoute);
function renderCaseUpload(){
 const assets=state.case_assets,editable=state.access_role!=='viewer'&&!state.active&&!caseImportBusy();
 renderResourceAccess(assets);renderCaseDatasetTable(assets,editable);
 if(!assets)return;
 $('#case-upload-status').textContent=assets.message;
 $('#case-upload-form').hidden=state.access_role==='viewer';
 renderRepositoryTable(assets,editable);renderRepositoryUploadControls();renderCaseUploadControls();
 if(!assets.ready)$('#new-comparison').disabled=true;
}
function renderResourceAccess(assets){
 $('#case-upload-panel').hidden=!assets||state.access_role==='viewer';
 $('#case-upload-disabled').hidden=!!assets&&state.access_role!=='viewer';
 if(!assets||state.access_role==='viewer')$('#case-upload-disabled').textContent=state.access_role==='viewer'?'当前账号为只读权限，可查看和使用案例集，不能上传、修改或删除。':'当前工作台未开启案例集管理；仍可查看和使用已登记的案例集。';
 $('#repository-upload-panel').hidden=!assets||state.access_role==='viewer';
 $('#repository-upload-disabled').hidden=!!assets&&state.access_role!=='viewer';
 if(!assets||state.access_role==='viewer')$('#repository-upload-disabled').textContent=state.access_role==='viewer'?'当前账号为只读权限，可查看仓库资源，不能上传、更新或删除。':'当前工作台未开启仓库管理；仍可查看已有仓库。';
}
function renderCaseDatasetTable(assets,editable){
 const datasets=state.catalog.flatMap(suite=>suite.datasets.map(dataset=>({...dataset,suite:suite.name,suiteId:suite.id})));
 const uploaded=new Map((assets?.datasets||[]).map(dataset=>[dataset.id,dataset]));
 $('#case-dataset-count').textContent=datasets.length;
 $('#case-dataset-body').innerHTML=datasets.length?datasets.map(d=>{const managed=uploaded.get(d.id);return `<tr><td>${esc(d.name||d.suite+' · '+(d.count===1?'单例验证':d.count+' 例案例集'))}<div class="subline">${esc(d.id)}</div></td><td>${esc(d.suite)}</td><td>${d.count}${d.rounds?' PR / '+d.rounds+' 轮':' 例'}</td><td>${managed?'网页导入':'内置'}</td><td>${managed?`<button class="text-button" data-edit-case="${esc(d.id)}" data-name="${esc(managed.name)}" ${editable?'':'disabled'}>编辑</button> <button class="text-button danger-text" data-delete-case="${esc(d.id)}" data-name="${esc(managed.name)}" ${editable?'':'disabled'}>删除</button> · `:''}<button class="text-button" data-case-suite="${esc(d.suiteId)}" data-case-dataset="${esc(d.id)}" ${state.access_role==='viewer'||(!!assets&&!assets.ready)||(!!state.active&&!state.queue_mode)?'disabled':''}>新建测评 →</button></td></tr>`}).join(''):'<tr><td colspan="5" class="empty-row">还没有可用案例集。</td></tr>';
}
function renderRepositoryTable(assets,editable){
 const repositories=assets.repositories||[];
 $('#repository-count').textContent=repositories.length;
 $('#repository-body').innerHTML=repositories.length?repositories.map(repo=>`<tr><td>${esc(repo.repo)}<div class="subline">${esc(repo.slug)}</div></td><td>${(repo.versions||[]).map(version=>`<div class="repository-version"><code>${esc(version.sha256.slice(0,12))}</code><span>${version.current?'当前版本':'历史版本'}</span><small>${formatBytes(version.size)}</small></div>`).join('')}</td><td><button class="text-button" data-update-repository="${esc(repo.slug)}" ${editable?'':'disabled'}>更新版本</button> <button class="text-button danger-text" data-delete-repository="${esc(repo.slug)}" data-name="${esc(repo.repo)}" ${editable?'':'disabled'}>删除</button></td></tr>`).join(''):'<tr><td colspan="3" class="empty-row">还没有独立上传的仓库资源。</td></tr>';
}
function renderRepositoryUploadControls(){
 $('#repository-upload-form').hidden=state.access_role==='viewer';
 const repositoryFiles=Array.from($('#repository-upload-file').files),repositoryFileError=validateRepositoryFiles(repositoryFiles);
 $('#repository-upload-file').disabled=caseUploading||caseImportBusy()||!!state.active;$('#repository-upload-select').disabled=caseUploading||caseImportBusy()||!!state.active;
 $('#repository-upload-submit').disabled=caseUploading||caseImportBusy()||!!state.active||!repositoryFiles.length||!!repositoryFileError;
 $('#repository-upload-submit').textContent=caseUploading?'正在导入…':repositoryFiles.length>1?`导入 ${repositoryFiles.length} 个仓库`:'导入仓库';
 $('#repository-upload-select').textContent=repositoryFiles.length?'更换文件':'选择仓库 ZIP';
 $('#repository-upload-filename').textContent=repositoryFiles.length===1?repositoryFiles[0].name:repositoryFiles.length?`已选择 ${repositoryFiles.length} 个 ZIP 文件`:'尚未选择文件';
 $('#repository-upload-hint').textContent=repositoryFileError|| (repositoryFiles.length?`${formatBytes(repositoryFiles.reduce((sum,file)=>sum+file.size,0))} · 将按顺序逐个导入，每个文件上限 ${caseUploadLimitLabel()}`:'ZIP 内需包含仓库的 .git 目录；支持一次选择多个 ZIP，单文件上限 '+caseUploadLimitLabel());
 $('#repository-upload-feedback').textContent=repositoryUploadFeedback;
 renderRepositoryProgress();
}
function renderCaseUploadControls(){
 const file=$('#case-upload-file').files[0];
 $('#case-upload-file').disabled=caseUploading||caseImportBusy()||!!state.active;$('#case-upload-select').disabled=caseUploading||caseImportBusy()||!!state.active;
 $('#case-upload-submit').disabled=caseUploading||caseImportBusy()||!!state.active||!file||file.size>caseUploadLimit()||!file.name.toLowerCase().endsWith('.zip');
 $('#case-upload-submit').textContent=caseImportBusy()?'后台导入中…':caseUploading?'正在上传…':'确认导入';
 $('#case-upload-select').textContent=file?'更换文件':'选择 ZIP 文件';
 $('#case-upload-filename').textContent=file?file.name:'尚未选择文件';
 $('#case-upload-filehint').textContent=file?(file.size/1024/1024).toFixed(2)+' MiB · '+(caseUploading?'正在上传':'待导入'):'纯案例配置包不含 Git bundle，最大 '+caseUploadLimitLabel();
 $('#case-upload-feedback').textContent=caseUploadFeedback;
 renderCaseImport();
}
function formatBytes(size){if(!Number.isFinite(size)||size<=0)return '—';return size>=1073741824?(size/1073741824).toFixed(1)+' GiB':(size/1048576).toFixed(1)+' MiB'}
function validateRepositoryFiles(files){if(!files.length)return '';if(files.some(file=>!file.name.toLowerCase().endsWith('.zip')))return '请选择 ZIP 格式的仓库包。';if(files.some(file=>file.size>caseUploadLimit()))return `单个仓库 ZIP 不得超过 ${caseUploadLimitLabel()}。`;return ''}
function renderRepositoryProgress(){
 const progress=$('#repository-upload-progress'),fill=$('#repository-upload-progress-fill'),text=$('#repository-upload-progress-text'),value=$('#repository-upload-progress-value');
 const current=caseUploading?repositoryUploadProgress:null;progress.hidden=!current;if(!current)return;
 const transferring=current.phase==='upload'&&current.total>0,percent=transferring?Math.min(100,Math.floor(current.loaded/current.total*100)):0;
 const batch=`第 ${current.index+1}/${current.count} 个：${current.fileName}`;
 text.textContent=transferring?`${batch} 正在上传`:`${batch} 已上传，正在检查 Git 历史并整理仓库资源`;
 value.textContent=transferring?percent+'%':`${current.index+1}/${current.count}`;
 fill.classList.toggle('indeterminate',!transferring);fill.style.width=transferring?percent+'%':`${Math.max(8,(current.index+1)/current.count*100)}%`;
 const track=progress.querySelector('[role="progressbar"]');if(transferring)track.setAttribute('aria-valuenow',String(percent));else track.removeAttribute('aria-valuenow');
}
function uploadRepositoryZip(file,batch){
 return new Promise((resolve,reject)=>{
  const request=new XMLHttpRequest();request.open('POST','/api/repositories/upload');request.setRequestHeader('X-Dashboard-Token',token);request.setRequestHeader('Content-Type','application/zip');
  request.upload.addEventListener('progress',event=>{repositoryUploadProgress={...batch,phase:'upload',loaded:event.loaded,total:event.lengthComputable?event.total:file.size};renderRepositoryProgress()});
  request.upload.addEventListener('load',()=>{repositoryUploadProgress={...batch,phase:'processing'};repositoryUploadFeedback=`${batch.fileName} 上传完成，服务器正在解压、校验提交并整理 Git 历史。`;$('#repository-upload-feedback').textContent=repositoryUploadFeedback;renderRepositoryProgress()});
  request.addEventListener('load',()=>{let result;try{result=JSON.parse(request.responseText)}catch{reject(new Error('服务器返回了非 JSON 响应，请检查平台上传大小限制和超时配置'));return}if(request.status<200||request.status>=300){reject(new Error(result.error||'仓库上传失败'));return}resolve(result)});
  request.addEventListener('error',()=>reject(new Error('仓库上传连接中断，请检查网络和页面服务状态')));request.addEventListener('abort',()=>reject(new Error('仓库上传已中断')));request.send(file);
 });
}
async function uploadCases(event){
 event.preventDefault();const file=$('#case-upload-file').files[0];if(!file||caseUploading||caseImportBusy())return;
 if(file.size>caseUploadLimit()){caseUploadFeedback='案例 ZIP 上限为 '+caseUploadLimitLabel();renderCaseUpload();return;}
 caseUploading=true;renderCaseUpload();
 caseUploadFeedback='正在上传、校验并准备案例，请稍候…';renderCaseUpload();
 try{
  const response=await fetch('/api/cases/upload',{method:'POST',headers:{'X-Dashboard-Token':token,'Content-Type':'application/zip'},body:file});
  const result=parseUploadReply(response.status,await response.text());
  caseImportTask=result.task||null;
  caseUploadFeedback=result.message;$('#case-upload-file').value='';toast(caseImportTask?'案例包已接收，后台导入中':'案例集已导入');await refresh();
 }catch(error){caseUploadFeedback=error.message}
 finally{caseUploading=false;renderCaseUpload()}
}
$('#case-upload-form').addEventListener('submit',uploadCases);
$('#case-upload-select').addEventListener('click',()=>$('#case-upload-file').click());
$('#case-upload-file').addEventListener('change',()=>{
 const file=$('#case-upload-file').files[0];
 caseUploadFeedback=!file?'选择文件后，点击“确认导入”。':!file.name.toLowerCase().endsWith('.zip')?'请选择 ZIP 格式的案例配置包。':file.size>caseUploadLimit()?'文件超过 '+caseUploadLimitLabel()+'，请调整上传上限或选择较小的案例包。':'已选择文件，确认导入后将检查仓库资源和提交。';
 renderCaseUpload();
});
async function uploadRepositories(event){
 event.preventDefault();const files=Array.from($('#repository-upload-file').files);if(!files.length||caseUploading)return;
 const invalid=validateRepositoryFiles(files);if(invalid){repositoryUploadFeedback=invalid;renderCaseUpload();return;}
 let completed=0;caseUploading=true;repositoryUploadFeedback=`准备按顺序导入 ${files.length} 个仓库 ZIP…`;renderCaseUpload();
 try{
  for(let index=0;index<files.length;index++){
   const file=files[index],batch={index,count:files.length,fileName:file.name};
   repositoryUploadProgress={...batch,phase:'upload',loaded:0,total:file.size};repositoryUploadFeedback=`正在导入第 ${index+1}/${files.length} 个：${file.name}`;renderCaseUpload();
   try{await uploadRepositoryZip(file,batch);completed++}catch(error){throw new Error(`第 ${index+1}/${files.length} 个文件“${file.name}”导入失败；此前已完成 ${completed} 个。${error.message}`)}
  }
  repositoryUploadProgress=null;repositoryUploadFeedback=`已成功导入 ${completed} 个仓库 ZIP。`;$('#repository-upload-file').value='';toast(`已导入 ${completed} 个仓库`);await refresh();
 }catch(error){repositoryUploadFeedback=error.message;if(completed)await refresh()}
 finally{caseUploading=false;repositoryUploadProgress=null;renderCaseUpload()}
}
$('#repository-upload-form').addEventListener('submit',uploadRepositories);
$('#repository-upload-select').addEventListener('click',()=>$('#repository-upload-file').click());
$('#repository-upload-file').addEventListener('change',()=>{
 const files=Array.from($('#repository-upload-file').files),invalid=validateRepositoryFiles(files);
 repositoryUploadFeedback=invalid||(!files.length?'选择一个或多个包含 Git 历史的仓库 ZIP 后点击“导入仓库”。':`${files.length} 个 ZIP 将按顺序导入；每个文件的 .git 历史都会在服务器校验并整理后加入资源库。`);
 renderCaseUpload();
});
$('#case-dataset-body').addEventListener('click',event=>{
 const edit=event.target.closest('[data-edit-case]');if(edit){openAssetEdit('case',edit.dataset.editCase,edit.dataset.name);return;}
 const remove=event.target.closest('[data-delete-case]');if(remove){openAssetDelete('case',remove.dataset.deleteCase,remove.dataset.name);return;}
 const button=event.target.closest('[data-case-dataset]');if(!button||button.disabled)return;
 newRun();if(!$('#create-dialog').open)return;
 $('#create-suite').value=button.dataset.caseSuite;createFields(true);
 $('#create-dataset').value=button.dataset.caseDataset;updatePlans();
});
$('#repository-body').addEventListener('click',event=>{
 const update=event.target.closest('[data-update-repository]');
 if(update){$('#repository-upload-file').click();return;}
 const remove=event.target.closest('[data-delete-repository]');
 if(remove)openAssetDelete('repository',remove.dataset.deleteRepository,remove.dataset.name);
});
function openAssetEdit(type,id,name){assetEdit={type,id};$('#asset-edit-title').textContent='编辑案例集名称';$('#asset-edit-name').value=name;$('#asset-edit-error').textContent='';$('#asset-edit-dialog').showModal();$('#asset-edit-name').focus()}
function openAssetDelete(type,id,name){assetDelete={type,id};$('#asset-delete-message').textContent=type==='case'?`确认删除案例集“${name}”？`:`确认删除仓库“${name}”及其所有版本？`;$('#asset-delete-confirm').disabled=false;$('#asset-delete-dialog').showModal()}
async function mutateAsset(endpoint,payload){const result=await api(endpoint,payload);toast(result.message);await refresh()}
$('#asset-edit-form').addEventListener('submit',async event=>{
 event.preventDefault();if(!assetEdit)return;
 const button=$('#asset-edit-form button[type="submit"]');button.disabled=true;
 try{await mutateAsset('/api/cases/manage',{action:'rename',id:assetEdit.id,name:$('#asset-edit-name').value});$('#asset-edit-dialog').close()}
 catch(error){$('#asset-edit-error').textContent=error.message}
 finally{button.disabled=false}
});
$('#asset-edit-close').addEventListener('click',()=>$('#asset-edit-dialog').close());
$('#asset-edit-cancel').addEventListener('click',()=>$('#asset-edit-dialog').close());
$('#asset-delete-close').addEventListener('click',()=>$('#asset-delete-dialog').close());
$('#asset-delete-cancel').addEventListener('click',()=>$('#asset-delete-dialog').close());
$('#asset-delete-confirm').addEventListener('click',async()=>{
 if(!assetDelete)return;const button=$('#asset-delete-confirm');button.disabled=true;
 try{const endpoint=assetDelete.type==='case'?'/api/cases/manage':'/api/repositories/manage';const payload=assetDelete.type==='case'?{action:'delete',id:assetDelete.id}:{action:'delete',slug:assetDelete.id};await mutateAsset(endpoint,payload);$('#asset-delete-dialog').close()}
 catch(error){$('#asset-delete-message').textContent=error.message;button.disabled=false}
});
function datasetLabel(d,suite){return d.name?`${d.name} · ${d.count} 例`:(d.count===1?'流程验证':d.count<=5?'小规模对照':'案例测评')+' · '+d.count+(suite==='mcr'?' PR / '+d.rounds+' 轮':' 例')}
function newRun(){if(!state)return;if(state.case_assets&&!state.case_assets.ready){toast('请先上传并准备案例 ZIP');return;}$('#create-form').reset();$('#create-error').textContent='';options($('#create-suite'),state.catalog.map(c=>({value:c.id,label:c.name+' · '+c.role})),filter||'aacr');profileOptions($('#create-profile'),'model.local.json');judgeOptions();createFields(true);$('#create-dialog').showModal();}
function createFields(rename=false){const id=$('#create-suite').value;const suite=state.catalog.find(c=>c.id===id);options($('#create-dataset'),suite.datasets.map(d=>({value:d.id,label:datasetLabel(d,id)})));options($('#create-reviewer'),id==='mcr'?[{value:'llm_direct',label:'直接模型（Defect Card）'},{value:'lcr',label:'LCR（可检视，暂不支持缺陷状态判断）'}]:[{value:'lcr',label:'LCR CLI'},...(id==='ccrab'?[]:[{value:'server_agent',label:'服务端 Agent（协议草案）'},...(id==='aacr'?[{value:'cline_skill',label:'Cline + Skill（Mac 单例试跑）'}]:[])])]);options($('#create-evaluator'),id==='ccrab'?[{value:'executable',label:'发布测试（DeepSeek 修复适配）'}]:id==='mcr'?[{value:'original',label:'MCR 一对一 + 状态'}]:[{value:'martian',label:'Martian 语义评分（调用裁判模型）'},...(id==='codefuse_cr'?[]:[{value:'original',label:'案例集原始大模型评分'}])]);$('#create-judge-field').hidden=id==='ccrab';$('#create-judge').disabled=id==='ccrab';$('#token-field').hidden=id!=='mcr';$('#timeout-field').hidden=['mcr','ccrab'].includes(id);$('#retry-field').hidden=id==='ccrab'||id==='mcr'||$('#create-reviewer').value!=='lcr';if(rename){const date=new Date();const value=[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('')+'-'+[date.getHours(),date.getMinutes(),date.getSeconds()].map(v=>String(v).padStart(2,'0')).join('');$('#create-name').value=(id==='swe_prbench_llm'?'swe':id)+'-'+value;}$('#create-note').textContent=id==='ccrab'?'单例实验：LCR 检视 → DeepSeek 修复 → 容器发布测试。固定检视时限 10 分钟；无意见则跳过修复。不使用语义裁判，不对比官方榜单。':id==='mcr'?'MCR 按轮次回放。直接模型与 LCR 分开报告；LCR 每轮沿用固定 10 分钟任务时限。':'案例顺序执行，先检查环境，再检视和评分。';$('#create-note').textContent+=' 点击“创建并开始”会调用模型；仅保存配置不会。';profileHint();updatePlans()}
function judgeOptions(wanted){
 const profiles=state.profiles.filter(p=>!p.internal&&availableModel(p));
 const choices=profiles.map(p=>({value:p.id,label:`${p.label} · ${p.review_model}`}));
 for(const p of profiles)if(p.judge_model&&p.judge_model!=='未设置'&&!profiles.some(other=>other.review_model===p.judge_model))choices.push({value:'legacy-judge:'+p.id,label:p.judge_model+' · 沿用旧裁判配置'});
 options($('#create-judge'),choices,wanted||'model.local.json');
}
function profileHint(){const p=state.profiles.find(p=>p.id===$('#create-profile').value);$('#profile-hint').textContent=p&&$('#create-suite').value==='ccrab'?`检视与修复：${p.review_model} · 当前仅支持 DeepSeek，不需要裁判配置`:p?`负责代码检视${p.review_ready?'':' · 接口信息未齐全'}`:'';}
async function createRun(event){event.preventDefault();const values=Object.fromEntries(new FormData(event.target));if(!updatePlans())return;values.action='create';values.start_after_create=event.submitter?.value==='start';delete values.intent;values.timeout_minutes=Number(values.timeout_minutes);values.max_tokens=Number(values.max_tokens);values.case_jobs=Number(values.case_jobs??1);values.max_case_retries=Number(values.max_case_retries??0);if(values.reviewer!=='lcr')values.max_case_retries=0;$('#create-form').querySelectorAll('[type=submit]').forEach(b=>b.disabled=true);try{const result=await api('/api/jobs',values);$('#create-dialog').close();toast(values.start_after_create?'测评已提交，将先检查环境':'配置保存已提交，不调用模型');await refresh();openRun(result.key,result.job_id)}catch(e){$('#create-error').textContent=e.message}finally{$('#create-form').querySelectorAll('[type=submit]').forEach(b=>b.disabled=false)}}
function openRun(key,job=''){if(state.comparisons?.some(c=>c.key===key)){openComparison(key);return;}closeForRoute($('#comparison-detail'));closeForRoute($('#evidence-dialog'));selected=key;selectedJob=job;tab=job?'log':'overview';reportText='';fileText='';$('#detail-case').innerHTML='<option value="">全部案例 / 轮次</option>';$('#detail-retry').checked=false;$('#detail-profile').innerHTML='';$('#report-select').innerHTML='';$('#file-select').innerHTML='';$('#log-select').innerHTML='';renderDetail();if(!$('#detail-dialog').open)$('#detail-dialog').showModal();writeRoute(viewAtLocation(),{run:key,tab,job:job||undefined});loadTab()}
function renderDetail(){const r=state.runs.find(r=>r.key===selected);if(!r){$('#detail-name').textContent=selected?.split('/').pop()||'正在创建';$('#detail-eyebrow').textContent='BENCHMARK';$('#detail-meta').textContent='正在保存配置；排队时可在执行记录查看。';$('#detail-metrics').innerHTML='';$('#case-list').innerHTML='';$('#stage-actions').innerHTML='';}else{
 $('#detail-name').textContent=r.display_name||r.name;$('#detail-eyebrow').textContent=suiteName(r.suite)+' / '+(r.suite==='mcr'?'MULTI-ROUND':'CODE REVIEW');$('#detail-meta').textContent=`${r.reviewer==='llm_direct'?'直接模型':r.reviewer==='server_agent'?'服务端 Agent'+(r.simulation?'（模拟，不计分）':''):r.reviewer==='cline_skill'?'Cline + Skill':'LCR'} · ${r.model}　裁判：${r.judge_model} / ${r.evaluator}　${r.completed}/${r.total} ${r.suite==='mcr'?'轮':'例'}检视完成${r.reviewer==='lcr'&&!['mcr','ccrab'].includes(r.suite)?'　案例并发：'+(r.case_jobs??1):''}`;
 $('#detail-dialog .case-options').hidden=r.suite==='ccrab';
 $('#detail-retry').closest('label').hidden=r.reviewer==='server_agent';
 if(r.reviewer==='server_agent')$('#detail-retry').checked=false;
 $('#download-export').hidden=r.suite==='ccrab';

 $('#detail-notice').textContent=r.reviewer==='server_agent'?'服务端 Agent：停止仅结束本地等待，不会取消远端任务。使用“恢复查询原任务”继续获取结果。'+(r.simulation?'当前为模拟批次，不执行真实检视、不评分。':'以服务回传的模型与提交范围校验答卷。'):r.suite==='ccrab'?'先验证测试对照，再检视、修复和测试。无意见时跳过修复；失败不计分。每批次执行一次，不自动重试；历史结果仍可查看。':'检视与语义评分会调用模型。已完成答卷会跳过；失败时保留日志并停止后续步骤。';
 const wanted=$('#detail-profile').value||r.profile||state.profiles.find(p=>p.review_model===r.model&&p.judge_model===r.judge_model)?.id||'model.local.json';profileOptions($('#detail-profile'),wanted);$('#detail-profile').disabled=!!state.profiles.find(p=>p.id===wanted)?.internal;
 options($('#detail-case'),[{value:'',label:'全部案例 / 轮次'},...r.cases.map(c=>({value:c.id,label:c.id+' · '+(labels[c.status]||c.status)}))]);
 $('#detail-metrics').innerHTML=(r.suite==='ccrab'?[['检视完成',`${r.completed} / ${r.total}`],['修复执行',r.repair_executed?'已执行':'未执行'],['修复后测试',r.metrics?.test_pass_rate===1?'通过':r.metrics?.test_pass_rate===0?'未通过':'未执行'],['结果类型','发布测试']]:[['检视完成',`${r.completed} / ${r.total}`],['精确率（Precision）',pct(r.metrics?.precision)],['召回率（Recall）',pct(r.metrics?.recall)],['F1',pct(r.metrics?.f1)]]).map(([label,v])=>`<div class="metric"><small>${label}</small><strong>${v}</strong></div>`).join('');
 if(r.suite==='mcr')for(const [field,label] of [['state_accuracy','缺陷状态准确率'],['transition_accuracy','状态转移准确率'],['state_coverage','状态覆盖率']])if(Number.isFinite(r.metrics?.[field]))$('#detail-metrics').insertAdjacentHTML('beforeend',`<div class="metric"><small>${label}</small><strong>${pct(r.metrics[field])}</strong></div>`);
 const stages=r.suite==='ccrab'?['check']:r.suite==='mcr'?['check','review','eval','report','compare-martian','export']:['check','preview','review','recover','eval','report','export'];$('#stage-actions').innerHTML=stages.filter(action=>!r.simulation||!['eval','report'].includes(action)).map(action=>`<button class="secondary run-action" data-action="${action}" ${state.access_role==='viewer'||(state.active&&!state.queue_mode)||(r.compatibility_warning&&(action!=='export'||r.suite==='mcr'))?'disabled':''}>${esc(r.reviewer==='server_agent'&&action==='recover'?'恢复查询原任务':actions[action])}</button>`).join('');
 $('#case-list').innerHTML=r.cases.map(c=>`<div class="case-row"><div><strong>${esc(c.id)}</strong><small>${esc(c.repo)}${c.scored?' · 已有轮次评分':''}</small></div>${badge(c.status)}</div>`).join('');
 options($('#report-select'),r.artifacts.filter(a=>a.report).sort((a,b)=>(a.path.includes('/')?1:0)-(b.path.includes('/')?1:0)).map(a=>({value:a.path,label:a.path})));
 options($('#file-select'),r.artifacts.map(a=>({value:a.path,label:a.path+' · '+Math.ceil(a.size/1024)+' KB'})));
 }
 const jobs=state.jobs.filter(j=>j.key===selected);
 const latest=jobs.find(j=>['queued','running','cancelling'].includes(j.status))||jobs[0];
 const choice=r?.simulation&&r.status==='simulation_complete'?{label:'查看模拟证据',view:'files'}:UX.primary(r,latest);const primary=$('#primary-action');primary.textContent=choice.label;primary.dataset.nextAction=choice.action||'';primary.dataset.nextView=choice.view||'';
 primary.disabled=!!choice.action&&(state.access_role==='viewer'||(!state.queue_mode&&!!state.active)||!r);
 const phase=latest&&['queued','running','cancelling'].includes(latest.status)?stageName(latest.step):r?.status==='reviewed'?'检视完成 · 等待评分':r?.status==='scored'?'检视与评分完成':r?.status==='complete_no_findings'?'检视完成 · 修复未执行':'等待操作';
 $('#run-summary').innerHTML=`<strong>${esc(phase)}</strong><p>${esc(latest?.status==='queued'?'任务已排队，尚未执行。':latest?.status==='cancelling'?'正在停止任务，已保存的结果会保留。':latest?.status==='running'?`正在${stageName(latest.step)}。${r?'已保存 '+r.completed+'/'+r.total+' 份完整答卷。':''}当前阶段尚未结束。`:r?(r.simulation&&r.status==='simulation_complete'?'模拟协议流程已完成；未执行真实检视，未生成评分。':UX.result(r)):'测评配置正在排队保存。尚未开始检视。')}</p>${latest?`<div class="runtime-facts"><span>本次操作耗时：${esc(UX.duration(latest.elapsed_seconds))}</span><span>日志最近更新：${stamp(latest.last_output_at)}</span></div><small>日志更新时间不代表模型内部进度；没有可靠数据时不估算剩余时间。</small>`:''}${latest&&['failed','cancelled','interrupted'].includes(latest.status)?`<p class="failure-note">${esc(latest.message)}　已保存的结果保留；先查看原因，再决定恢复或重跑。</p>`:''}`;
options($('#log-select'),jobs.map(j=>({value:j.id,label:stamp(j.created_at)+' · '+actionName(j)+' · '+(labels[j.status]||j.status)})),selectedJob||$('#log-select').value);selectedJob=$('#log-select').value;
 const active=state.jobs.find(j=>j.id===state.active&&j.key===selected);$('#detail-stop').hidden=!active;$('#detail-stop').disabled=state.access_role==='viewer'||active?.status==='cancelling';document.querySelectorAll('.detail-toolbar .run-action').forEach(n=>n.disabled=state.access_role==='viewer'||(!!state.active&&!state.queue_mode)||!r||!!r.compatibility_warning||(r.suite==='ccrab'&&!r.can_run));$('#detail-status').textContent=active?`${active.message} · ${active.step} · ${active.completed_steps}/${active.total_steps} 步`:(r?.compatibility_warning||'所有结果保存在项目中。检视与评分沿用冻结批次配置。');
 switchTab(tab,false);if(tab==='log')loadLog();
}
function switchTab(value,load=true){tab=value;document.querySelectorAll('.detail-tabs button').forEach(n=>n.classList.toggle('active',n.dataset.tab===tab));for(const t of ['overview','log','report','files'])$('#tab-'+t).hidden=t!==tab;if(load){writeRoute(viewAtLocation(),{run:selected,tab,job:selectedJob||undefined});loadTab()}}
function loadTab(){if(tab==='log')loadLog();if(tab==='report')loadArtifact('report');if(tab==='files')loadArtifact('file')}
async function loadLog(){const job=$('#log-select').value;if(!job){$('#log-content').textContent='此批次还没有页面执行记录。历史 CLI 日志可在“原始文件”中查看。';$('#job-message').textContent='';return}selectedJob=job;try{const out=await api('/api/log?job='+encodeURIComponent(job));if(selectedJob!==job)return;const pre=$('#log-content');const bottom=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-50;pre.textContent=(out.truncated?'[仅显示最近 200 KB；完整日志已保存在 ui-state/jobs/]\n':'')+out.text;const j=state.jobs.find(j=>j.id===job);$('#job-message').textContent=(j?.message||'')+' · '+(j?.step||'');if(bottom)pre.scrollTop=pre.scrollHeight}catch(e){toast(e.message)}}
function markdown(text){const lines=text.split('\n'),out=[];let code=false,table=false,list=false;const inline=s=>esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');for(const line of lines){if(line.startsWith('```')){if(table){out.push('</tbody></table>');table=false}if(list){out.push('</ul>');list=false}code=!code;out.push(code?'<pre>':'</pre>');continue}if(code){out.push(esc(line)+'\n');continue}if(line.trim().startsWith('|')){if(/^\|[\s:|\-]+\|?$/.test(line))continue;const cells=line.trim().replace(/^\||\|$/g,'').split('|');if(!table){out.push('<table><tbody>');table=true}out.push('<tr>'+cells.map(c=>'<td>'+inline(c.trim())+'</td>').join('')+'</tr>');continue}if(table){out.push('</tbody></table>');table=false}const heading=line.match(/^(#{1,3})\s+(.+)/);if(heading){if(list){out.push('</ul>');list=false}out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);continue}if(/^[-*] /.test(line)){if(!list){out.push('<ul>');list=true}out.push('<li>'+inline(line.slice(2))+'</li>');continue}if(list){out.push('</ul>');list=false}if(line.trim())out.push('<p>'+inline(line)+'</p>')}if(table)out.push('</tbody></table>');if(list)out.push('</ul>');if(code)out.push('</pre>');return out.join('')}
async function loadArtifact(kind){const isReport=kind==='report', name=$(isReport?'#report-select':'#file-select').value;const target=$(isReport?'#report-content':'#file-content');if(!name){target.textContent=isReport?'还没有生成报告。完成评分后会自动生成。':'没有可查看的文件';if(isReport)target.classList.add('empty');return}const key=selected;target.textContent='正在读取…';try{const out=await api('/api/artifact?'+new URLSearchParams({key,path:name}));if(selected!==key||$(isReport?'#report-select':'#file-select').value!==name)return;if(isReport){reportText=out.text;target.classList.remove('empty');target.innerHTML=markdown(out.text)}else{fileText=out.text;target.textContent=(out.truncated?'[仅显示前 1 MB]\n':'')+out.text}}catch(e){target.textContent=e.message}}
function confirmRun({title,scope,profile,description}) {
 const dialog=$('#confirm-dialog');
 if(dialog.open)return Promise.resolve(false);
 $('#confirm-title').textContent=title;
 $('#confirm-scope').textContent=scope;
 $('#confirm-profile').textContent=profile;
 $('#confirm-description').textContent=description;
 dialog.returnValue='cancel';
 return new Promise(resolve=>{
  const listeners=new AbortController();
  dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close('cancel');},{signal:listeners.signal});
  dialog.addEventListener('click',event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))dialog.close('cancel');},{signal:listeners.signal});
  dialog.addEventListener('close',()=>{listeners.abort();resolve(dialog.returnValue==='confirm');},{once:true});
  dialog.showModal();
 });
}
let actionPending=false;
async function startAction(action){
 if(!selected||actionPending)return;
 const key=selected, ccrab=key.startsWith('ccrab/');
 const currentRun=state.runs.find(r=>r.key===key);
 const task=ccrab?undefined:$('#detail-case').value||undefined;
 if(action==='workflow'&&task){toast('一键流程请选全部案例；单例请使用“检视”按钮');return;}
 const data={action,key,profile:$('#detail-profile').value,task,retry:ccrab?false:$('#detail-retry').checked};
 actionPending=true;
 try{
  if(['review','eval','report','workflow','compare-martian'].includes(action)){
   const scope=ccrab?'当前单例':action==='eval'?'所有校验完整的已保存答卷':task||'全部案例 / 轮次';
   const description=action==='eval'?'只对校验完整的答卷调用裁判评分；未完成案例会排除并在报告中列明，不计零分。':action==='review'&&currentRun?.reviewer==='lcr'?`将使用所选模型配置，可能产生 API 费用。失败案例最多自动重试 ${currentRun.max_case_retries??1} 次；重试会重新运行该案例。已完成答卷会跳过。`:ccrab?'将使用所选模型配置，可能产生 API 费用。每批次仅执行一次，失败后请新建批次。':'将使用所选模型配置，可能产生 API 费用。已完成答卷会跳过。';
   const confirmed=await confirmRun({title:'开始'+(ccrab?'单例检视、修复与测试':actions[action])+'？',scope,profile:$('#detail-profile').selectedOptions[0]?.textContent||data.profile,description});
   if(!confirmed)return;
  }
  if(selected!==key||(state.active&&!state.queue_mode)){toast('批次或任务状态已变化，请重新选择操作');return;}
  const result=await api('/api/jobs',data);selectedJob=result.job_id;tab='log';writeRoute(viewAtLocation(),{run:selected,tab,job:selectedJob});toast(state.queue_mode?'任务已加入队列，日志会持续保存':'任务已启动，日志会持续保存');await refresh();loadLog();
 }catch(e){toast(e.message)}finally{actionPending=false;}
}
async function stopJob(id){if(!id)return;try{await api('/api/stop',{job:id});toast('正在停止本任务，已保存结果保留');await refresh()}catch(e){toast(e.message)}}
function download(name,text){const a=document.createElement('a');const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function exportDownload(){try{const response=await fetch('/api/export?'+new URLSearchParams({key:selected}),{headers:{'X-Dashboard-Token':token}});if(!response.ok){const out=await response.json();throw Error(out.error)}const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=selected.split('/').pop()+'-results.tar.gz';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){toast(e.message)}}
document.addEventListener('click',event=>{const el=event.target.closest('button');if(!el)return;if(el.dataset.view)showView(el.dataset.view);if(el.dataset.suite){filter=filter===el.dataset.suite?'':el.dataset.suite;render()}if(el.dataset.open)openRun(el.dataset.open);if(el.dataset.job){const j=state.jobs.find(j=>j.id===el.dataset.job);if(j)openRun(j.key,j.id)}if(el.dataset.stop)stopJob(el.dataset.stop);if(el.dataset.action)startAction(el.dataset.action);if(el.dataset.tab)switchTab(el.dataset.tab);if(el.classList.contains('close-create'))$('#create-dialog').close()});
$('#primary-action').addEventListener('click',()=>{const button=$('#primary-action');if(button.dataset.nextAction)startAction(button.dataset.nextAction);else if(button.dataset.nextView)switchTab(button.dataset.nextView);});
async function acknowledgeRecovery(){
 const button=$('#ack-recovery');
 button.disabled=true;
 try{
  const confirmed=await confirmRun({title:'确认恢复队列？',scope:'恢复等待执行的任务',profile:'沿用各任务已保存的模型配置',description:'请确认旧任务进程和旧容器已经停止。已中断任务不会自动重跑；队列中尚未执行的任务会继续执行，可能调用模型。'});
  if(!confirmed)return;
  await api('/api/recovery',{confirmed:true});
  toast('队列已恢复；已中断任务未重跑');await refresh();
 }catch(e){toast(e.message)}finally{button.disabled=false}
}
$('#ack-recovery').addEventListener('click',acknowledgeRecovery);
$('#new-run').addEventListener('click',newRun);$('#refresh').addEventListener('click',refresh);$('#search').addEventListener('input',renderRuns);$('#create-suite').addEventListener('change',()=>createFields(true));$('#create-reviewer').addEventListener('change',()=>{$('#retry-field').hidden=$('#create-suite').value==='ccrab'||$('#create-suite').value==='mcr'||$('#create-reviewer').value!=='lcr'});$('#create-profile').addEventListener('change',profileHint);$('#create-form').addEventListener('submit',createRun);$('#close-detail').addEventListener('click',()=>{$('#detail-dialog').close()});$('#detail-dialog').addEventListener('close',()=>{if(consumeRouteClose($('#detail-dialog')))return;selected=null;writeRoute(viewAtLocation())});$('#detail-stop').addEventListener('click',()=>stopJob(state.active));$('#log-select').addEventListener('change',()=>{selectedJob=$('#log-select').value;writeRoute(viewAtLocation(),{run:selected,tab,job:selectedJob});loadLog()});$('#report-select').addEventListener('change',()=>loadArtifact('report'));$('#file-select').addEventListener('change',()=>loadArtifact('file'));$('#download-log').addEventListener('click',()=>download('execution.log',$('#log-content').textContent));$('#save-report').addEventListener('click',()=>download($('#report-select').value.split('/').pop()||'report.md',reportText));$('#save-file').addEventListener('click',()=>download($('#file-select').value.split('/').pop()||'result.txt',fileText));$('#download-export').addEventListener('click',exportDownload);


// Keep native selects as the form data source; render a consistent, keyboard-accessible picker.
function enhanceSelect(select) {
 const wrapper=document.createElement('div');wrapper.className='select-control';
 select.before(wrapper);wrapper.append(select);select.hidden=true;select.tabIndex=-1;
 const trigger=document.createElement('button');trigger.type='button';trigger.className='select-trigger';
 trigger.setAttribute('role','combobox');trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
 const caption=document.createElement('span');caption.className='select-caption';trigger.append(caption);
 const menu=document.createElement('div');menu.className='select-menu';menu.id=select.id+'-options';menu.setAttribute('role','listbox');menu.hidden=true;
 trigger.setAttribute('aria-controls',menu.id);
 const label=select.closest('label');
 const labelText=select.getAttribute('aria-label') || (label ? [...label.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim() : '选择选项');
 trigger.setAttribute('aria-label',labelText);menu.setAttribute('aria-label',labelText);
 wrapper.append(trigger,menu);let active=-1,search='',searchTimer;
 function choices(){return [...select.options].map((o,i)=>({o,i})).filter(({o})=>!o.disabled&&!o.hidden)}
 function highlight(index){active=index;[...menu.children].forEach(n=>n.classList.toggle('highlighted',Number(n.dataset.index)===active));const item=menu.querySelector('.highlighted');if(item){trigger.setAttribute('aria-activedescendant',item.id);item.scrollIntoView({block:'nearest'});}else trigger.removeAttribute('aria-activedescendant');}
 function close(){menu.hidden=true;trigger.setAttribute('aria-expanded','false');trigger.removeAttribute('aria-activedescendant');wrapper.classList.remove('is-open');}
 function sync(){
  caption.textContent=select.selectedOptions[0]?.textContent || '暂无可选项';trigger.title=caption.textContent;
  trigger.disabled=select.disabled||!choices().length;
  menu.replaceChildren();[...select.options].forEach((o,i)=>{if(o.hidden)return;const item=document.createElement('div');item.id=menu.id+'-'+i;item.className='select-option';item.dataset.index=i;item.setAttribute('role','option');item.setAttribute('aria-label',o.textContent);item.setAttribute('aria-selected',String(o.selected));item.setAttribute('aria-disabled',String(o.disabled));item.textContent=o.textContent;menu.append(item);});
  if(!menu.hidden){if(trigger.disabled)close();else highlight(active>=0&&select.options[active]&&!select.options[active].disabled?active:select.selectedIndex);}
 }
 function open(){if(trigger.disabled)return;document.dispatchEvent(new CustomEvent('picker-open',{detail:trigger}));const rect=trigger.getBoundingClientRect();const dialog=trigger.closest('dialog');const boundary=dialog?.getBoundingClientRect().bottom||innerHeight;wrapper.classList.toggle('opens-up',boundary-rect.bottom<235&&rect.top>235);menu.hidden=false;wrapper.classList.add('is-open');trigger.setAttribute('aria-expanded','true');highlight(select.selectedIndex>=0?select.selectedIndex:choices()[0]?.i);}
 function choose(index){const option=select.options[index];if(!option||option.disabled)return;select.selectedIndex=index;close();sync();select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));trigger.focus();}
 trigger.addEventListener('click',()=>menu.hidden?open():close());
 menu.addEventListener('mousedown',e=>e.preventDefault());
 menu.addEventListener('click',e=>{const item=e.target.closest('[role=option]');if(item)choose(Number(item.dataset.index));});
 trigger.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&!menu.hidden){e.preventDefault();e.stopPropagation();close();return;}
  if(e.key==='Tab'){close();return;}
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const wasClosed=menu.hidden;if(wasClosed)open();const rows=choices();const pos=rows.findIndex(r=>r.i===active);const next=e.key==='Home'?0:e.key==='End'?rows.length-1:wasClosed?Math.max(0,pos):Math.max(0,Math.min(rows.length-1,pos+(e.key==='ArrowDown'?1:-1)));if(rows[next])highlight(rows[next].i);return;}
  if(e.key==='Enter'||e.key===' '){e.preventDefault();menu.hidden?open():choose(active);return;}
  if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();search+=e.key.toLowerCase();clearTimeout(searchTimer);searchTimer=setTimeout(()=>search='',700);if(menu.hidden)open();const match=choices().find(({o})=>o.textContent.toLowerCase().startsWith(search));if(match)highlight(match.i);}
 });
 trigger.addEventListener('blur',close);
 document.addEventListener('pointerdown',e=>{if(!wrapper.contains(e.target))close();});
 document.addEventListener('picker-open',e=>{if(e.detail!==trigger)close();});
 trigger.closest('dialog')?.addEventListener('close',close);
 select.addEventListener('change',sync);
 new MutationObserver(sync).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','label']});
 sync();
}
 document.querySelectorAll('select').forEach(enhanceSelect);

const modelTests=new Map();
function connectionResult(test){
 if(!test)return '';
 if(test.pending)return '<div class="connection-progress"><span class="spinner"></span>正在检查模型接口…</div>';
 if(test.error)return `<div class="connection-error" role="alert">${esc(test.error)}</div>`;
 return `<div class="connection-results">${test.results.map(r=>`<div class="connection-row ${r.status==='passed'?'passed':r.status==='reachable'?'warning':'failed'}"><span class="connection-icon" aria-hidden="true">${r.status==='passed'?'✓':r.status==='reachable'?'!':'×'}</span><div><strong>${esc(r.role)}</strong><span>${r.status==='passed'?'连通正常':esc(r.message)}</span></div><small>${esc(r.elapsed_seconds)}s${r.http_status?` · HTTP ${esc(r.http_status)}`:''}</small></div>`).join('')}</div><div class="connection-caption">${test.at?`${esc(test.at)} 测试 · `:''}仅验证基础请求，不代表完整测评通过</div>`;
}
function renderModels(){
 $('#new-model').disabled=state.access_role==='viewer';
 const profiles=state.profiles.filter(p=>!p.internal&&availableModel(p));
 $('#model-count').textContent=`${profiles.length} 个配置`;
 $('#model-list').innerHTML=profiles.map(p=>{
 const test=modelTests.get(p.id), passed=test?.results?.every(r=>r.status==='passed');
 const title=p.review_model||'未设置模型';
 const provider=p.provider==='qwen'?['Q','Qwen','qwen']:p.provider==='deepseek'?['D','DeepSeek','deepseek']:/qwen/i.test(title)?['Q','Qwen','qwen']:/deepseek/i.test(title)?['D','DeepSeek','deepseek']:/gpt/i.test(title)?['O','OpenAI','openai']:['M','兼容接口','generic'];
 const tone=test?.pending?'checking':passed?'passed':test?'warning':p.review_ready?'untested':'incomplete';
 const status=test?.pending?'测试中':passed?'连通正常':test?'需检查':p.review_ready?'未测试':'待完善';
 return `<article class="model-card"><div class="model-card-head"><div class="provider-mark ${provider[2]}" aria-hidden="true">${provider[0]}</div><div class="model-identity"><small>${provider[1]}</small><h3>${esc(p.label)}</h3></div><span class="model-status ${tone}">${status}</span></div><dl class="model-facts"><div><dt>可用角色</dt><dd>被测模型 / 裁判模型</dd></div><div><dt>模型 ID</dt><dd title="${esc(title)}">${esc(title)}</dd></div><div><dt>响应模式</dt><dd>${p.stream_enabled?'流式':'非流式'}</dd></div></dl>${test?`<div class="model-connection" role="status" aria-live="polite">${connectionResult(test)}</div>`:''}<div class="model-card-footer"><span>${test?.pending?'正在发送小请求':!p.review_ready?'请补齐配置后测试':'密钥已配置 · 不回显'}</span><div class="model-actions"><button class="secondary" data-test-profile="${esc(p.id)}" ${state.access_role==='viewer'||modelTestBusy?'disabled':''}>${test?.pending?'测试中…':test?'重新测试':'测试连通性'}</button>${p.id==='environment'?'':`<button class="secondary" data-edit-profile="${esc(p.id)}" ${state.access_role==='viewer'?'disabled':''}>编辑</button><button class="secondary danger" data-delete-profile="${esc(p.id)}" ${state.access_role==='viewer'?'disabled':''}>删除</button>`}</div></div></article>`;
 }).join('');
}
let editingProfileId='';
function openModel(profile=null){
 if(!state||state.access_role==='viewer')return;
 editingProfileId=profile?.id||'';
 const form=$('#model-form');form.reset();$('#model-error').textContent='';
 form.elements.profile_id.value=editingProfileId;
 $('#model-title').textContent=profile?'编辑模型配置':'新增模型配置';
 $('#save-model').textContent=profile?'保存修改':'保存配置';
 const key=form.elements.api_key;key.required=!profile;key.value='';
 key.placeholder=profile?'留空则保留现有密钥':'填写模型 API Key';
 form.elements.provider.value=profile?.provider==='qwen'||/qwen/i.test(profile?.review_model||'')?'qwen':'deepseek';
 if(profile){form.elements.name.value=profile.label||'';form.elements.model.value=profile.review_model==='未设置'?'':profile.review_model;form.elements.base_url.value=profile.base_url||'';form.elements.stream.checked=profile.stream_enabled===true;}
 $('#model-dialog').showModal();
}
$('#new-model').addEventListener('click',openModel);
document.querySelectorAll('.close-model').forEach(b=>b.addEventListener('click',()=>$('#model-dialog').close()));
$('#model-dialog').addEventListener('close',()=>{$('#model-form').reset();$('#model-error').textContent='';editingProfileId='';$('#model-title').textContent='新增模型配置';$('#save-model').textContent='保存配置';$('#model-form').elements.api_key.required=true});
$('#model-form').addEventListener('submit',async event=>{
 event.preventDefault();$('#save-model').disabled=true;$('#model-error').textContent='';
 try{const form=event.target;const data=Object.fromEntries(new FormData(form));delete data.profile_id;if(editingProfileId){data.stream=form.elements.stream.checked?'true':'false';await api('/api/profiles/'+encodeURIComponent(editingProfileId),data,'PUT')}else await api('/api/profiles',data);$('#model-dialog').close();await refresh();toast('配置已保存，未调用模型。已有批次仍使用创建时固定的配置。')}
 catch(e){$('#model-error').textContent=e.message}finally{$('#save-model').disabled=false}
});

let modelTestBusy=false, modelTestVersion=0;
function clearModelTest(){modelTestVersion++;$('#model-test-result').replaceChildren()}
$('#model-form').addEventListener('input',clearModelTest);
$('#model-form').addEventListener('change',clearModelTest);
$('#model-dialog').addEventListener('close',clearModelTest);
async function testModel(profileId){
 if(modelTestBusy)return;
 const form=$('#model-form');
 if(!profileId&&!form.reportValidity())return;
 const target=$('#model-test-result');
 const version=modelTestVersion;
 const data=profileId?{profile_id:profileId}:Object.fromEntries(new FormData(form));
 if(profileId==='environment')data.profile_id=profileId;
 if(!profileId&&editingProfileId){data.profile_id=editingProfileId;data.stream=form.elements.stream.checked?'true':'false'}
 modelTestBusy=true;$('#test-model').disabled=true;$('#save-model').disabled=true;
 if(profileId){modelTests.set(profileId,{pending:true});renderModels()}else target.innerHTML=connectionResult({pending:true});
 $('#test-model').textContent='测试中…';
 try{const out=await api('/api/profiles/test',data);
  if(!profileId&&version!==modelTestVersion)return;
  const result={...out,at:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})};
  if(profileId){modelTests.set(profileId,result)}else target.innerHTML=connectionResult(result);
 }catch(e){if(profileId)modelTests.set(profileId,{error:e.message});else if(version===modelTestVersion)target.innerHTML=connectionResult({error:e.message})}
 finally{modelTestBusy=false;$('#test-model').disabled=false;$('#test-model').textContent='测试连通性';$('#save-model').disabled=false;renderModels()}

}
$('#test-model').addEventListener('click',()=>testModel());
$('#model-list').addEventListener('click',event=>{const test=event.target.closest('[data-test-profile]');if(test){testModel(test.dataset.testProfile);return}const edit=event.target.closest('[data-edit-profile]');if(edit){const profile=state.profiles.find(p=>p.id===edit.dataset.editProfile);if(profile)openModel(profile);return}const remove=event.target.closest('[data-delete-profile]');if(remove){const profile=state.profiles.find(p=>p.id===remove.dataset.deleteProfile);if(profile)confirmDeleteProfile(profile)}});
let deletingProfile=null;
function confirmDeleteProfile(profile){if(state.access_role==='viewer')return;deletingProfile=profile;$('#model-delete-description').textContent=`确定删除“${profile.label}”吗？删除后将从当前工作台和 COS 配置存储中移除。`;$('#model-delete-error').textContent='';$('#confirm-delete-profile').disabled=false;$('#model-delete-dialog').showModal()}
$('#confirm-delete-profile').addEventListener('click',async event=>{event.preventDefault();if(!deletingProfile)return;const button=event.currentTarget;button.disabled=true;$('#model-delete-error').textContent='';try{await api('/api/profiles/'+encodeURIComponent(deletingProfile.id),null,'DELETE');modelTests.delete(deletingProfile.id);$('#model-delete-dialog').close();deletingProfile=null;await refresh();toast('模型配置已删除。已有批次仍保留创建时的配置。')}catch(error){$('#model-delete-error').textContent=error.message}finally{button.disabled=false}});



let comparisonSelected=null, comparisonText='', comparisonMode='execution', evidenceSelected=null;
function experimentBadge(c){const x=UX.experiment(c);return `<span class="badge ${esc(x.tone)}">${esc(x.label)}</span>`}
function renderComparisons(){
 $('#new-comparison').disabled=state.access_role==='viewer';
 const experiments=state.comparisons||[];
 const card=(c,result)=>{const x=UX.experiment(c);return `<article class="panel job-card"><div><h3>${esc(c.display_name||c.name)}</h3><p>${esc(suiteName(c.suite))} · ${c.report.groups.length} 个模型 × ${c.report.case_count} 例 · 裁判 ${esc(c.judge_model)}</p><small>${result?'质量对比覆盖 '+c.report.common_cases.length+'/'+c.report.case_count+' 个共同案例':'评分完成 '+x.done+'/'+x.total+' 次；超时 '+x.timeouts+' 次'}${x.live?' · 进行中':''}</small></div>${experimentBadge(c)}<button class="secondary" data-open-comparison="${esc(c.key)}" data-mode="${result?'results':'execution'}">${result?'查看对比':'查看进度'}</button></article>`};
 $('#experiment-list').innerHTML=experiments.map(c=>card(c,false)).join('')||'<div class="panel guide">还没有实验。点击“创建实验”，设置案例、被测模型和统一裁判。</div>';
 $('#comparison-list').innerHTML=experiments.map(c=>card(c,true)).join('')||'<div class="panel guide">暂无可对比的实验结果。先在“多模型实验”创建并运行实验，再到这里查看结果。</div>';
 if(comparisonSelected)loadComparison();
}
function comparisonFields(){const suite=state.catalog.find(s=>s.id===$('#comparison-suite').value);options($('#comparison-dataset'),suite.datasets.map(d=>({value:d.id,label:datasetLabel(d,suite.id)})));updatePlans()}
$('#new-comparison').addEventListener('click',()=>{
 if(!state)return;$('#comparison-form').reset();
 $('#comparison-name').value='models-'+new Date().toISOString().replace(/[^0-9]/g,'').slice(0,14);
 $('#comparison-error').textContent='';options($('#comparison-suite'),state.catalog.filter(s=>['aacr','swe_prbench_llm','codefuse_cr'].includes(s.id)).map(s=>({value:s.id,label:s.name})),'aacr');comparisonFields();
 const profiles=state.profiles.filter(p=>!p.internal&&availableModel(p));
 $('#comparison-models').innerHTML=profiles.map(p=>`<label class="checkbox-label"><input type="checkbox" name="models" value="${esc(p.id)}" ${p.review_ready?'':'disabled'}>${esc(p.label)} · ${esc(p.review_model)}${p.review_ready?'':'（配置未齐全）'}</label>`).join('');
 options($('#comparison-judge'),profiles.filter(p=>p.review_ready).map(p=>({value:p.id,label:p.label+' · '+p.review_model})),'model.local.json');
 updatePlans();$('#comparison-create').showModal();
});
function updatePlans(){
 if(!state)return false;
 if($('#create-reviewer').value==='cline_skill')$('#create-note').textContent='Cline 3.0.68 + benchmark-review Skill。当前仅支持 Mac、AACR 单例清单；每例独立只读代码快照。超时为整例预算，日志和 Skill 加载证据会保留。';
 if($('#create-reviewer').value==='server_agent')$('#create-note').textContent='需先配置 agent.local.json。按创建任务 → 查询状态 → 获取结果执行；超时为单案例等待上限。停止本地等待不会取消远端任务；恢复只查询原任务。模拟服务不评分。';
 if(!state)return false;
 const concurrencySupported=!['mcr','ccrab'].includes($('#create-suite').value)&&$('#create-reviewer').value==='lcr';
 $('#concurrency-field').hidden=!concurrencySupported;$('#create-concurrency').disabled=!concurrencySupported;
 const selectedModel=state.profiles.find(p=>p.id===$('#create-profile').value),judge=state.profiles.find(p=>p.id===$('#create-judge').value.replace('legacy-judge:',''));
 const legacyJudge=$('#create-judge').value.startsWith('legacy-judge:');
 const judgeName=legacyJudge?judge?.judge_model:judge?.review_model;
 const suite=$('#create-suite').value,ready=state.access_role!=='viewer'&&!!selectedModel?.review_ready&&(suite==='ccrab'||(legacyJudge?!!judge?.ready:!!judge?.review_ready));
 $('#create-readiness').hidden=ready;$('#create-readiness').textContent='请先在左侧“模型配置”准备被测模型与裁判接口，然后重新打开此窗口。';
 $('#create-form').querySelectorAll('[type=submit]').forEach(b=>b.disabled=!ready);
 const singleData=state.catalog.find(s=>s.id===suite)?.datasets.find(d=>d.id===$('#create-dataset').value);
 $('#create-plan').textContent=singleData?`${singleData.count} ${suite==='mcr'?'个 PR，'+singleData.rounds+' 轮':'例'} · 被测模型：${selectedModel?.review_model||'未选择'} · ${suite==='ccrab'?'DeepSeek 修复 + 容器测试':('裁判：'+(judgeName||'未选择'))}。${['mcr','ccrab'].includes(suite)?'专项固定执行预算，见下方说明。':(['server_agent','cline_skill'].includes($('#create-reviewer').value)?'每案例执行或等待上限 '+$('#create-timeout').value+' 分钟，超时后可恢复查询原任务。':'案例并发 '+$('#create-concurrency').value+'，单案例文件顺序检视。每文件任务时限 '+$('#create-timeout').value+' 分钟，整例上限随变更文件数增加；不等同于多模型实验的整例总时限。')}`:'';
 const models=[...$('#comparison-models').querySelectorAll('input:checked')],hasJudge=!!$('#comparison-judge').value;
 $('#comparison-readiness').hidden=models.length>=2&&models.length<=6&&hasJudge;
 $('#comparison-readiness').textContent=state.profiles.filter(p=>!p.internal&&p.review_ready).length<2?'请先到“模型配置”准备至少两个可用模型接口。':'请选择 2–6 个被测模型和一个统一裁判。';
 $('#submit-comparison').disabled=models.length<2||models.length>6||!hasJudge;
 const dataset=state.catalog.find(s=>s.id===$('#comparison-suite').value)?.datasets.find(d=>d.id===$('#comparison-dataset').value);
 $('#comparison-plan').textContent=dataset?`${models.length} 个模型 × ${dataset.count} 例 = ${models.length*dataset.count} 次检视，逐组串行执行。每案例检视总时限 ${$('#comparison-form [name=timeout_minutes]').value} 分钟；裁判每案例最多 10 分钟。失败单独记录，不自动重试。` : '';
 return ready;
}
for(const id of ['create-form','comparison-form'])$('#'+id).addEventListener('input',updatePlans);
for(const id of ['create-form','comparison-form'])$('#'+id).addEventListener('change',updatePlans);
$('#comparison-suite').addEventListener('change',comparisonFields);
$('#close-comparison-create').addEventListener('click',()=>$('#comparison-create').close());
$('#comparison-form').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.target),data=Object.fromEntries(f);data.models=f.getAll('models');data.timeout_minutes=Number(data.timeout_minutes);$('#submit-comparison').disabled=true;try{const result=await api('/api/comparisons',data);$('#comparison-create').close();await refresh();showView('experiments');openComparison(result.key)}catch(error){$('#comparison-error').textContent=error.message}finally{$('#submit-comparison').disabled=false}});
function openComparison(key,mode='execution'){
 closeForRoute($('#detail-dialog'));closeForRoute($('#evidence-dialog'));comparisonSelected=key;comparisonMode=mode;comparisonText='';
 const result=mode==='results';$('#comparison-report-section').hidden=!result;$('#comparison-report-section').open=false;$('#comparison-download').hidden=!result;$('#comparison-json').hidden=!result;$('#comparison-show-results').hidden=result;$('#comparison-log-section').hidden=result;$('#comparison-log-section').open=false;
 $('#comparison-report').textContent='读取报告…';$('#comparison-log').textContent='';$('#comparison-summary').innerHTML='';$('#comparison-groups').innerHTML='';$('#comparison-cases').innerHTML='';
 const view=result?'comparisons':'experiments';showView(view,{fromHistory:true});writeRoute(view,{experiment:key});
 if(!$('#comparison-detail').open)$('#comparison-detail').showModal();loadComparison();
}
function failureSummary(c,experiment){
 if(c.error==='TimeoutError')return c.status==='score_failed'?`裁判超过 ${Math.round((experiment.budget?.judge_seconds||600)/60)} 分钟上限；检视答卷保留，未计分。`:`整例超过 ${Math.round((experiment.budget?.review_seconds||600)/60)} 分钟上限；不是零分答卷。`;
 return ({review_failed:'检视未完整结束，保留日志，未计分。',score_failed:'裁判未完整结束，保留答卷，未计分。',setup_failed:'准备检查未通过，尚不能检视。'}[c.status]||'');
}
function renderComparisonBody(c){
 const x=UX.experiment(c),r=c.report,result=comparisonMode==='results';
 $('#comparison-title').textContent=(c.display_name||c.name)+(result?' · 结果对比':' · 执行进度');
 $('#comparison-progress').innerHTML=`<strong>${esc(x.label)}</strong><p>评分完成 ${x.done}/${x.total} 次 · 超时 ${x.timeouts} 次 · 其他失败 ${Math.max(0,x.failed-x.timeouts)} 次</p><small>编号 ${esc(c.name)} · ${esc(suiteName(c.suite))} · 裁判 ${esc(c.judge_model)}</small>`;
 $('#comparison-summary').innerHTML=result?`<div class="result-note"><strong>质量对比仅覆盖 ${r.common_cases.length}/${r.case_count} 个共同案例</strong><p>${r.common_cases.length?'以下 P/R/F1 使用相同案例计算；请同时看完成率，不据小样本或部分成功直接判定模型优劣。':'尚无所有模型共同完成评分的案例，暂不能比较质量分数。'} Token 是已记录用量，包含缓存 Token；覆盖范围不同或缺少失败用量时，不能据总量判断费用。</p></div>`:'';
 $('#comparison-groups').innerHTML='<div class="comparison-grid">'+r.groups.map(g=>{
 const working=g.cases.find(v=>['reviewing','scoring'].includes(v.status)),q=g.common_quality;
 return `<section class="comparison-model"><h3>${esc(g.label)}</h3><small>${esc(g.model)}</small><p>检视 ${g.completed}/${r.case_count} · 评分 ${g.scored}/${r.case_count} · 超时 ${g.timeouts}</p><progress value="${g.scored}" max="${r.case_count}"></progress>${result?`<div class="comparison-facts"><div><strong>${Math.round(g.completed/r.case_count*100)}%</strong><small>检视完成率</small></div><div><strong>${pct(q?.f1)}</strong><small>共同案例 F1</small></div></div><p>精确率 P ${pct(q?.precision)} / 召回率 R ${pct(q?.recall)}</p><p>检视平均 ${g.mean_seconds==null?'—':Math.round(g.mean_seconds)+' 秒'}（含失败尝试）<br>已知 Token ${(g.review_tokens.total_tokens||0).toLocaleString()} · 覆盖 ${g.token_coverage}/${r.case_count} 例</p>`:`<p>${x.live&&working?esc(UX.caseStatus(working,true)+' · '+working.id):g.scored===r.case_count?'本组已完成':!x.live?'本组已结束，未完成项见下表':'等待后续案例执行'}</p>`}</section>`;
 }).join('')+'</div>';
 $('#comparison-cases').innerHTML=`<h3>逐案例${result?'结果与评分依据':'进度与原因'}</h3><div class="table-wrap"><table class="comparison-case-table"><thead><tr><th>案例 / 模型</th><th>状态</th><th>检视耗时</th><th>${result?'本例 F1':'阶段说明'}</th><th></th></tr></thead><tbody>${r.groups.flatMap(g=>g.cases.map(v=>`<tr><td>${esc(v.id)}<small>${esc(g.label)}</small></td><td>${esc(UX.caseStatus(v,x.live))}</td><td>${v.elapsed_seconds==null?'—':Math.round(v.elapsed_seconds)+' 秒'}</td><td>${result?pct(v.metrics?.f1):esc(failureSummary(v,c)||(['reviewing','scoring'].includes(v.status)&&!x.live?'以任务结束状态为准，日志保留。':'—'))}</td><td><button class="text-button" data-evidence-group="${esc(g.key)}" data-evidence-case="${esc(v.id)}">${v.metrics?'查看评分依据':'查看状态与证据'}</button></td></tr>`)).join('')}</tbody></table></div>`;
 $('#comparison-group-links').innerHTML=r.groups.filter(g=>state.runs.some(r=>r.key===g.key)).map(g=>`<button class="secondary" data-open="${esc(g.key)}">${esc(g.label)} · 原始记录</button>`).join('');
 $('#comparison-stop').hidden=result||!x.live||state.access_role==='viewer';$('#comparison-stop').disabled=c.status==='cancelling';
}
async function loadComparison(){
 const key=comparisonSelected,c=state.comparisons?.find(c=>c.key===key);if(!c)return;renderComparisonBody(c);
 try{const out=await api('/api/artifact?'+new URLSearchParams({key,path:'report.zh-CN.md'}));if(comparisonSelected!==key)return;comparisonText=out.text;$('#comparison-report').innerHTML=markdown(out.text);
 if(c.job_id&&comparisonMode==='execution'){const log=await api('/api/log?job='+encodeURIComponent(c.job_id));if(comparisonSelected===key)$('#comparison-log').textContent=log.text}}
 catch(e){if(comparisonSelected===key){$('#comparison-report').textContent=e.message;$('#comparison-log').textContent=e.message}}
}
async function openEvidence(key,group,caseId){
 const marker=JSON.stringify([key,group,caseId]);evidenceSelected=marker;
 $('#evidence-title').textContent=caseId+' · 案例依据';$('#evidence-content').textContent='正在读取已有证据，不调用模型…';
 if(!$('#evidence-dialog').open)$('#evidence-dialog').showModal();
 writeRoute(comparisonMode==='results'?'comparisons':'experiments',{experiment:key,group,case:caseId});
 try{
  const d=await api('/api/comparison-case?'+new URLSearchParams({key,group,case:caseId}));if(evidenceSelected!==marker)return;
  const c=state.comparisons.find(c=>c.key===key),live=UX.experiment(c).live,score=d.score,pairs=score.metrics?.pairs||[],usedG=new Set(pairs.map(p=>p[0])),usedC=new Set(pairs.map(p=>p[1]));
  const block=(title,text)=>`<h4>${esc(title)}</h4><div class="evidence-text">${esc(text)}</div>`;
  let html=`<div class="run-summary"><strong>${esc(UX.caseStatus(d.status,live))}</strong><p>${esc(failureSummary(d.status,c))}</p><small>未匹配不等于误报。以下读取原始意见、裁判判断和最终计数，不重新评分。</small></div>`;
  if(d.diagnostic){const dx=d.diagnostic;html+=`<details open><summary>诊断摘要</summary><p>已结束模型调用 ${dx.ended_calls} 次；实际重试 ${dx.retries} 次。已记录 HTTP 状态：${esc(Object.entries(dx.http_statuses).map(([k,v])=>k+' × '+v).join('，')||'暂无')}。状态 0 表示没有正常 HTTP 响应。</p>${dx.deadline_calls.map(v=>`<p>上下文时限到期：${esc(v.task)} · ${esc(v.file)} · ${Math.round(v.seconds)} 秒</p>`).join('')}${dx.pending_calls.length?'<p>缺少结束记录的调用：'+esc(dx.pending_calls.map(v=>v.task+' / '+v.file).join('；'))+'。这不证明进程仍在运行。</p>':''}</details>`;}
  if(score.status==='complete'){
   html+=`<h3>最终匹配 ${pairs.length} 对</h3>`;
   for(const [gi,ci] of pairs){const decision=score.decisions.find(v=>v.golden===gi&&v.candidate===ci);html+=`<section class="evidence-pair">${block('参考 G'+(gi+1),d.references[gi]?.comment)}${block('模型意见 C'+(ci+1),d.comments[ci]?.content)}${block('裁判理由（原文）',decision?.reasoning||'未记录')}</section>`;}
   html+='<h3>未匹配模型意见</h3>'+d.comments.map((v,i)=>usedC.has(i)?'':`<section class="evidence-pair">${block('C'+(i+1)+' · '+v.path+':'+v.start_line,v.content)}</section>`).join('');
   if(usedC.size===d.comments.length)html+='<p>无未匹配意见。</p>';
   html+='<h3>未匹配参考意见</h3>'+d.references.map((v,i)=>usedG.has(i)?'':`<section class="evidence-pair">${block('G'+(i+1),v.comment)}</section>`).join('');
   if(usedG.size===d.references.length)html+='<p>所有参考均获匹配。</p>';
   html+='<details><summary>全部意见对裁判判断（含未采用的匹配边）</summary>'+score.decisions.map(v=>`<section class="evidence-pair"><strong>G${v.golden+1} ↔ C${v.candidate+1}：${v.match?'语义匹配':'不匹配'}</strong><small> · ${pairs.some(p=>p[0]===v.golden&&p[1]===v.candidate)?'计入最终匹配':'未计入最终匹配'}</small><p class="evidence-text">${esc(v.reasoning)}</p></section>`).join('')+'</details>';
  }else html+='<p>尚无完整评分，不展示质量分数。已有文件可从下方查看；不把缺失答卷视为零意见。</p>';
  html+='<details><summary>原始证据文件</summary><div class="actions-row">'+d.artifacts.map(path=>`<button class="text-button" data-evidence-file="${esc(path)}" data-evidence-file-group="${esc(group)}">${esc(path)}</button>`).join('')+'</div><pre class="file-view" id="evidence-file-text"></pre></details>';
  $('#evidence-content').innerHTML=html;
 }catch(e){if(evidenceSelected===marker)$('#evidence-content').textContent=e.message;}
}
$('#comparison-cases').addEventListener('click',e=>{const b=e.target.closest('[data-evidence-group]');if(b)openEvidence(comparisonSelected,b.dataset.evidenceGroup,b.dataset.evidenceCase)});
$('#evidence-content').addEventListener('click',async e=>{const b=e.target.closest('[data-evidence-file]');if(!b)return;const marker=evidenceSelected,target=$('#evidence-file-text');target.textContent='读取中…';try{const d=await api('/api/artifact?'+new URLSearchParams({key:b.dataset.evidenceFileGroup,path:b.dataset.evidenceFile}));if(marker===evidenceSelected)target.textContent=(d.truncated?'仅展示部分内容\n':'')+d.text}catch(err){if(marker===evidenceSelected)target.textContent=err.message}});
$('#close-evidence').addEventListener('click',()=>$('#evidence-dialog').close());
$('#evidence-dialog').addEventListener('close',()=>{if(consumeRouteClose($('#evidence-dialog')))return;evidenceSelected=null;if(comparisonSelected)writeRoute(comparisonMode==='results'?'comparisons':'experiments',{experiment:comparisonSelected})});
for(const id of ['comparison-list','experiment-list'])$('#'+id).addEventListener('click',e=>{const b=e.target.closest('[data-open-comparison]');if(b)openComparison(b.dataset.openComparison,b.dataset.mode)});
$('#comparison-show-results').addEventListener('click',()=>openComparison(comparisonSelected,'results'));
$('#close-comparison-detail').addEventListener('click',()=>$('#comparison-detail').close());
$('#comparison-detail').addEventListener('close',()=>{if(consumeRouteClose($('#comparison-detail')))return;comparisonSelected=null;writeRoute(comparisonMode==='results'?'comparisons':'experiments')});
$('#comparison-stop').addEventListener('click',()=>stopJob(state.comparisons.find(c=>c.key===comparisonSelected)?.job_id));
$('#comparison-download').addEventListener('click',()=>download('模型对比报告.md',comparisonText));
$('#comparison-json').addEventListener('click',()=>download('模型对比明细.json',JSON.stringify(state.comparisons.find(c=>c.key===comparisonSelected)?.report,null,2)));
restoreRoute();refresh();setInterval(refresh,4000);
pollCaseImport();setInterval(pollCaseImport,2000);
