# MCR 多轮专项接入方案

状态：已实现独立多轮模块；操作与当前验证边界见 [MCR操作](MCR操作.md)。2026-10-01 已核对官方源码，revision 为 fa88b72ca94b928560afd5573aaf715f64b7210b。

定位：AACR 主测，SWE-PRBench/CodeFuse 单轮辅测，MCR 多轮复审专项，单独报告。

## 协议选择

第一版采用官方历史回放：按真实 PR 轮次提供本轮之前的事件、当轮 diff 和相应上下文，要求输出 Defect Card（缺陷描述、previous_state、current_state、原因）。核对实际发布的 py_prompts.jsonl 后，确认提示词明确排除当前轮讨论；它与仓库中的构造脚本存在差异，采用冻结发布文件。该流程也不是让 Agent 自主修改代码并形成新的对话。

不把上一轮被测模型答卷自动加入官方输入；若后续测试 Agent 自身记忆和修复闭环，独立定义新协议，不与本版成绩混算。

## 执行与输入边界

一个案例对应一个 PR，包含多轮。新增独立多轮数据契约和 runner，不将现有单轮 ReviewCase 强行改义。每轮单独保存输入、提交、答卷、诊断和状态；可按轮次重启恢复，PR 完成率与轮次完成率分别汇总。

冻结官方数据与标注，分离 ground_truth，检查时间线截断。核查 PR/issue 描述和事件是否含未来信息，发现可疑时间边界明确记录，不静默宣称官方无泄露。代码读取必须对应当轮提交，先单案例顺序执行，不在共享工作区并发切换提交。

## 被测对象适配

- 直接模型：先复用官方提示词，通过现有接口直接返回 Defect Card，命名为 llm_direct。成绩属于该模型在指定提示词下的表现，不属于 LCR。
- LCR：本机 help 已确认支持 --background-file，可传入历史上下文，CLI 源码不修改。但当前常规 JSON findings 没有缺陷状态契约，适配器不会替它生成状态字段；缺失状态标为 unsupported/unknown，不能推断为 Resolved。
- 其他 Agent：声明多轮背景输入、状态输出等能力，再加入专项。不能通过裁判替被测工具补做状态推理来制造“支持”。

## 评分

保留 Martian 作为缺陷文本匹配的对照；它原生不是状态裁判。

MCR 专项优先接入官方一对一语义匹配与指标实现，复核状态指标代码。已检查的 Metric/prompt_builder.py 仅把缺陷标题和描述传入匹配提示词，因此不能把其命中率当成状态准确率。

状态部分必须独立：对已经匹配的缺陷比较当前状态/转移，报告匹配覆盖率、状态输出覆盖率及准确率；若采用自定义补充指标，命名 internal，不冒充官方完整协议。无输出不视作已解决，缺失/失败不当作成功空答卷。原始裁判与 Martian 分开存储，模型配置固定使用现有 DeepSeek；不与官方模型榜单直接对比。

## 验收顺序

1. 固定选择一个 2–3 轮 PR（不按得分筛选），准备全部历史、输入和离线资源，核对每轮 diff。
2. 直接模型跑完全部轮次，验证非空缺陷、状态结构、评分和中文报告。
3. 同 PR 验证 LCR 背景输入及实际输出能力；据证据决定完整状态评分或仅检视评分，清楚标注能力缺口。
4. 测试中途失败后按轮次恢复、未来信息隔离及旧套件兼容，再扩容与内网验证。

正式接入成本高于静态数据转换，不能用“把每轮分开跑 review”作为完整验收。

## 来源

- https://github.com/DeepSoftwareAnalytics/MCR-bench
- https://github.com/DeepSoftwareAnalytics/MCR-bench/blob/fa88b72ca94b928560afd5573aaf715f64b7210b/evaluation/Prompt_construction/construct_prompts.py
- https://github.com/DeepSoftwareAnalytics/MCR-bench/blob/fa88b72ca94b928560afd5573aaf715f64b7210b/evaluation/Metric/prompt_builder.py
