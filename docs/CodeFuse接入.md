# CodeFuse-CR-Bench 辅测

AACR 主测；SWE-PRBench 和 CodeFuse-CR-Bench 辅测，分别报告。CodeFuse 公开项目为 [SWE-CARE](https://github.com/inclusionAI/SWE-CARE)，本工程套件名为 `codefuse_cr`。

## 当前接入范围

- 数据：官方 Hugging Face `inclusionAI/SWE-CARE` 的 test split，固定 revision `3b3a625ef26bd497a3a13485c0dc9ece537f24d0`，实读 671 行。Parquet 下载哈希与官方 LFS 清单核对一致。
- 选择规则：按待检视 patch 字符数从小到大、再按案例 ID 排序，选第一例 `psf__requests-5711@9484e13`；用于流程验证，不代表正式抽样。选择发生在检视前。
- 原始记录完整保留，含问题描述、参考意见、合并版本和分类。仅 repo/base/待检视 head 进入 LCR；不给参考意见、最终修复 patch，也暂不传 issue 背景。因此属于 code-only 仓库检视适配，不复现官方完整上下文实验。
- 评分使用已接入的 Martian Offline 提示词和评分函数。CodeFuse 官方原始综合评分未接入，`--evaluator original` 明确拒绝；已有 AACR/SWE 原始裁判不受影响。
- 不修改 LCR。无需安装奖励模型、嵌入模型或目标仓库业务依赖。只有行外读取 Parquet 时需要 pyarrow；已转换 JSONL 的本地/内网测评不需要。

## Mac 本地完整流程

在工程根目录执行，沿用 model.local.json 的现有配置：

```bash
run="codefuse-one-$(date +%Y%m%d-%H%M%S)"
.venv/bin/python local.py --timeout-seconds 60 init --suite codefuse_cr --evaluator martian --run-id "$run" --dataset data/codefuse-pilot1.jsonl --timeout-minutes 10
.venv/bin/python local.py --timeout-seconds 60 check --run-id "$run"
.venv/bin/python local.py --timeout-seconds 60 preview --run-id "$run" --instance 'psf__requests-5711@9484e13'
.venv/bin/python local.py --timeout-seconds 800 review --run-id "$run" --instance 'psf__requests-5711@9484e13'
.venv/bin/python local.py --timeout-seconds 600 eval --run-id "$run"
.venv/bin/python local.py --timeout-seconds 60 export --run-id "$run"
```

check/preview/review 会核对实际 merge-base diff 与官方 patch；仅忽略 Git index 哈希缩写和 hunk 标题的函数文字差异，不忽略改动内容。发现差异就停止。该例的 base 不是 head 的直接祖先，不能用简单的 base..head diff 替代 merge-base diff。

完整答卷才进入评分。失败保留 raw/diagnostics，不静默删例；评分协议或输入变化要新建批次。无需为了重新评分而重跑检视，答卷复用方式见[裁判对比](裁判对比.md)。

## 行内 Agent Cloud

使用本次更新后的源码包和资源包，按[Cloud 部署](行内Cloud部署.md)完成基础安装，再离线恢复新仓库：

```bash
.venv/bin/python tools/setup.py --repos-only --suite codefuse_cr --dataset data/codefuse-pilot1.jsonl
```

资源包包含 `bundles/psf__requests.git.tar.gz`，保留浅历史边界，无需在内网 clone。

运行入口改为 `.venv/bin/python cloud.py`；阶段和参数同上，去掉 local.py 专属 `--timeout-seconds`，review 增加 `--external-timeout-seconds 800`，平台工具自身 timeout 设置为至少 800 秒。每条命令都显式指定同一个 run-id，不依赖跨命令保留 shell 变量。真实接口令牌从平台环境变量提供，不放入仓库。

Mac 完成不等于 Cloud 验收，仍须在平台验证时限、持久化和日志下载。

## 更多案例

导入已下载的固定版本 Parquet 或 JSONL（不调用模型）：

```bash
.venv/bin/python tools/import_codefuse.py --source /path/to/test.parquet --instance 'psf__requests-5711@9484e13' --revision 3b3a625ef26bd497a3a13485c0dc9ece537f24d0 --output data/codefuse-custom.jsonl
```

可重复 --instance；`--smallest N` 仅用于小规模接入验证。正式样本应先固定仓库、类别与难度覆盖，再检视，不能按得分选样本。导入工具生成来源声明；自定义清单默认标记为未认证来源，需核对下载哈希和版本，不能仅填写 revision 就声称官方已验证。

新增仓库需预先准备对应历史和离线资源，确保 check 校验通过。原始 metadata 在评分 JSON 中保留，本次没有扩大正式案例量。

## 本次结果（2026-10-01）

批次：`codefuse-smoke-20261001-01`。

- LCR + DeepSeek 真实检视：1 个文件、约 4 秒、0 条意见，完整成功答卷。
- 参考意见 1 条，命中 0 条。Martian 上游约定下 Precision/Recall/F1 均为 0，不能推断工具整体能力。
- 没有候选意见，实际裁判 API 请求为 0；本例验证了空答卷计分与报告，不声称完成了 CodeFuse 非空答卷的真实语义判断。Martian 实际模型传输此前在 AACR/SWE 已验证。
- 结果：`runs/codefuse-smoke-20261001-01/report.zh-CN.md`；证据：raw/、diagnostics/、results/、judge/、metrics/。未调整得分、参考答案或 LCR 行为。

42 项回归测试通过，覆盖数据隔离、非法输入、patch 不一致拒绝、旧裁判版本兼容与裁判部分失败不计分。离线包验证范围另见[验证说明](验证说明.md)。
