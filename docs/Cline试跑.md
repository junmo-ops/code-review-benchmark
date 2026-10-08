# Cline + Skill 单例试跑

已用 Cline CLI 3.0.68、benchmark-review Skill 和 DeepSeek 完成 AACR 单例真实检视及 Martian 评分。Cline 通过项目独立 npm 安装使用，不改全局 CLI、不改 LCR 源码。安装目录约 362 MB，包含 329 个包；不能据此认定它比 OpenCode 更轻。

## 已验证结果

批次 `cline-skill-20261002-223715`，案例 `cline__cline@419534c`。

- 检视进程正常退出，耗时 68.12 秒，Cline 报告 18 个迭代。
- 原始 NDJSON 中存在 `skills` 工具加载 benchmark-review 的完整记录。
- 产生 3 条中文意见；参考意见 2 条，命中 1 条。
- 当前 Martian Offline 适配评分：Precision 33.3%，Recall 50%，F1 0.400。
- Cline 累计输入 652790、输出 3770、缓存读取 606464 Token。缓存是输入用量中的单独统计，不再加进 total。SDK 返回 totalCost=0 未作为实际免费或账单结论。
- 日志含 providerOptions 弃用警告，未阻止本例完成；没有改上游实现来消除警告。

这是接入试跑，不能据一个案例得出工具优劣。原始参考意见里还有“注释过于冗长”的意见；未匹配参考的检视意见也不能直接认定为事实误报。此次沿用单批次 Martian Offline 口径，不混入多模型实验的一对一评分。

首次试跑先保存输入范围、Skill 指纹、版本与预算，完成真实调用后才根据观察到的事件格式实现答卷转换并导入正式批次。未重跑检视、未修改检视意见。这个批次是接入证据；正式比较请用冻结适配器后的新批次。

## 当前执行边界

工作台「单模型测评」→ AACR 单案例清单 →「Cline + Skill（Mac 单例试跑）」。选择已有 DeepSeek 配置及裁判，整例预算建议先用 10 分钟。

现阶段只开放 Mac、单案例。Linux 隔离、公司内网接口、三个 HTTP 接口后面的 Cline 服务执行器尚未验证。本次真实运行走本地 Cline 适配器，之前的三个模拟 HTTP 接口仍保持模拟，不冒充已接通 Cline。

- 通过 git archive 创建 head 的临时代码快照，并提供 merge-base diff；不切换共享仓库、不复制其 .git。
- 用 macOS sandbox-exec 限制案例代码写入，拒绝读取测评工程目录。执行前实际验证过标准答案文件读取被拒绝。
- Skill 只提供通用缺陷检视步骤和 JSON 答卷格式，未含参考答案。案例仓库原有规则文件保留，属于本次上下文的一部分。
- 临时 HOME、凭证和状态与个人 Cline 配置隔离；执行结束清理临时目录。主日志保存在批次 raw 目录。
- 输出必须有 completed run_result、完整 JSON 答卷、Skill 加载证据，才能评分。原始结果无效时不将其当作 0 条意见。
- 已有执行记录不自动重跑；recover 只转换原始成功输出，不调用检视模型。

## 固定版本安装与命令

在项目目录运行（当前本机已安装）：

```bash
npm install --prefix runtimes/cline --save-exact --ignore-scripts cline@3.0.68
```

新的单例批次可用：

```bash
run="cline-skill-$(date +%Y%m%d-%H%M%S)"
./.venv/bin/python local.py --timeout-seconds 120 init --run-id "$run" --reviewer cline_skill --suite aacr --dataset data/pilot1.jsonl --timeout-minutes 10
./.venv/bin/python local.py --timeout-seconds 120 check --run-id "$run"
./.venv/bin/python local.py --timeout-seconds 700 review --run-id "$run" --instance 'cline__cline@419534c'
./.venv/bin/python local.py --timeout-seconds 600 eval --run-id "$run"
```

命令默认读取 model.local.json。模型令牌不放命令行。review、eval 会真实调用配置的模型；check 不调用。

实现：`reviewers/cline_skill/adapter.py`；Skill：`reviewers/cline_skill/skills/benchmark-review/SKILL.md`。输出字段为 path/start_line/end_line/content，继续复用原有案例集及裁判。
