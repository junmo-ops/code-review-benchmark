# 服务端 Agent 接入

## 公司接口（2026-11-05 确认）

新增 `company-repo-review-v1` 协议；原模拟协议继续保留，下面旧协议章节仅适用于 `review-service-v1-draft`。

复制 `agent.company.example.json` 为配置根目录中的 `agent.local.json`，或设置 `BENCH_AGENT_CONFIG` 指向该文件。不要覆盖已有配置而不核对。
示例已配置 AACR 50 例涉及的 19 个仓库，映射为 `S990768/LV01.30_项目名`，由平台在 `repo_name` 中携带组织前缀。
暂定地址 `https://codereview.paas.cmbchina.cn`，无需鉴权；即使设置了 `BENCH_AGENT_TOKEN`，公司协议也不发送该凭证。

- POST `/repo-review/async-report`，发送 `repo_name`、`commit_ids: [start, end]`、`review_mode: from_to`。
- GET `/review-report-scan/report/{report_id}`，从 `body.report` 读取报告，业务成功码必须为 `SUC0000`。
- 新批次直接将案例 base_commit/head_commit 作为 start/end，平台不克隆仓库，由服务端准备代码。批次冻结 diff_mode=direct；历史 merge_base 批次保持原有共同祖先口径，已提交任务复用保存的请求和 ID。升级适配器后仍执行工具身份校验，旧批次可能需要使用原版本，禁止改写冻结配置绕过。
- `PROCESSING` 继续轮询；`SUCCESS` 转换意见；`FAIL` 不生成正式答卷、不按零问题评分。模型异常退出由服务端标 FAIL，完整性采用已确认的状态约定。
- 行号按服务端已确认的 end 版本解释。优先使用 start_line/end_line；缺失时可用 line_number，无法确定行号或描述时保存原始结果并报错，不静默丢弃意见。
- 核对报告 ID、full_name、起止 SHA 和 scan_type。示例预期 full_name 与 repo_name 一致；若实际响应格式不同，先核对真实样例再适配，不自动忽略范围不符。
- 当前服务模型固定为 DeepSeek，具体型号未确认。批次及答卷显示“DeepSeek（具体版本未确认）”，来源标记为人工确认的模型系列；不会将页面选择的模型当成已在服务端使用。被测模型配置不会发给服务端，裁判仍使用既有配置。
- 兼容返回的 `scan_type: FROM_TO`。从 `extra_info.token_usage` 接入输入、输出和总 Token；缺失、无效或全零用量保留为未知，原始用量保存在答卷 service 字段。失败时展示 `extra_info.error` 并保存脱敏原始响应。没有提供的引擎版本记为未知。服务端未确认支持幂等；本地保存提交意图和 report_id，创建响应不明时不自动重发。恢复只查询原报告。本地停止不代表远端取消。

首次联调选择 Checkmate 单例，先预览请求再运行；此实现尚未经行内真实接口验收。页面仍保留既有模型配置选择，但公司协议的实际被测模型固定为上述标记。

## 行内 Windows 电脑本地联调

测评平台后端与 Agent 服务直接运行在同一台电脑时，使用 `agent.company-local.example.json`：地址为 `http://127.0.0.1:8000`，协议及 19 个仓库映射与公司接口配置一致，无需鉴权。它是独立配置，不改变原服务地址，也不覆盖已有 `agent.local.json`。

先启动 Agent，在浏览器打开 `http://127.0.0.1:8000/health` 确认服务可访问。然后在测评项目根目录打开 PowerShell：

```powershell
$env:BENCH_AGENT_CONFIG = (Resolve-Path .\agent.company-local.example.json).Path
npm run dev
```

如果测评平台已经启动，先正常停止原进程，再在上述终端重新启动，使后端及执行子进程继承同一配置。启动前确认没有运行中的测评；不要中断正在执行的任务来切换地址。

打开启动输出中的工作台地址（前端默认 5173；测评平台后端默认 8765；Agent 为 8000），新建测评时选择“服务端 Agent”，先跑 Checkmate 单例。平台会自动调用 `/repo-review/async-report`，再查询 `/review-report-scan/report/{report_id}`。不要在 `/docs` 中重复创建同一任务。

本地地址只改变请求目的地；Agent 仍按自己的配置访问 Git、数据库和模型。平台仍需要案例仓库历史来计算提交范围，仓库映射使用 `S990768/LV01.30_Checkmate` 等标识。裁判模型沿用工作台模型配置，不会被此次地址切换替换。

可在批次 `raw/<案例标识>.remote.json` 查看已发送请求和任务 ID，在 `.result.json` 查看脱敏原始响应，在 `.execution.json` 查看执行错误。网络连接失败时优先检查 Agent 是否启动及端口；创建请求响应不明时先查服务端，不能直接再次提交。

这份配置适用于电脑原生运行；若测评后端在容器或另一台机器，`127.0.0.1` 指向的是那个运行环境，需要另配可达地址。公司接口请求由 Python 后端发起，浏览器不直接访问 Agent，不需要为这条链路设置浏览器跨域。

切回原服务时，在重启平台前指定：

```powershell
$env:BENCH_AGENT_CONFIG = (Resolve-Path .\agent.company.example.json).Path
npm run dev
```

切换地址后新建批次；不要将旧地址的 report_id 拿到另一服务查询。

## 仓库路径前缀与克隆域名

平台请求发送 `repo_name: S990768/LV01.30_Checkmate`，服务端源码支持带 `/` 的仓库标识，报告 `full_name` 也使用该值。两份公司配置示例均已加入 `S990768/` 前缀。更新配置后须新建批次；已创建批次保留原映射与请求，不会自动改写。

前缀不会改变 Git 域名。服务端的 `GIT_REPO_URL` 模板仍需指向 `gitee-proxy.paasst.cmbchina.cn` 并保留 `{repo_name}` 占位符；不要再在模板中重复追加 `S990768/`。实际 Checkmate 克隆地址应为 `https://gitee-proxy.paasst.cmbchina.cn/S990768/LV01.30_Checkmate.git`。此域名模板属于服务端环境配置，平台不发送完整克隆 URL。

## 原模拟协议

当前实现的是待对接协议，不代表公司服务已经提供这些接口。用于提前准备工作台和适配层，待维护方回来确认路径、认证、字段、检视范围后再联调。没有修改 LCR 的业务逻辑。

支持 AACR、SWE-PRBench、CodeFuse 单轮案例。工作台「单模型测评」可选择「服务端 Agent（协议草案）」。MCR 的多轮状态、c-CRAB 的修复执行、多模型实验的服务端工具编排不在本次接入范围。

## 执行过程

工作台提交冻结的仓库标识、两个提交、比较方式、请求模型名 → 服务创建任务并返回 task_id → 工作台轮询 → 服务返回完整检视意见 → 工作台沿用选定的 Martian 或原始裁判评分。

不发送标准答案、案例参考意见、裁判配置或模型 API Key。被测模型配置只提供模型名称，实际模型鉴权和调用由服务端管理。结果必须回传实际使用模型和引擎版本。

## 三个接口

下列路径和字段是建议契约，未声称与现有公司接口一致。

### 1. 创建任务

`POST /review-tasks`，`Authorization: Bearer <服务凭证>`，`Idempotency-Key: <客户端生成的唯一键>`。

```json
{
  "repository": "benchmark/cline",
  "base_commit": "完整40位SHA",
  "head_commit": "完整40位SHA",
  "diff_mode": "merge_base",
  "model": "服务端支持的模型标识"
}
```

返回 HTTP 202（也接受 200/201）：

```json
{"task_id": "task-123"}
```

`merge_base` 表示先求两个提交的共同祖先，审查该祖先到 head 的差异；`direct` 表示直接比较 base 到 head。不要静默替换成“head 对父提交”模式。单提交服务不能直接声称支持整个范围。

幂等要求服务端对同一个键返回同一个任务，且输入变化时拒绝请求。客户端在 POST 前保存键和请求。创建响应丢失后不会自动重发：需要维护方通过键核对任务，避免不明状态下重复付费。

### 2. 查询状态

`GET /review-tasks/{task_id}`：

```json
{"task_id": "task-123", "status": "running"}
```

状态：`queued`、`running`、`succeeded`、`failed`、`partial`、`cancelled`。只有 succeeded 才取正式结果；其他终态不作为完整答卷评分。

### 3. 获取结果

`GET /review-tasks/{task_id}/result`：

```json
{
  "task_id": "task-123",
  "status": "succeeded",
  "complete": true,
  "simulation": false,
  "scope": {
    "repository": "benchmark/cline",
    "base_commit": "完整40位SHA",
    "head_commit": "完整40位SHA",
    "diff_mode": "merge_base"
  },
  "model": "服务实际使用的模型标识",
  "engine_version": "服务实际引擎版本",
  "failed_files": 0,
  "comments": [
    {"path": "src/example.ts", "start_line": 10, "end_line": 12, "content": "具体缺陷及影响"}
  ],
  "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}
}
```

行号是 head 版本的行号，从 1 开始，路径相对于仓库根目录。没有意见时返回 `comments: []`，未统计 Token 时返回 `usage: null`；未知不能冒充 0。实际范围、模型、完整性或模拟模式不符时拒绝评分。上述字段必须由服务根据执行事实填充，不能仅原样回显请求来冒充校验。

## 配置

复制项目的 `agent.example.json` 为 `agent.local.json`，也可通过 `BENCH_AGENT_CONFIG` 指定文件路径；内网服务器使用配置根目录中的同名文件。配置独立于模型连接，默认示例是本机模拟服务。

- `base_url`：服务地址。支持 HTTP/HTTPS；不跟随重定向，不自动重试网络请求。
- `repositories`：可选，案例原仓库名 → 行内已导入仓库标识；省略、留空或未命中时直接使用案例的 `repo`。案例已填写 `S990768/LV01.30_Checkmate` 时无需增加映射。导入仓库和保留完整提交历史需另行完成，适配器不自动上传或改造提交。
- `token_env`：保存服务凭证的环境变量名，默认 `BENCH_AGENT_TOKEN`。配置文件中不写凭证。
- `simulation`：真实联调时改为 false，并新建批次。
- `paths`：创建、状态、结果接口路径，查询路径包含 `{task_id}`。
- `request_fields`：请求字段重命名；`response_fields` / `result_fields` / `comment_fields`：响应字段映射，支持 `data.task_id` 这样的嵌套读取。
- `status_values`：服务状态到上述标准状态的映射，例如 `{"SUCCESS":"succeeded"}`。

范围内部字段仍遵循示例 scope；如果后端结构不同，需要明确增加范围转换。当前只支持 Bearer 认证、JSON 对象、意见数组一次返回；分页、回调、取消接口等尚未实现。配置及适配代码指纹在创建批次时冻结，变更后需新建批次。

## 本机验证（不调用模型）

在项目根目录操作。第一次配置且还没有 `agent.local.json` 时：

```bash
cp -n agent.example.json agent.local.json
```

一个终端运行：

```bash
.venv/bin/python tools/mock_review_service.py
```

另一个终端运行工作台：

```bash
.venv/bin/python backend/src/ui.py --no-open
```

新建单模型测评，选择 AACR 单案例清单、服务端 Agent；模型和裁判沿用已配置选项。模拟服务仅模拟任务进度，返回空意见，不读取仓库、不调用任何模型；模拟批次自动跳过评分，也禁止手动评分。

模拟完成只说明协议和任务管理可用，不说明服务端检视质量。模拟记录保存到 raw/*.simulation.json，绝不写入正式 results 答卷目录。测试服务任务只保存在内存中，重启会丢失，不用于真实执行。

也可运行自动检查：

```bash
.venv/bin/python -m unittest tests.test_server_agent -v
```

此测试含临时本机 HTTP 服务及真实 backend/src/cloud.py 子进程的 init/check/preview/review/status 流程；同时确认模拟评分被拒绝。不访问公司接口、不调用模型。

## 超时、恢复与证据

新建服务端 Agent 测评默认等待 60 分钟，可在高级参数中调整为 1–120 分钟；已有批次保留创建时的时限。服务端模式的“超时分钟数”是每案例本地等待时限，不是服务端模型时限。关闭工作台或停止本地进程不代表取消远端任务。回到原批次，选中案例后点「从已有输出恢复结果」，只使用已保存 task_id，不再创建任务。

raw 目录保存创建意图、幂等键、task_id、原始结果、执行状态及追加事件日志；正式成功输出才进入 results。创建请求未收到 task_id 时暂停，交由维护方核对，不自行猜测。

真实联调首轮只跑 1 例：确认服务取得相同仓库和提交范围、实际模型、完整意见、引擎版本，然后才评分及扩大范围。工作台暂不支持自动发现服务端可用模型；请求模型不受支持时应由服务明确拒绝。

服务端等待超时后，本次自动流程停止，不继续自动评分。任务 ID 和请求保留；确认服务端完成后，在原批次选中案例恢复查询，成功后再点击评分。

### 检视耗时口径

公司 Agent 的检视耗时取报告 `trigger_at` 到 `completed_at` 的间隔，包含服务端排队、检视和报告处理，不是纯模型调用时间。平台等待和恢复查询耗时仍单独保存在执行记录中，不叠加到服务端检视耗时。缺少时间、格式无效、结束早于开始、或仅一端带时区时，不推测耗时。

新答卷保存 `duration_seconds` 和来源标记。旧答卷已保存 `service.timing` 的，重新生成中文报告时可直接补展示；不会重写已评分答卷、旧 metrics 或评分缓存。结果对比同样按该时间读取，重复生成报告不会累计。若旧批次入口因冻结版本校验阻止操作，可用只读展示命令生成报告：`python -m suites.aacr.report <批次目录> --offline`，无需重新检视或评分。
