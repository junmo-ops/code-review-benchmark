# MCR 多轮专项操作

MCR 与 AACR、SWE-PRBench、CodeFuse 共用一个工程，但使用独立入口 `mcr.py`、模块 `multiround/` 和 `runs/mcr/` 结果目录。当前附带 Flask PR 4268，一个 PR、三轮。它验证历史回放与缺陷状态跟踪；成绩与单轮套件分开报告。

## 被测对象

- `llm_direct`：直接将固定上游逐轮提示词交给模型，输出 Defect Card。成绩属于模型，不属于 LCR。
- `lcr`：在当轮提交上运行 CLI，传入本轮之前的历史背景，核对 Git diff 后检视。保留原生 findings；没有原生状态字段，状态能力标为 unsupported。CLI 逻辑未修改。

三轮是固定历史回放，不自动追加模型上一轮答卷，不让模型修改代码。发布版提示词明确排除本轮讨论，参考答案仅交给裁判；上游构造脚本与发布版文字不同，以冻结发布数据为准。历史事件可能包含先前轮次的人工审查内容，这属于本协议的输入，不是完全盲测。

## Mac 本地先跑直接模型

在项目根目录执行。沿用现有 `model.local.json`：检视使用 `LCR_LLM_*`，裁判使用 `JUDGE_*`。可以用 `--config 路径` 指定其他配置；不要把真实配置提交 Git。

```bash
run="mcr-direct-$(date +%Y%m%d-%H%M%S)"
.venv/bin/python mcr.py init --run-id "$run" --dataset data/mcr-pilot1.jsonl --reviewer llm_direct
.venv/bin/python mcr.py --timeout-seconds 900 review --run-id "$run"
.venv/bin/python mcr.py --timeout-seconds 900 eval --run-id "$run"
.venv/bin/python mcr.py report --run-id "$run"
```

`init` 固定数据、代码、接口摘要与模型名；修改模型、参数或代码后新建批次。默认直接模型及 MCR 主裁判的输出上限为 32768；需修改时在 init 加 `--max-output-tokens 数值`。首次 8192 上限的实测耗尽了推理 Token、没有生成答卷，该失败记录保留，未评分。此参数不修改 LCR 的内部输出预算。

## 用 LCR 跑同一 PR

前提是已按本项目 Mac/Cloud 安装说明准备本平台 LCR。Windows tgz 不能用于 Linux。

```bash
run="mcr-lcr-$(date +%Y%m%d-%H%M%S)"
.venv/bin/python mcr.py init --run-id "$run" --dataset data/mcr-pilot1.jsonl --reviewer lcr
.venv/bin/python mcr.py prepare-repos --run-id "$run"
export LCR_DIAGNOSTICS_DIR="$PWD/runs/mcr/$run/diagnostics"
.venv/bin/python mcr.py --timeout-seconds 780 review --run-id "$run" --task pallets__flask-4268_round1
.venv/bin/python mcr.py --timeout-seconds 780 review --run-id "$run" --task pallets__flask-4268_round2
.venv/bin/python mcr.py --timeout-seconds 780 review --run-id "$run" --task pallets__flask-4268_round3
.venv/bin/python mcr.py eval --run-id "$run"
```

离线仓库来自 `bundles/pallets__flask.git.tar.gz`；`prepare-repos` 只在目录不存在时还原，不覆盖已有仓库。每轮切换至冻结的目标提交，遇到本地改动停止。MCR 各批次之间有仓库锁；不要同时在其他工具中操作该仓库。当前 LCR 适配固定文件并发 1、任务超时 10 分钟、每轮进程上限 720 秒。

## 行内 Cloud

绑定源码工程，解压本工程生成的 Linux 资源包，按已有 Cloud 部署说明创建 Python 环境。MCR 不依赖浏览器后台进程：每条命令同步执行，逐轮落盘。

使用平台环境变量配置密钥，源码包不包含 `model.local.json`。直接模型不用 Git 案例仓库；LCR 需要先执行 `prepare-repos`。每轮使用上面的 `--task` 命令：工具调用的 timeout 应略大于 `mcr.py --timeout-seconds`，例如外层 840000ms、脚本 780 秒。独立 shell 中不会保留上一条命令的 `$run` 或 export，因此在平台命令中使用明确的批次名称，并将环境变量配置到平台。

关闭浏览器/电脑后的执行、任务存活上限、文件持久化和下载能力仍需平台验证；本机通过不等于 Cloud 已验收。当前同步验证不依赖 nohup 或后台会话。

## 恢复、日志和导出

- 再次 `review` 会跳过完整答卷；指定 `--task` 可只执行一轮，但必须先完成前轮。
- 失败尝试不会静默覆盖。先确认旧进程停止、检查该轮日志，再对同一命令增加 `--retry`。旧尝试移入 `attempts/`。
- 评分失败只重试 `eval --task ... --retry`，不用重新检视。
- `status` / `report` 只读已有答卷与评分，不调用模型。PR 未全部评分，不发布该批次整体分数。
- `rounds/<task>/input.json` 是实际模型输入；`review/` 保存请求/响应或 CLI 输出；`answer.json` 是归一答卷；`judge/`、`score.json` 是评分证据；`report.zh-CN.md` 是中文报告。
- 外层命令记录位于 `local-command-logs/mcr-*.json`。LCR 诊断使用上面的 `LCR_DIAGNOSTICS_DIR` 单独落盘。

```bash
.venv/bin/python mcr.py status --run-id "$run"
.venv/bin/python mcr.py export --run-id "$run"
```

导出为 `runs/mcr/<批次>-results.tar.gz`，包含该批次输入、答卷、评分、报告及其中的日志，不包含密钥配置。外层命令日志不在此归档中。

## 两种评分

`eval` 复用冻结 MCR 上游的一对一语义匹配提示词及 P/R/F1 函数。micro 汇总与状态指标是内部补充协议：状态准确率只比较已匹配且明确输出状态的问题，必须同时看召回和状态覆盖率；不能将 100% 条件准确率当成完整多轮能力。

可复用同一答卷进行 Martian 缺陷匹配对照：

```bash
.venv/bin/python mcr.py --timeout-seconds 900 compare-martian --run-id "$run"
```

Martian 结果在各轮 `martian/`，沿用原生匹配计数，不评价状态，不与 MCR 的一对一分数合并。两种裁判都采用真实模型，空答卷的缺陷计数可直接计算，不会伪称调用了裁判。

本次使用缩小的样本、内部汇总和适配后的 LCR 输入，不与官方榜单直接比较。
