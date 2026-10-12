"""Compare persisted scores only after verifying identical complete answers."""
import argparse, hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))
from runner.storage import save, validate_answer
from suites.registry import resolve_run_suite


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def score(run, cfg):
    if cfg.get("evaluator", {}).get("id", "original") == "martian":
        path = run / "metrics/martian.json"
        data = read(path)
        if data.get("status") != "complete":
            raise ValueError("Martian 评分未完成")
        summary = data["summary"]
        values = [summary[k] for k in ("precision", "recall", "f1")]
        name, rounds = "Martian Offline 适配", 1
    elif cfg.get("suite", {}).get("id", cfg.get("suite_id", "aacr")) == "aacr":
        path = run / "metrics/metrics_ocr_average.json"
        data = read(path); summary = data["summary"]
        if summary.get("missing_instances", 0) or summary["evaluated_instances"] != summary["total_instances"]:
            raise ValueError("AACR 评分未完成")
        values = [summary[k] for k in ("semantic_match_rate", "semantic_recall_rate", "semantic_f1")]
        name, rounds = "原始 AACR 大模型裁判", data["eval_rounds"]
    else:
        path = run / "metrics/swe_prbench_llm.json"
        data = read(path)
        if data.get("status") != "complete":
            raise ValueError("SWE 评分未完成")
        values = [data["summary"][k] for k in ("precision", "recall", "f1")]
        name, rounds = "原始 SWE 纯大模型裁判", data["protocol"]["eval_rounds"]
    return {"name": name, "precision": values[0], "recall": values[1], "f1": values[2],
            "rounds": rounds, "metrics_file": str(path),
            "metrics_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def compare(left, right, output):
    runs = [Path(left).resolve(), Path(right).resolve()]
    configs = [read(r / "run-config.json") for r in runs]
    for key in ("dataset_sha256", "reviewer_id", "tool_hashes", "review_model", "review_endpoint_sha256", "judge_model", "judge_endpoint_sha256"):
        if configs[0].get(key) != configs[1].get(key):
            raise ValueError("对照条件不一致：" + key)
    answers = []
    for run, cfg in zip(runs, configs):
        if hashlib.sha256((run / "dataset.jsonl").read_bytes()).hexdigest() != cfg["dataset_sha256"]:
            raise ValueError("数据清单已改变")
        suite = resolve_run_suite(cfg)
        current = {}
        for case in suite.load_cases(run / "dataset.jsonl"):
            answer = validate_answer(case, run / "results" / (case.safe_id + ".json"))
            current[case.instance_id] = hashlib.sha256(json.dumps(answer, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        answers.append(current)
    if not answers[0] or answers[0] != answers[1]:
        raise ValueError("两批次完整答卷不一致，不能作为裁判对照")
    scores = [score(r, c) for r, c in zip(runs, configs)]
    evidence = {"runs": [str(r) for r in runs], "identical_answers": answers[0],
                "dataset_sha256": configs[0]["dataset_sha256"], "judge_model": configs[0]["judge_model"], "scores": scores}
    output = Path(output)
    if output.exists() or output.with_suffix(".json").exists():
        raise ValueError("对比文件已存在，请使用新文件名")
    lines = ["# 同一答卷的裁判对比", "", "完整答卷及数据哈希一致；未重新执行检视。", "",
             "裁判模型：`" + configs[0]["judge_model"] + "`。", "",
             "| 方案 | Precision | Recall | F1 | 轮数 |", "|---|---:|---:|---:|---:|"]
    for s in scores:
        lines.append(f"| {s['name']} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} | {s['rounds']} |")
    lines += ["", "本表比较整套裁判协议，不仅是模型判断。AACR 原裁判含位置过滤；SWE 原纯大模型协议按确认结果一对一计数。Martian 只比较意见文本，允许一条意见匹配多条参考，TP 按命中参考计数，Precision 可能超过 100%。", "",
              "未匹配参考不等于已证实误报。单例只验证接入，不能推断工具整体效果，也不能与官方榜单直接对比。", ""]
    for run in runs:
        lines.append(f"- [{run.name}]({run / 'report.zh-CN.md'})")
    save(output.with_suffix(".json"), evidence)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("裁判对比：", output)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--left", type=Path, required=True)
    p.add_argument("--right", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(); compare(a.left, a.right, a.output)

if __name__ == "__main__":
    main()
