# 裁判层

`--evaluator martian` 是新批次默认值；`--evaluator original` 调用原套件评估器。缺少 evaluator 字段的历史配置继续走原逻辑。选择与代码/参数哈希在 init 冻结，不能在旧批次覆盖切换。

上游：https://github.com/withmartian/code-review-benchmark

版本：`e616e849755441da38f18bf3adba2c9583b03803`，MIT。vendor/martian/UPSTREAM.json 记录原始文件哈希；源码未修改。直接复用 `LLMJudge.match_comment`、JUDGE_PROMPT 和 `evaluate_review`，不是自行仿写提示词。

适配边界：

- AACR/SWE 参考意见与保存的完整答卷映射为 golden/candidates；不运行 Martian GitHub 抓取、意见提取与可选语义去重，不按 Martian 专属类别筛选。
- 裁判通过项目 JUDGE 配置直接访问 DeepSeek 等兼容接口；无需 Martian 账号。原传输方法被替换为现有接口，固定 temperature=0、max_tokens=8192、timeout=180 秒、max_retries=0、串行、一轮。它们与上游默认传输设置不同。
- 每对意见保存原始请求、响应和判定；不保存令牌。截断、格式错误或任意一对失败均不发布完整评分，另建评分批次复用答卷重试。
- 上游 TP 按命中参考计数，允许多对一，Precision 可能超过 1；相同文本存在字典合并行为。保留规则并在报告披露，不静默改成一对一。F1 由 P/R 推导。
- 不加位置过滤、不向裁判传 diff；未命中参考不能证明误报。此为原案例集上的 Martian Offline 裁判适配，不是完整官方数据集/流水线或榜单成绩。

`tools/compare_judges.py` 在评分之外生成同答卷对照表，拒绝输入或模型不一致的对照。
