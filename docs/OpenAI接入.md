# 新增 OpenAI 检视模型

当前选定模型：GPT-5.4（API ID：gpt-5.4）。它默认reasoning effort为none，当前LCR无需新增reasoning_effort参数即可按该默认值请求。尚未进行真实API调用验证。

当前接入方式：OpenAI负责LCR检视，原DeepSeek继续裁判和中文报告。同一规则、案例、预算和评分流程保持一致；每个检视模型使用独立run-id。

## 配置

已有model.local.json时，执行：

```bash
cd '/Users/moxiaojun/个人/Code-Review-Bench标准化案例集测评/ocr-lab/code-review-benchmark'
.venv/bin/python tools/configure_openai.py --model gpt-5.4
```

脚本通过隐藏输入读取API Key，创建权限600的model.openai.local.json，复制原DeepSeek裁判配置；不会修改model.local.json，不调用接口。若原配置不存在，则裁判字段取环境变量，未配置的字段留空，评分前需补齐。

gpt-5.4只是已核对参数范围的示例，不代表账户已有权限或完成真实接入验证；可以将--model替换为账户可用的Chat Completions模型。自建OpenAI兼容网关可用--base-url指定其地址，但网关协议和模型参数必须实际支持。不要在URL中携带令牌。目标文件已存在时直接编辑，脚本停止覆盖。

原配置与新增配置都是Git忽略文件，必须留在本机，不上传行内。

## 先跑一例完整流程

```bash
.venv/bin/python local.py --config model.openai.local.json --timeout-seconds 60 init --suite aacr --run-id mac-gpt54-01 --dataset data/pilot1.jsonl --timeout-minutes 10
.venv/bin/python local.py --config model.openai.local.json --timeout-seconds 800 review --run-id mac-gpt54-01 --instance 'cline__cline@419534c'
.venv/bin/python local.py --config model.openai.local.json --timeout-seconds 1800 eval --run-id mac-gpt54-01
.venv/bin/python local.py --config model.openai.local.json --timeout-seconds 60 export --run-id mac-gpt54-01
```

每条命令都带同一个--config。模型名与检视接口摘要在init冻结，不能将DeepSeek批次中途切成OpenAI。失败先看保存的日志，确认旧任务已停止后才重跑。模型调用有API费用；配置脚本和init/check不调用模型。

切回原DeepSeek配置：省略--config即可，沿用对应DeepSeek批次或新建批次；不要复用mac-gpt54-01。

## 兼容边界

LCR调用Chat Completions和函数工具，设置max_completion_tokens（现有规则为58888），未设置temperature。本次没有修改CLI逻辑。GPT-5.4官方文档列出Chat Completions、函数工具及128000输出上限，与这些请求参数在静态检查层面匹配；真实权限、限流、tool调用响应仍需实际运行。

不要直接替换为任意OpenAI模型：模型输出上限可能低于58888，或者只支持Responses API。当前主链不是Responses API。选择其他模型前先核对参数，遇到CLI参数问题先记录并同步维护方，不能擅自改CLI策略。

裁判当前包含DeepSeek专用chat_template_kwargs，以及temperature、top_p和max_tokens；中文报告译文接口也有temperature/max_tokens。因而这次不把裁判切成OpenAI。若需要OpenAI裁判，需另做评分接口参数适配并明确评分口径，不能只改模型名和令牌。

依据：
- https://developers.openai.com/api/docs/models/gpt-5.4
- https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4

