"""Suite registry and frozen identity validation. Never guess an unknown suite."""
from .aacr.adapter import AACRSuite
from .swe_prbench.adapter import SWEPRBenchSuite
from .swe_prbench_llm.adapter import SWELLMSuite
from .codefuse_cr.adapter import CodeFuseSuite
SUITES = {"codefuse_cr": CodeFuseSuite(), "aacr": AACRSuite(), "swe_prbench": SWEPRBenchSuite(), "swe_prbench_llm": SWELLMSuite()}

# Exact source revisions retained for batches created before partial scoring and
# provenance metadata were added. The current bytes are pinned too, so later
# scorer changes cannot silently become compatible with an old batch.
AACR_COMPATIBLE_REVISIONS = {
    "suites/aacr/adapter.py": {
        "previous": {"c93ca39a1d9880584d72c6a1cfbecae2d79b2df1e99108343db64928408df283"},
        "current": {
            "7e6ff6a3c25130c5c8c068902e0c1475bf8a2854778d7db74cfe2e348723b975",
            "44bbadea69a331b8a63ccadb0fff0aacab9b5e9ec3dc2e7d1fe2c3cafea43881",
        },
    },
    "suites/aacr/evaluator.py": {
        "previous": {"881c95275bea4f9c860fb6d9a5d617cc1f1267392c935b7f92055798fed7a81b"},
        "current": {"72a75921d544b7ecbfd35db16c3650223b9838863adc7c9d8b79da056c76de0a"},
    },
    "suites/aacr/report.py": {
        "previous": {"f701e07ae5cd4f44dfcaed5da09e74816e83304502babf0ea860282bc81f0edb"},
        "current": {"fc729a96fc120bf1ff3bfed46b7ad463a2c01a617c107f4cd70895ca33c53c4b"},
    },
}


def compatible_frozen_identity(frozen, current, suite_id):
    if frozen == current:
        return True
    if not _is_compatible_aacr_pair(frozen, current, suite_id):
        return False
    previous_files = frozen.get("files")
    current_files = current.get("files")
    if not _same_identity_metadata(frozen, current, previous_files, current_files):
        return False
    return _has_only_compatible_aacr_changes(previous_files, current_files)


def _is_compatible_aacr_pair(frozen, current, suite_id):
    return suite_id == "aacr" and isinstance(frozen, dict) and isinstance(current, dict)


def _same_identity_metadata(frozen, current, previous_files, current_files):
    if not isinstance(previous_files, dict) or not isinstance(current_files, dict):
        return False
    previous_meta = {key: value for key, value in frozen.items() if key != "files"}
    current_meta = {key: value for key, value in current.items() if key != "files"}
    return previous_meta == current_meta and set(previous_files) == set(current_files)


def _has_only_compatible_aacr_changes(previous_files, current_files):
    changed = False
    for name, current_hash in current_files.items():
        previous_hash = previous_files[name]
        if previous_hash == current_hash:
            continue
        revisions = AACR_COMPATIBLE_REVISIONS.get(name)
        if not revisions or previous_hash not in revisions["previous"] or current_hash not in revisions["current"]:
            return False
        changed = True
    return changed

def get_suite(name):
    try:
        return SUITES[name]
    except KeyError:
        raise ValueError(f"案例集 {name!r} 尚未接入；已支持：{', '.join(SUITES)}。不能套用 AACR 评分。") from None

def ensure_compatible(suite, reviewer):
    missing = suite.required_capabilities - reviewer.CAPABILITIES
    if missing:
        raise ValueError("检视工具不支持该案例集所需能力：" + ", ".join(sorted(missing)))

def resolve_run_suite(config, verify=True):
    frozen = config.get("suite")
    if frozen is None:
        if config.get("schema_version", 1) != 1:
            raise ValueError("新格式批次缺少案例集信息")
        # Existing batches in this engineering project used only the AACR adapter.
        return get_suite("aacr")
    suite = get_suite(frozen["id"])
    if verify and not compatible_frozen_identity(frozen["identity"], suite.identity(), suite.id):
        raise ValueError("案例集适配器或评分器已变化；请恢复原版本或创建新批次")
    return suite
