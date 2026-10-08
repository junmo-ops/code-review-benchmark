# SWE-PRBench 历史适配

2026-10-01 起新测评选择 `swe_prbench_llm`，仅依赖大模型判定；本目录保留旧批次兼容。以下是历史词面回退协议，不再要求为当前内部测评补齐嵌入模型。

来源为 `vendor/swe_prbench/UPSTREAM.json` 固定的官方 MIT harness 源码；数据来自 Hugging Face 固定 revision，CC BY 4.0，见 `data/sources/swe_prbench/provenance.json` 及原始数据卡。上游源码未修改。

本适配器使用官方人工意见筛选、裁判提示词、配对归一化和维度评分函数，独立进程执行，不经过 AACR 评分器。

当前协议 `lcr_repository_variant`：
- 检视输入为仓库及固定 base/head，不向 LCR 传人工意见。
- 保留官方 A/B/C 冻结上下文作来源证据，但 LCR 不是固定上下文模型调用。
- 裁判使用已配置的 DeepSeek，一轮；官方论文的 GPT-5.2 裁判成绩不可直接比较。
- 为轻量离线迁移，固定上游内置 lexical fallback，无 SentenceTransformer / all-mpnet-base-v2。英文人工标注与中文输出的词面匹配局限需考虑；正式复现官方嵌入评分需另建协议版本及资源包。
- 不计算官方 overall_score，不合并 AACR 分数。上游评分可能对原始裁判配对归一化，两个结果都保存。
- 缺少人工标注、裁判失败/截断/缺失 ID/矛盾配对都停止评分，不使用上游假成功回退。

运行说明：`docs/双案例集试跑.md`。
