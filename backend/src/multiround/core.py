import ast, hashlib, importlib.util, json, re
from pathlib import Path
from runner.storage import ROOT

CURRENT={'New','Open','Resolved','Reopened'}
PREVIOUS=CURRENT|{'NotPresent'}

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def identity():
    files=[ROOT/'backend/src/mcr.py', ROOT/'backend/src/local.py']
    for directory in ('backend/src/multiround','vendor/mcr','backend/src/runner','backend/src/reviewers/lcr','backend/src/evaluators','vendor/martian'):
        files += [p for p in (ROOT/directory).rglob('*') if p.suffix=='.py' or p.name in ('LICENSE','UPSTREAM.json')]
    return {str(p.relative_to(ROOT)):sha(p) for p in sorted(files)}

def load(path):
    with Path(path).open(encoding='utf-8') as f:cases=[json.loads(x) for x in f if x.strip()]
    seen=set()
    for case in cases:
        cid=case['instance_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+',cid) or cid in seen:raise ValueError('MCR 案例 ID 无效或重复')
        seen.add(cid)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',case['repo']) or '..' in case['repo'].split('/'):raise ValueError('仓库名无效')
        rounds=case['rounds']
        if len(rounds)<2 or [x['round_id'] for x in rounds]!=list(range(1,len(rounds)+1)):raise ValueError('MCR 必须包含连续完整多轮')
        for i,row in enumerate(rounds):
            if row['task_id']!=f'{cid}_round{i+1}' or not isinstance(row['prompt'],str):raise ValueError('轮次标识无效')
            for c in row['code_diff']['commits']:
                if not re.fullmatch(r'[a-f0-9]{40}',c['sha']):raise ValueError('提交未固定')
            if not row['code_diff']['commits'] or not row['code_diff']['diff']:raise ValueError('缺少当轮提交/diff')
            if any(e.get('round_id',0)>=row['round_id'] for e in row['history']):raise ValueError('历史含本轮或未来事件')
            future=[c['sha'] for later in rounds[i+1:] for c in later['code_diff']['commits']]
            if any(c in row['prompt'] for c in future):raise ValueError('提示词提前暴露未来提交')
            if 'before round' not in row['prompt']:raise ValueError('仅支持已核对的 prior-history 提示词')
            for d in row['ground_truth']['defects']:
                if d.get('state') not in CURRENT or d.get('previous_state') not in PREVIOUS:raise ValueError('参考状态无效')
    if not cases:raise ValueError('案例为空')
    return cases

def validate_card(card,row,reviewer='llm_direct'):
    if card.get('task_id')!=row['task_id'] or card.get('round_id')!=row['round_id'] or not isinstance(card.get('defects'),list):raise ValueError('答卷任务/轮次/缺陷结构错误')
    seen=set()
    for d in card['defects']:
        if not d.get('defect_id') or d['defect_id'] in seen:raise ValueError('缺陷 ID 缺失或重复')
        seen.add(d['defect_id'])
        if not isinstance(d.get('description'),str) or not d['description'].strip() or not isinstance(d.get('title'),str):raise ValueError('缺陷描述无效')
        if reviewer=='llm_direct':
            st=d.get('state_transition') or {}
            if st.get('current_state') not in CURRENT or st.get('previous_state') not in PREVIOUS or not st.get('transition_reason'):raise ValueError('缺陷状态输出无效')
    return card

# Directly reuse the pinned upstream matching prompt.
spec=importlib.util.spec_from_file_location('mcr_upstream_prompt',ROOT/'vendor/mcr/prompt_builder.py')
upstream=importlib.util.module_from_spec(spec);spec.loader.exec_module(upstream)
# Extract the exact pure upstream function without executing its global FileHandler.
source=(ROOT/'vendor/mcr/metrics_calculator.py').read_text(encoding='utf-8')
fn=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name=='calculate_metrics')
namespace={'Dict':dict};exec(compile(ast.Module(body=[fn],type_ignores=[]),'<mcr_calculate_metrics>','exec'),namespace)
calculate_metrics=namespace['calculate_metrics']

def matching_pairs(decision,refs,preds):
    matches=decision.get('matches')
    if not isinstance(matches,list):raise ValueError('裁判缺少 matches')
    used_p=set();used_g=set();pairs=[]
    for m in matches:
        p,g=m.get('model_defect_index'),m.get('ground_truth_defect_index')
        if type(p) is not int or not 1<=p<=len(preds) or p in used_p:raise ValueError('裁判预测索引无效或重复')
        used_p.add(p)
        if g is None:continue
        if type(g) is not int or not 1<=g<=len(refs) or g in used_g:raise ValueError('裁判未满足一对一')
        used_g.add(g);pairs.append((p-1,g-1))
    # Unmatched predictions may be omitted by upstream prompt; never trust its totals.
    return pairs

def metrics(refs,preds,pairs):
    tp=len(pairs);known=current=transition=0
    for p,g in pairs:
        state=preds[p].get('state_transition') or {}
        if state.get('current_state') not in CURRENT:continue
        known+=1;current+=state['current_state']==refs[g]['state']
        transition+=state['current_state']==refs[g]['state'] and state.get('previous_state')==refs[g]['previous_state']
    return dict(calculate_metrics(tp,len(preds)-tp,len(refs)-tp),tp=tp,fp=len(preds)-tp,fn=len(refs)-tp,
                state_known=known,state_correct=current,transition_correct=transition,
                state_coverage=known/tp if tp else None,state_accuracy=current/known if known else None,
                transition_accuracy=transition/known if known else None,
                matched_reference_coverage=tp/len(refs) if refs else None)
