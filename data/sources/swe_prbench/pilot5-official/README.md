# SWE-PRBench Stylelint 官方五例

来源：https://huggingface.co/datasets/foundry-ai/swe-prbench

固定版本：`b87f5797aef3ed2c3153bb1304ea4d801d36ba6e`，许可证 CC-BY-4.0。

从官方 eval_100 子集内 Stylelint 仓库案例中，按 diff_patch 字符数升序、task_id 次序选前 5 例；抽样时尚未读取人工标注。保留官方 annotation 及 A/B/C 冻结上下文。所选 task_id 和 SHA256 记录在 selection.json。

本包供 LCR 仓库适配流程验证，不代表 SWE-PRBench 全集表现，不与官方榜单直接比较。

| 案例 | PR | 人工标注数 |
| --- | --- | --- |
| stylelint__9060 | https://github.com/stylelint/stylelint/pull/9060 | 1 |
| stylelint__9026 | https://github.com/stylelint/stylelint/pull/9026 | 1 |
| stylelint__8953 | https://github.com/stylelint/stylelint/pull/8953 | 7 |
| stylelint__8985 | https://github.com/stylelint/stylelint/pull/8985 | 1 |
| stylelint__9062 | https://github.com/stylelint/stylelint/pull/9062 | 2 |
