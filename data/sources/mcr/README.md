# MCR 小样本来源

上游：https://github.com/DeepSoftwareAnalytics/MCR-bench

冻结 revision：`fa88b72ca94b928560afd5573aaf715f64b7210b`；Apache-2.0 许可证见 `vendor/mcr/LICENSE`。

- `selected-rounds.json`：上游 `MCR-bench/dataset/py.jsonl` 中的三条原始记录。
- `selected-prompts.json`：上游 `evaluation/Prompt_construction/prompts/py_prompts.jsonl` 中对应的三条原始发布提示词记录；该文件使用 Git LFS，取实际内容而非指针。
- `selection.json` / `provenance.json`：数据来源摘要、固定 revision、原始大文件 SHA256、局部文件 SHA256、选择方法及排除项。
- `data/mcr-pilot1.jsonl`：将同一 PR 三条记录组合为多轮案例，提示词保持发布文本不变。逐轮标注与原始记录核对一致。

选择过程在调用模型之前完成：选择同时存在于两份文件、完整 2–3 轮的 Python PR；排除历史事件涉及本轮/未来轮或提示词直接含未来提交 SHA 的候选；按三轮提示词总字符数和 ID 排序选最小项。结果是 Flask PR 4268。它主要修改文档字符串的语法，适合验证低成本链路，不具备全面覆盖逻辑缺陷的代表性。

时间边界检查是结构性检查，不保证任意自然语言历史绝无未来信息。原始事件有被 GitHub 更新过的提交元数据，但发布提示词未渲染这些字段；实际使用发布提示词，不把整个原始事件 JSON 传给模型。

发布提示词写明“before round N (not including events in round N)”，与同 revision 的构造代码描述存在差异。本适配以发布文件为准，不宣称重新运行构造器可以产生完全相同的输入。

MCR 匹配源码保持原样在 `vendor/mcr/`，包括逐文件 hash。本项目只提取上游纯指标函数，避免执行其模块级日志文件创建；逐轮执行、中文报告与补充状态指标是本工程适配代码。
