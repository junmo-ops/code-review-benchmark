# AACR 案例来源

本目录保存当前接入验证所用的五例原始记录和来源清单，属于当前测评数据。

- `pilot5.raw.json`：保留原始字段的五例记录；从历史目录移入，内容未改动。
- `provenance.json`：原始文件与运行清单的 SHA256、案例 ID、PR 地址及来源限制。
- `../../pilot5.jsonl`：五例标准运行清单。
- `../../pilot1.jsonl`：其中 `cline__cline@419534c` 一例，供先验证完整流程。

数据通过 `vendor/aacr/evaluation/converters/aacr_bench.py` 转成统一结构：`githubPrUrl` → `repo`，`source_commit` → `base_commit`，`target_commit` → `head_commit`，`comments` → `reference_comments`。已逐例核对转换结果，仓库、提交、参考评论、行号与当前清单一致。

当前只有五例 Cline 接入样本，不是完整 AACR 测评集，也不是具备代表性的正式抽样。早期下载地址、上游版本和抽样种子未保存，来源清单中明确留空，不作补猜。扩大范围前需要保存上游来源、版本、许可及抽样规则。评分框架是当前工程复用的框架；本说明不声称它与 AACR 官方某个版本的评分协议完全一致。

创建批次仍使用 `--dataset data/pilot1.jsonl` 或 `--dataset data/pilot5.jsonl`。原始记录不传给 LCR；其中参考评论仅用于裁判评分。历史 OCR 答卷和评分保留在 `history/ocr/`。
