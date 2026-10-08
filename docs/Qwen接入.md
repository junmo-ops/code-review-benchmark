# Qwen 接入

阿里云百炼与公司内网共用 OpenAI 兼容 Chat Completions 接入方式，分别保存模型、地址和密钥。不需要安装本地模型。相同模型名称不代表网关参数限制完全相同，两个环境各验证一次。

## 创建配置

在项目根目录执行以下命令，依次输入实际模型 ID、接口根地址、API Key（隐藏输入）。向导只保存配置，不发请求、不修改当前默认配置。裁判沿用 `model.local.json`，方便保持评分条件一致。

阿里云：

```bash
.venv/bin/python tools/configure_qwen.py --environment aliyun
```

公司内网（Linux）：

```bash
.venv/bin/python tools/configure_qwen.py --environment internal
```

Windows 使用 `.\.venv\Scripts\python.exe` 替换上述 Python 路径。

分别生成 `model.qwen.aliyun.local.json` 和 `model.qwen.internal.local.json`。服务器设置 `BENCH_CONFIG_ROOT` 时，向导默认在该目录读取来源配置并保存新文件，与网页一致。也可通过 `--source` 选原裁判配置，`--output` 指定新的 `model*.local.json` 文件。已有文件不会覆盖，密钥不放在命令参数中。

Base URL 填控制台/内网服务提供的 OpenAI 兼容根地址，不加 `/chat/completions`，不要填写网页地址或 DashScope 原生生成接口。阿里云根据地域、业务空间提供地址和相应凭证，不能假定所有账户共用一个地址。模型 ID 填账户可访问的实际名称；向导不替你选择版本。

## 在工作台运行

1. 刷新页面，点击“新建测评”。
2. 选择 AACR、“流程验证 · 1 例”、LCR CLI。
3. 在“模型配置”中选择刚生成的文件，核对检视模型为 Qwen、裁判仍是原模型。
4. 使用新批次名称。“仅保存配置”不调用模型；“创建并开始”会运行检视和评分，产生 API 用量。
5. 完整结束后查看报告、耗时和日志，再扩大案例数。不能把原 DeepSeek 批次直接切换成 Qwen 续跑。

配置缺少裁判字段时，请先补齐后评分。配置能被网页识别，只表示接入配置完成，不表示真实模型请求已经验证。

## 当前兼容边界

- AACR、SWE-PRBench、CodeFuse 的 LCR 检视复用现有调用链，模型需要支持非流式 Chat Completions 和工具调用。
- 已检查的 LCR 源码发送 `max_completion_tokens` 和可选 `temperature`，没有传入 `enable_thinking` 的现有配置入口。部分 Qwen 型号/部署对非流式思考、输出长度等有限制。遇到相关 400 错误，先保存错误并与 CLI 维护方确认参数适配，不靠增加超时时间解决，也不静默改变测评配置。
- MCR 的直接模型入口复用兼容接口，发送 `max_tokens`、`temperature`，要求输出完整 JSON；仍需单独验证，LCR 单例通过不代表 MCR 已通过。
- c-CRAB 当前固定 DeepSeek 修复流程，尚不支持通过此配置切换 Qwen，避免把检视模型和固定修复 Agent 同时更换。
- 本次只增加配置向导和文档，没有修改 LCR 请求逻辑，也没有发起真实 Qwen 请求。

官方参考：[文本生成与兼容接口](https://help.aliyun.com/zh/model-studio/text-generation)、[思考模式及非流式限制](https://help.aliyun.com/zh/model-studio/deep-thinking)。
