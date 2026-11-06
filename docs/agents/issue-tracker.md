# Issue Tracker

使用本地 Markdown，不发布到远程平台。

- 每个功能使用 `.scratch/<feature-slug>/`。
- 规格保存为 `spec.md`。
- 工单分别保存为 `issues/<NN>-<slug>.md`，从 01 编号。
- 文件顶部用 `Status:` 记录状态，名称见 triage-labels.md。
- 评论追加在 `## Comments` 下。
- 技能要求“发布”时，写入对应本地文件；要求“获取”时，读取该文件。
- 更新已有文件前先读取，保留用户修改。
