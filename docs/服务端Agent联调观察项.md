# 服务端 Agent 联调观察项

记录日期：2026-11-06。

## 职责与验证边界

测评平台只负责请求参数、响应字段、状态转换和测评结果接入。服务端内部逻辑由服务端团队负责，不在本项目修改，也不作为当前接口适配的前置条件。

下列观察来自本地 `/Users/moxiaojun/个人/llm_agent` 源码阅读，尚未通过真实接口复现。后续联调保留请求、原始响应和对应任务 ID，确认实际影响后反馈服务端。

## 调用侧接口适配事项

- `scan_type` 实际由服务端转成大写，范围检视返回 `FROM_TO`；调用侧目前按 `from_to` 校验，需要兼容。
- 服务端保存 `extra_info.token_usage`，字段为 `total_input_tokens`、`total_output_tokens`、`total_tokens`。联调确认查询响应后接入用量展示；不能仅凭数值零认定没有消耗。
- 服务端保存 `extra_info.error`。联调确认查询响应后用于展示失败原因。

## 后续测试观察项

| 观察项 | 源码线索 | 联调验证与反馈内容 |
| --- | --- | --- |
| 问题坐标可能被溯源位置覆盖 | `app/services/report/repo_report_processor.py` 使用 blame 的来源提交、路径和行号覆盖原问题位置 | 选择有历史行移动或文件改名的案例，对照 end 版本确认响应坐标；记录对评分的影响后反馈 |
| 报告缺失或解析失败可能仍返回成功 | `app/services/repo_review_persistence.py` 在无报告或解析异常后仍可能保存 `SUCCESS` 和空问题列表 | 与服务端协作验证报告获取或解析异常，区分明确零问题与结果缺失；不在正式任务上制造故障 |
| 范围比较采用共同祖先语义 | `assets/skills/leancode-review/scripts/prepare-review.py` 将两点范围转换为三点范围 | 通过分叉提交验证实际比较范围；当前调用侧已预先计算共同祖先，记录这一条件 |
| 缺失用量元数据时返回零 | `app/services/messaging/message_utils.py` 对缺失 usage 的消息跳过，累计值默认零 | 对照模型返回与查询响应，确认零值是否代表缺失，避免报告误导 |

以上均为待联调确认项，不代表线上已复现故障。
