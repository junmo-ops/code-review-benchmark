"""AACR-specific grading integration and request audit, isolated from the runner."""
import asyncio,hashlib,json,os,sys
from types import SimpleNamespace
from runner.storage import ROOT,save,safe_json,validate_answer
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
    # Use the suite-owned references, never the runner input handed to LCR.
    instances = load_instances(run / "dataset.jsonl")
    load_judge_config()
    for item in instances:
        if not (run / "results" / (item.safe_id + ".json")).exists():
            raise SystemExit("缺少完整答卷：" + item.instance_id)
        validate_answer(item, run / "results" / (item.safe_id + ".json"))
    for name in ("JUDGE_BASE_URL", "JUDGE_API_KEY", "JUDGE_MODEL"):
        if not os.getenv(name):
            raise SystemExit(f"缺少环境变量 {name}")
    if config.get("judge_model") and config["judge_model"] != os.environ["JUDGE_MODEL"]:
        raise ValueError("裁判模型与批次冻结配置不同，请使用新批次")
    endpoint_hash = config.get("judge_endpoint_sha256")
    if endpoint_hash and endpoint_hash != hashlib.sha256(os.environ["JUDGE_BASE_URL"].encode()).hexdigest():
        raise ValueError("裁判接口与批次冻结配置不同，请使用新批次")
    os.environ["JUDGE_USE_MOCK"] = "false"
    if (run / "metrics").exists():
        raise SystemExit("评分目录已存在，避免覆盖。请先检查已有结果。")
    save(run / "evaluation-config.json", {"suite_id":"aacr",
         "suite_identity":config.get("suite", {}).get("identity"),
         "judge_model":os.environ["JUDGE_MODEL"],
         "judge_endpoint_sha256":hashlib.sha256(os.environ["JUDGE_BASE_URL"].encode()).hexdigest(),
         "eval_rounds":config.get("eval_rounds",3), "line_k":config.get("line_k",1)})
    included = [i.instance_id for i in instances]
    save(run / "evaluation-scope.json", {"included": included,
        "excluded": [i.instance_id for i in load_instances(run / "dataset.jsonl") if i.instance_id not in included],
        "note": (f"本轮冻结清单共 {len(included)} 例，全部纳入评分；结论仅适用于本轮范围。")})
    import judge  # noqa: E402
    import pipeline  # noqa: E402
    if judge.USE_MOCK_LLM:
        raise SystemExit("裁判误设为 Mock，停止评分")
    from openai import AsyncOpenAI
    records = []

    async def create(**kwargs):
        record = {"prompt_sha256": hashlib.sha256(json.dumps(kwargs["messages"]).encode()).hexdigest()}
        records.append(record)
        try:
            async with AsyncOpenAI(api_key=os.environ["JUDGE_API_KEY"],
                                   base_url=os.environ["JUDGE_BASE_URL"],
                                   timeout=180, max_retries=0) as client:
                response = await client.chat.completions.create(**kwargs)
            record.update(status="success", answer=response.choices[0].message.content,
                          usage=response.usage.model_dump() if response.usage else None)
            return response
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__)
            raise
        finally:
            save(run / "judge-requests.json", records)

    judge._llm_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    original = judge.match_semantic

    async def checked_match(reference, generated):
        decision = await original(reference, generated)
        if records and (records[-1].get("status") != "success" or
                        not (records[-1].get("answer") or "").strip()):
            raise RuntimeError("裁判调用失败或空回答，停止评分")
        return decision

    judge.match_semantic = checked_match
    status = "failed"
    try:
        # Inherited AACR pipeline; OCR-shaped wrapper is an input compatibility layer.
        pipeline.run_eval_stage(instances, "ocr", run / "results", run / "metrics",
                                line_k=config.get("line_k", 1), limit_for_eval=None, eval_rounds=config.get("eval_rounds", 3))
        status = "complete"
    finally:
        usage = {key: sum((r.get("usage") or {}).get(key, 0) for r in records)
                 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        save(run / "judge-audit.json", {"status": status, "mock": False,
             "judge_model": os.environ["JUDGE_MODEL"], "judge_usage_only": usage,
             "requests": records, "semantic_matches": [], "eval_rounds": config.get("eval_rounds", 3)})
    stage_report(run, config)


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

