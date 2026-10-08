# 本地安全整改

上游来源及原始文件哈希见 [UPSTREAM.json](UPSTREAM.json)。以下差异仅修改本地副本，不改变上游来源记录：

- `requirements.txt` 将 PyTorch 最低版本提高到 2.6.0，并移除 Anthropic SDK 依赖。
- `eval_harness/model_clients.py` 与 `eval_harness/run_eval.py` 移除 Anthropic API、批处理和旧式默认路由；模型配置需使用仍支持的 OpenAI 兼容或 Gemini 接口。
- `eval_harness/model_endpoints.example.yaml` 和使用说明移除 Anthropic 配置示例。
- 删除只用于 Anthropic Sonnet 交叉裁判的可选维护脚本 `eval_harness/cross_judge_validation.py`。

项目内部 `swe_prbench_llm` 评分适配使用 OpenAI 兼容接口，不依赖上述 Anthropic 功能。Torch 版本下限按安全管控要求提升；当前项目的 SWE 评分路径固定为词面回退/大模型匹配，不安装或调用 Torch 嵌入模型。
