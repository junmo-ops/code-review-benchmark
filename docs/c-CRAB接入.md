# c-CRAB 单例接入与本机验证

## 当前状态

2026-10-01：已完成素材接入和独立 DeepSeek 修复适配器，本机 Colima / Docker 环境已验证。实际执行状态以 `runs/ccrab/<run-id>/status.json` 与测试日志为准。页面已提供 c-CRAB 单例入口。

c-CRAB 是独立的执行验证专项，不套用 AACR 或 Martian 的语义评分。LCR 行为不变。

## 固定素材

- 来源：https://github.com/c-CRAB-Benchmark/dataset
- 固定提交：`856dfa3102b8996d168c4f195217b7603e1d1bc6`。
- 从最终 Stage 4 的 184 个案例中选择 NetworkX 仓库 patch 最小的一例：`networkx__networkx-7159@7e65b7d`。纯 Python、改动小，选择发生在本地检视之前，仅验证流程，不作为代表性抽样。
- 清单：`data/ccrab-pilot1.jsonl`；完整原始记录和来源哈希：`data/sources/ccrab/`。
- 使用上游 `testgen_combined.zip` 已发布的测试与 result.json，不重新生成测试、不调整标准答案。该记录标记 before 失败、after 成功；2026-10-01 本机容器已独立复核该对照。
- Git 仓库：`repos/networkx__networkx`，单独保存，不操作其他案例仓库。
- 官方单例评分源码及导入依赖：`history/ccrab-upstream`，原样保存并记录逐文件哈希。不是完整源码 checkout，不包含完整镜像构建资源；不会混入常规源码交付包。
- 固定上游树中未找到 LICENSE 文件，未推断授权。当前素材用于本地接入验证，未打包分发。

## 现在可以执行

在项目根目录运行：

```bash
.venv/bin/python backend/src/ccrab.py check
.venv/bin/python backend/src/ccrab.py plan
```

`check` 检查素材哈希、Git 提交、实际 diff、Docker 和镜像，并写入 `runs/ccrab/preflight/readiness.json` 和 `report.zh-CN.md`。条件不足返回 2；素材损坏返回 1。它不会安装容器、拉取镜像、读取令牌或调用模型。检查通过仅代表准备条件满足，不代表模型与测试已执行。

`plan` 输出给检视端的白名单字段和待运行阶段；不会把参考意见、最终修复 patch 或测试提供给检视工具。

官方脚本的导入检查已通过：

```bash
.venv/bin/python history/ccrab-upstream/run_tool_eval.py --help
```

此检查只证明 Python 入口可加载，不证明容器或修复能运行。额外 Python 依赖记录在 `backend/src/executable/requirements.txt`，未安装上游完整开发环境。

## 完整答卷转换

后续得到与此案例 repo/base/head 一致的工程标准答卷后：

```bash
.venv/bin/python backend/src/ccrab.py adapt \
  --answer /absolute/path/to/complete-answer.json \
  --output runs/ccrab/one-case/tool-results
```

输出为上游接受的 `<instance_id>/result.json`，工具标识 `lcr`，包含 `file/start_line/end_line/issue_header/issue_content`。拒绝不同案例、部分答卷和覆盖旧文件。转换不代表执行验证；完整空答卷保留，但上游脚本会跳过空意见，不能据此报告测试通过。

## 与官方执行器的区别

官方 `pipeline/agent_resolver.py` 在容器内调用 Claude Code；它的 `--model` 不能直接接收现有 DeepSeek OpenAI 兼容配置。用户选择 DeepSeek，因此本项目采用独立修复适配器和本地 Python 测试镜像，保留官方数据与发布测试，标记为方法适配实验，不对比官方榜单。

## 本例局限

该发布测试主要断言源码字符串包含指定 Unicode 转义写法，不是对所有语义等价修复的行为验证。原样保留并记录此限制，不为了得分改测试。即便未来一例通过，也只说明此例流程可运行。

准备验证：7 项接入与修复工具单元检查通过；官方 CLI help 可加载。这些检查不代表真实模型或容器验证通过。未执行 Playwright。

## DeepSeek 适配路径（2026-10-01 更新）

执行入口为 `backend/src/executable/ccrab_run.py`，修复工具为 `backend/src/executable/deepseek_repair.py`。

- 本机采用 Colima 专用 profile `ccrab` 和 Docker CLI；不安装桌面 Docker 应用。启动命令：`colima start ccrab --cpus 2 --memory 4 --disk 20 --vm-type vz --activate=false --ssh-config=false`。
- 使用本地 `python:3.11-slim` + `pytest==8.3.5` 镜像，运行时记录实际 image ID；不冒充官方发布镜像。宿主 ARM 与 Cloud x86 分别验收。
- 模型配置默认读取 `model.local.json` 中的 LCR DeepSeek 字段。检视端仍为原有 LCR；修复端是独立文件编辑循环，不修改 LCR 行为。
- 修复端预算：最多 20 轮，每次 8192 输出 Token、180 秒超时、SDK 不重试；900 秒后不再发起下一轮（在途请求仍受单次时限约束）。失败保留证据，不自动重试。
- 仅提供 PR diff、完整 LCR 意见与目标提交源码。工具仅支持列出路径、读取文件、唯一文本替换、结束；没有 shell 和网络工具，不可访问宿主其他路径，也不提供隐藏测试、人工参考意见或参考修复。
- 测试在无网络、无额外权限的容器内执行；源码与测试只读挂载，临时容器在结束或异常后删除。
- 首先验证原版本断言失败、参考修复版本通过，控制不成立就停止，不调用模型。之后修复完成才注入测试；不允许根据隐藏测试反馈重试修复。
- 空完整答卷不触发修复；本地约定执行检出率为 0，单独标注，非官方脚本的实际执行结果。

执行入口（安装与服务就绪后）：

```bash
.venv/bin/python -m executable.ccrab_run build --run-id ccrab-image-01
.venv/bin/python -m executable.ccrab_run run --run-id ccrab-deepseek-01
```

同名目录不覆盖，每次重跑需新批次。日志、模型请求与响应、完整答卷、修复 patch、容器测试结果和中文报告保存在 `runs/ccrab/<run-id>/`。目前这些目录中的真实执行证据才是验收依据，不能把命令准备或单元检查通过当作模型和容器验证完成。

## 本机容器环境说明

Mac 无法直接运行 Linux 容器，因此使用两层环境：Colima 的 Linux 虚拟机，以及 Python 案例测试镜像。虚拟机与镜像安装一次后复用，不会每轮重新下载。当前配置使用专用 `ccrab` profile，不切换用户默认 Docker context；命令显式使用 `colima-ccrab`。

本机 VM 镜像为 Colima 官方 v0.10.4 的 Ubuntu 24.04 ARM64 Docker 镜像，SHA256 为 `1fc0354f4f99734ce3886628cc7af8b0437c1a1d391b126bd09cba0df35ee53f`，已校验。首次启动发现 `/etc/resolv.conf` 指向缺失的 systemd-resolved 文件，已在专用虚拟机中修复；Docker 服务已配置宿主现有代理。代理依赖本机代理进程，迁移行内时需按行内网络重新配置，不能复制本机代理地址。

该 VM 挂载项目目录和系统临时目录用于测试；运行测试容器时仅挂载案例源码及发布测试。宿主 Apple ARM 验证不等于行内 x86 Cloud 验证。

## 2026-10-01 首轮真实结果

批次：`ccrab-deepseek-20261001-01`。镜像构建批次：`ccrab-image-20261001-03`；前两次环境失败日志保留，不计为模型失败。

- 容器：Linux ARM64、Python 3.11.16、pytest 8.3.5；镜像 ID 见批次 `config.json`。
- 发布测试对照：目标版本 1 条断言失败（0.42 秒），参考修复版本 1 条测试通过（0.37 秒）。对照成立。
- 检视：原有 LCR + DeepSeek 官方接口 `deepseek-flash`，1 个文件，11 秒，完整成功返回 0 条意见；累计 Token 32380。
- 修复：因无意见跳过，未调用 DeepSeek 修复 Agent，未产生修复 patch。不能声称“修复全流程通过”。
- 本地空答卷约定值为 0，明确不是官方脚本执行得分；不报告语义 F1。
- 独立修复器仅通过单元检查，有意见后的真实修复与测试分支仍待后续案例验证。本轮不注入参考答案，不换例追求成功。

证据：`runs/ccrab/ccrab-deepseek-20261001-01/` 中的 `report.zh-CN.md`、`answer.json`、`controls/`、`raw/`、`diagnostics/` 与 `config.json`。

下次复用环境：`colima start ccrab --activate=false --ssh-config=false`。不用时可执行 `colima stop ccrab` 释放虚拟机运行资源；这不会删除镜像或结果。首次创建的自定义挂载已保存在该 profile。

## 从页面运行

1. 打开本地测评工作台 `http://127.0.0.1:8765`。
2. 点击“新建测评”，选择 **c-CRAB / ccrab-pilot1.jsonl** 和已有 **DeepSeek** 配置；创建只冻结模型、接口摘要与执行源码/案例哈希，不调用模型。
3. 打开批次，点击“检查准备”，确认 Docker 服务和案例镜像就绪。已有环境可用 `colima start ccrab --activate=false --ssh-config=false` 启动。
4. 点击“运行单例验证”，依次执行发布测试对照、LCR 检视、DeepSeek 修复、修复后测试。无意见时跳过修复，显示“未检出 · 未修复”。
5. 在“执行日志 / 中文报告 / 原始文件”查看证据并保存单个文件。当前 c-CRAB 未提供完整归档按钮。

历史 CLI 批次自动显示，只可查看。页面批次每次仅执行一次；失败、中断或更换配置后需新建批次，不覆盖结果、不自动重试。修复后测试结果只在完整执行后展示，失败不计分，空答卷不显示测试通过。固定检视时限为 10 分钟，页面整流程保护上限为 45 分钟。

页面服务更新后需重启才能加载后端改动。此次接入通过服务接口和单元测试验证，按用户要求未运行 Playwright，浏览器交互由用户验证。
