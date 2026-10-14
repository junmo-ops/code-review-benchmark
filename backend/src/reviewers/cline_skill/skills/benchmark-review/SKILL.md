---
name: benchmark-review
description: Review a supplied code change for actionable defects using repository context, without editing code. Produce structured findings for an automated review evaluation.
---

Read REVIEW_REQUEST.json and REVIEW_DIFF.patch in the working directory. The repository snapshot is the requested head revision; the patch represents the requested merge-base range.

Inspect changed code and relevant definitions or callers. Report actionable defects introduced or exposed by the change, explaining the triggering condition and impact. Distinguish supported findings from speculation. Review only; do not modify code, execute project code, install dependencies, browse the web, delegate to other agents, or access paths outside this workspace.

Return a final JSON object, with no Markdown fences:
{"status":"complete","comments":[{"path":"relative/path","start_line":1,"end_line":1,"content":"具体问题、触发条件与影响"}]}

Use head-version line numbers starting at 1 and repository-relative paths. Describe findings in Chinese. If the review completed without actionable findings, return an empty comments array. If you cannot complete the review, return status "failed" and explain why; never represent an interrupted review as an empty successful answer. Do not claim a finding was fixed.
