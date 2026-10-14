# 服务端 Agent 接入（协议草案）

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
- `repositories`：案例原仓库名 → 行内已导入仓库标识。导入仓库和保留完整提交历史需另行完成，适配器不自动上传或改造提交。
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

服务端模式的“超时分钟数”是每案例本地等待时限，不是服务端模型时限。关闭工作台或停止本地进程不代表取消远端任务。回到原批次，选中案例后点「恢复查询原任务」，只使用已保存 task_id，不再创建任务。

raw 目录保存创建意图、幂等键、task_id、原始结果、执行状态及追加事件日志；正式成功输出才进入 results。创建请求未收到 task_id 时暂停，交由维护方核对，不自行猜测。

真实联调首轮只跑 1 例：确认服务取得相同仓库和提交范围、实际模型、完整意见、引擎版本，然后才评分及扩大范围。工作台暂不支持自动发现服务端可用模型；请求模型不受支持时应由服务明确拒绝。
