"""AACR-specific grading integration and request audit, isolated from the runner."""
import asyncio,hashlib,json,os,sys
from types import SimpleNamespace
from runner.storage import ROOT,save,safe_json
from .evaluation_resume import prepare_scope, prepare_state, evaluate_rounds
from .judge_transport import JudgeTransport, REQUEST_TIMEOUT, RETRY_DELAYS
from .upstream import activate
activate()
from schema import load_instances

def load_judge_config():
    """Load project-local judge settings only for evaluation, never log values."""
    path = ROOT / "judge.local.json"
    if not path.exists():
        return  # Existing environment-only setup remains supported.
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        raise SystemExit("无法读取 judge.local.json，请检查 JSON 格式和文件权限。") from None
    keys = ("JUDGE_BASE_URL", "JUDGE_MODEL", "JUDGE_API_KEY")
    if not isinstance(settings, dict) or set(settings) != set(keys):
        raise SystemExit("judge.local.json 必须且只能包含 JUDGE_BASE_URL、JUDGE_MODEL、JUDGE_API_KEY。")
    for key in keys:
        if not isinstance(settings[key], str) or not settings[key].strip():
            raise SystemExit(f"请在 judge.local.json 中填写非空的 {key}。")
    # Explicit project settings take precedence over inherited user variables.
    for key in keys:
        os.environ[key] = settings[key].strip()



def stage_eval(instances, run, config):
    instances = prepare_scope(load_instances(run / 'dataset.jsonl'), run)
    if not instances:
        raise SystemExit("没有可评分的完整答卷；请先完成至少一个案例。")
    load_judge_config()
    for name in ("JUDGE_BASE_URL", "JUDGE_API_KEY", "JUDGE_MODEL"):
        if not os.getenv(name):
            raise SystemExit(f"缺少环境变量 {name}")
    if config.get("judge_model") and config["judge_model"] != os.environ["JUDGE_MODEL"]:
        raise ValueError("裁判模型与批次冻结配置不同，请使用新批次")
    endpoint_hash = config.get("judge_endpoint_sha256")
    if endpoint_hash and endpoint_hash != hashlib.sha256(os.environ["JUDGE_BASE_URL"].encode()).hexdigest():
        raise ValueError("裁判接口与批次冻结配置不同，请使用新批次")
    os.environ["JUDGE_USE_MOCK"] = "false"
    effective = dict(config, judge_model=os.environ['JUDGE_MODEL'],
                     judge_endpoint_sha256=hashlib.sha256(os.environ['JUDGE_BASE_URL'].encode()).hexdigest())
    prepare_state(instances, run, effective)
    _evaluate_with_transport(instances, run, config)
    stage_report(run, config)


def _evaluate_with_transport(instances, run, config):
    import judge  # noqa: E402
    if judge.USE_MOCK_LLM:
        raise SystemExit("裁判误设为 Mock，停止评分")
    transport = JudgeTransport(run)
    original, client = judge.match_semantic, judge._llm_client

    async def checked_match(reference, generated):
        transport.last_record = None
        decision = await original(reference, generated)
        if transport.last_error or not transport.last_record or transport.last_record.get('status') != 'success':
            raise RuntimeError('裁判请求失败或证据校验失败，进度已保存；可再次点击评分续评')
        return decision

    judge._llm_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=transport.create)))
    judge.match_semantic = checked_match
    status = "failed"
    try:
        evaluate_rounds(instances, run, config, transport)
        status = "complete"
    finally:
        judge.match_semantic, judge._llm_client = original, client
        _save_audit(run, config, transport.records, status)


def _save_audit(run, config, records, status):
    usage = {key: sum((r.get('usage') or {}).get(key, 0) for r in records)
             for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    save(run / 'judge-audit.json', dict(status=status, mock=False, judge_model=os.environ['JUDGE_MODEL'],
         judge_usage_only=usage, requests=records, semantic_matches=[], eval_rounds=config.get('eval_rounds', 3),
         transport=dict(request_timeout_seconds=REQUEST_TIMEOUT, retry_delays_seconds=list(RETRY_DELAYS))))


def stage_report(run, config):
    """Render saved scores without re-running the reviewer or judge."""
    audit_path = run / "judge-audit.json"
    if not audit_path.exists() or not (run / "metrics/metrics_ocr_average.json").exists():
        raise SystemExit("缺少已有三轮评分或裁判记录，不能补生成报告。")
    audit = safe_json(audit_path.read_text(encoding="utf-8-sig"), "裁判记录")
    if audit.get("status") != "complete" or audit.get("mock") is not False:
        raise SystemExit("已有真实裁判评分尚未完成，不能生成完成报告。")
    load_judge_config()
    for name in ("JUDGE_BASE_URL", "JUDGE_API_KEY", "JUDGE_MODEL"):
        if not os.getenv(name):
            raise SystemExit(f"报告翻译缺少 {name}，请检查 judge.local.json。")
    # Only translation may make requests here; the judge is never invoked.
    os.environ.setdefault("REPORT_BASE_URL", os.environ["JUDGE_BASE_URL"])
    os.environ.setdefault("REPORT_API_KEY", os.environ["JUDGE_API_KEY"])
    os.environ.setdefault("REPORT_MODEL", os.environ["JUDGE_MODEL"])
    from .report import generate_report
    print("中文报告：", asyncio.run(generate_report(run)))
