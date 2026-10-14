"""Shared storage helpers; no benchmark or reviewer dependency."""
import json
from pathlib import Path
from runner.paths import PROJECT_ROOT as ROOT

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

def safe_json(raw, label):
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{label} 不是 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} 顶层不是对象")
    return value

def validate_answer(item, path):
    answer = safe_json(path.read_text(encoding="utf-8"), "已保存答卷")
    if (answer.get("instance_id") != item.instance_id
            or answer.get("base_commit") != item.base_commit
            or answer.get("head_commit") != item.head_commit
            or not isinstance(answer.get("review"), dict)
            or answer["review"].get("status") != "complete"
            or not isinstance(answer["review"].get("comments"), list)):
        raise ValueError(f"已保存答卷范围或完整性无效：{path}")
    return answer
