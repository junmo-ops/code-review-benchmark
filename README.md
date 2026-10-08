# 代码检视测评工程

测评定位：**AACR 主测，SWE-PRBench 与 CodeFuse-CR-Bench 辅测**。另设 **MCR 多轮专项**，使用独立流程与报告。三套单轮集均已接入单例流程；当前 AACR 包含五例接入验证案例。Mac、Windows 与行内 Agent Cloud 共用测评流程，分别通过本地启动器和 `cloud.py` 执行。Windows 本地准备见 [Windows 本地运行](docs/Windows本地运行.md)；Windows 运行时从行内 npm 与 Python 包源安装。

当前主测 `aacr`，SWE 新批次使用 `swe_prbench_llm`。新批次默认采用独立的 **Martian Offline 裁判**；创建批次时指定 `--evaluator original` 保留各案例集原始大模型裁判。`swe_prbench` 保留旧词面评分批次兼容。三套分别报告，不以复现官方榜单为目标，也无需安装嵌入模型。

两套各跑一例见 [双案例集操作说明](docs/双案例集试跑.md)。

两种裁判复用同一份完整答卷，不重跑 LCR。见 [裁判切换与对比](docs/裁判对比.md)。

## 本地页面

双击根目录 **打开测评工作台.command**，或运行 `.venv/bin/python ui.py`，在浏览器选择案例集、创建批次、运行与停止任务、查看日志和中文报告。详见 [测评工作台](docs/测评工作台.md)。

## 需求与验收范围

[需求适配与范围说明](docs/需求适配与范围说明.md) 记录原方案的合理调整、暂不纳入的原因、结论限制与真正待完成事项。评估进展时区分范围调整和实施缺口；不表示需求方已正式验收。

## 操作说明

- [Cline + Skill 试跑](docs/Cline试跑.md)：DeepSeek 真实单例结果、执行边界和固定版本命令。

- [服务端 Agent 接入（协议草案）](docs/服务端Agent接入.md)：创建任务、查询状态、获取结果；本机模拟验证、恢复查询及真实联调边界。

- [对比实验](docs/对比实验.md)：同一案例集多模型串行运行、一对一质量指标和耗时/Token 汇总。

- [MCR 多轮专项](docs/MCR操作.md)：三轮历史回放、直接模型/LCR、状态评分与恢复。
- [CodeFuse 辅测](docs/CodeFuse接入.md)：单例命令、离线资源与接入边界。

- [测评对比规则](docs/测评对比规则.md)：比较工具、模型时固定哪些条件，以及主测、辅测的评分边界。
- [Mac 本地验证](docs/Mac本地验证.md)：本机配置、预览、检视、评分和恢复。
- [OpenAI 接入](docs/OpenAI接入.md)：GPT-5.4 配置与执行命令。
- [行内 Cloud 部署](docs/行内Cloud部署.md)：绑定仓库、安装离线资源与逐例执行。
- [平台执行提示词](docs/平台执行提示词.txt)：给平台 Agent 使用。
- [案例集扩展](docs/案例集扩展.md)：模块边界、接入步骤及评分版本管理。
- [验证说明](docs/验证说明.md)：已验证内容和仍待平台验证的边界。

所有命令都在本目录执行。已有的 `model.openai.local.json` 仍位于根目录，不需要重新配置。

## 目录用途

| 路径 | 用途 |
|---|---|
| `ui.py` / `webui/` | 本地测评页面；复用现有命令，任务日志在 `ui-state/` |
| `mcr.py` / `multiround/` | 独立多轮执行与状态评估，结果在 `runs/mcr/` |
| `vendor/mcr/` | MCR 固定版本匹配提示词、指标函数与 Apache-2.0 许可证 |
| `local.py` / `cloud.py` | Mac / Cloud 执行入口 |
| `docs/` | 操作说明 |
| `tools/` | 安装、配置、离线日志分析与打包工具 |
| `runner/` | 共用批次、调度、进程超时与存储 |
| `reviewers/lcr/` | LCR 调用、答卷转换、恢复和诊断 |
| `suites/` | AACR、SWE-PRBench、CodeFuse 的独立数据、评分与报告适配器 |
| `scoring/` | 新套件评分调度；复用冻结 Martian 核心，兼容旧批次 |
| `evaluators/` | 独立裁判选择、Martian 适配与评分版本冻结 |
| `vendor/martian/` | 固定版本的 Martian Offline 原始评分源码及 MIT 许可证 |
| `benchmark/` | 旧内部导入的兼容入口 |
| `vendor/aacr/` / `vendor/swe_prbench/` | 引入的上游源码、许可证及版本清单 |
| `framework/` | 旧目录迁移指引 |
| `data/` | 标准案例清单；`data/sources/aacr/` 保存五例原始记录与来源清单 |
| `examples/` | 不含真实令牌的配置模板 |
| `*.local.json` | 本机实际模型配置，不提交 Git、不打包 |
| `runs/` / `local-command-logs/` | 答卷、评分、诊断及外层命令日志 |
| `repos/` / `bundles/` | 案例仓库与离线 Git 历史 |
| `bin/` / `lcr/` / `wheelhouse/` | CLI 与离线运行依赖 |
| `.venv` | 指向父目录 Python 环境的软链接 |
| `history/ocr/` | 早期 OCR 测评证据，不随源码包交付 |
| `cli-logging-source/` | 日志测试 CLI 的构建来源，不随源码包交付 |
| `tests/` | 测评工具验证 |

运行所需仓库、CLI 和依赖仍保留。当前工程含 AACR 五例、SWE-PRBench 一例和 CodeFuse 一例，正式 100+ 例清单需要另行准备。

案例来源说明见 [AACR 案例来源](data/sources/aacr/README.md)。原始五例已与运行清单逐例核对；上游下载地址、版本及抽样种子未保存，当前只用于接入验证。

## 创建 AACR 批次

```bash
.venv/bin/python local.py --timeout-seconds 60 init --suite aacr \
  --run-id aacr-local-01 --dataset data/pilot1.jsonl --timeout-minutes 10
```

旧命令省略 `--suite` 时仍默认 AACR；已有批次继续按原 run-id 操作。新批次会冻结案例集、评分代码与参数。实际检视与评分仍会调用所配置的模型。

## 重新生成交付包

```bash
.venv/bin/python tools/build_delivery.py
```

生成父目录的 `code-review-benchmark-source.zip` 和 `lcr-cloud-assets.tar.gz`。源码包包含新目录结构；资源包用于 Linux x86_64、Python 3.11。打包不含真实模型配置、运行日志、历史结果和 CLI 源码。

### c-CRAB 执行验证专项（单例适配实验）

已固定 1 个 NetworkX 案例与发布测试，并提供独立 DeepSeek 修复适配器。本机 Colima / Docker 已就绪，测试对照成立；首轮 LCR + DeepSeek 完整检视返回 0 条意见，未触发修复，因此修复分支尚未实际验收。它是独立方法适配实验，不混入语义评分或对比官方榜单。页面已提供独立 c-CRAB 入口，可创建单例批次、检查环境、运行并查看日志/报告。见 [c-CRAB 接入说明](docs/c-CRAB接入.md)。

## 内网服务器模式

独立 Web 与 Worker、SQLite 持久队列、认证代理入口和 Linux 部署模板见 [单机内网部署](deployment/README.md)。本机 `ui.py` 入口保留；服务器不使用 `ui.py` 对外提供服务。现有 Linux 离线包要求 x86_64 / Python 3.11，服务器扩展依赖见 `deployment/requirements.txt`。
