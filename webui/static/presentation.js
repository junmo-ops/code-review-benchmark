/* Pure presentation decisions, independent of DOM and benchmark execution. */
(function(root){
 const active=j=>j&&['queued','running','cancelling'].includes(j.status);
 function primary(run,job){
  if(active(job))return {view:'log',label:job.status==='queued'?'查看排队状态':'查看运行进度'};
  if(!run)return {view:'log',label:'查看创建进度'};
  if(['scored','partial_scored','complete','complete_no_findings'].includes(run.status))return {view:'report',label:'查看结果'};
  if(job&&['failed','cancelled','interrupted'].includes(job.status))return {view:'log',label:'查看原因与日志'};
  if(['failed','partial','unreadable'].includes(run.status))return {view:'log',label:'查看原因与日志'};
  if(run.compatibility_warning)return {view:'report',label:'查看历史结果'};
  if(run.status==='reviewed')return {action:'eval',label:'开始评分'};
  return {action:'workflow',label:run.suite==='ccrab'?'开始单例验证':'开始测评'};
 }
 function result(run){
  if(run.suite==='ccrab'){
   if(run.status==='complete_no_findings')return '检视返回 0 条意见，未触发修复；修复后测试未执行。';
   if(run.status==='complete')return run.metrics.test_pass_rate===1?'修复后发布测试通过。':run.metrics.test_pass_rate===0?'修复后发布测试未通过。':'尚无已确认的修复后测试结果。';
   if(run.status==='failed'||run.status==='interrupted')return '执行未完成，本次不计测试得分。请查看失败阶段和日志。';
   return '先验证测试对照，再检视、修复与测试；无意见时跳过修复。';
  }
  if(run.status==='scored')return `已完成 ${run.completed}/${run.total} 个${run.suite==='mcr'?'轮次':'案例'}的检视，并获得完整评分。请结合覆盖范围与问题明细阅读结果。`;
  if(run.status==='partial_scored')return run.scored_cases==null?'已有评分，但评分覆盖范围无法确认；请查看评分范围文件和报告，勿将其视为全量结果。':`当前评分覆盖 ${run.scored_cases}/${run.total} 个案例；其余答卷未纳入本次评分。已有评分会保留，若需全量评分请新建批次。`;
  if(run.status==='reviewed')return '检视已完成，尚无已确认的完整评分；下一步开始评分。';
  if(run.completed>0)return `已保存 ${run.completed}/${run.total} 份完整答卷；其余案例需检查，不能把当前结果当作整批测评结果。`;
  return run.status==='pending'?'测评尚未开始。开始后会先检查环境，再执行检视和评分。':'尚无完整测评结果；执行异常不等于模型得分为 0。';
 }
 function duration(seconds){if(seconds==null)return '尚未开始';return `${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;}
 function experiment(c){
  const groups=c.report.groups,total=groups.reduce((n,g)=>n+g.cases.length,0),done=groups.reduce((n,g)=>n+g.scored,0);
  const failed=groups.reduce((n,g)=>n+g.failed,0),timeouts=groups.reduce((n,g)=>n+g.timeouts,0);
  const live=['queued','running','cancelling'].includes(c.status);
  const label=live?({queued:'等待执行',running:'运行中',cancelling:'正在停止'}[c.status]):c.status==='cancelled'?'已停止':c.status==='interrupted'?'已中断':done===total&&total?'已结束 · 全部完成':done?'已结束 · 部分完成':'已结束 · 无完整评分';
  return {label,live,total,done,failed,timeouts,tone:live?c.status:done===total&&total?'succeeded':done?'partial':'failed'};
 }
 function caseStatus(c,live){
  if(!live&&['reviewing','scoring'].includes(c.status))return '已中断 · 需检查';
  if(!live&&c.status==='pending')return '未执行';
  if(c.error==='TimeoutError')return c.status==='score_failed'?'评分超时':'整例检视超时';
  return ({pending:'待运行',reviewing:'检视中',scoring:'评分中',complete:'已完成',review_failed:'检视失败',score_failed:'评分失败',setup_failed:'准备失败'}[c.status]||c.status);
 }
 const api={primary,result,duration,experiment,caseStatus};
 if(typeof module!=='undefined')module.exports=api;else root.BenchUX=api;
})(typeof window!=='undefined'?window:globalThis);
