"""Suite registry and frozen identity validation. Never guess an unknown suite."""
from .aacr.adapter import AACRSuite
from .swe_prbench.adapter import SWEPRBenchSuite
from .swe_prbench_llm.adapter import SWELLMSuite
from .codefuse_cr.adapter import CodeFuseSuite
SUITES = {"codefuse_cr": CodeFuseSuite(), "aacr": AACRSuite(), "swe_prbench": SWEPRBenchSuite(), "swe_prbench_llm": SWELLMSuite()}

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
    if verify and frozen["identity"] != suite.identity():
        raise ValueError("案例集适配器或评分器已变化；请恢复原版本或创建新批次")
    return suite
