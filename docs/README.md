\# Issue Audit API 文档\
\
\## 接口概述\
\
Issue Audit（报告审核）接口用于管理代码审查报告的复核流程，支持发起审核、查看待审核列表、完成审核等功能。\
\
\*\*基础路径\*\*: \`/issue-audit\`\
\
\*\*统一返回格式\*\*: \`Response\<T>\`\
\
\## 接口清单\
\
\| 接口名称 | 方法 | URL | 用途 |\
\|---------|------|-----|------|\
\| 发起人待发起的审核列表 | POST | \`/issue-audit/ready/my\` | 查询发起人待发起的审核报告 |\
\| 发起人发起中的审核列表 | POST | \`/issue-audit/pending/my\` | 查询发起人发起中的审核报告 |\
\| 发起人已审核报告列表 | POST | \`/issue-audit/reviewed/my\` | 查询发起人已审核的报告 |\
\| 发起审核 | POST | \`/issue-audit/initiate\` | 发起新的审核流程 |\
\| 待我复核的审核列表（不分页） | GET | \`/issue-audit/pending\` | 审核人查询待复核的审核报告 |\
\| 已复核的审核列表（不分页） | GET | \`/issue-audit/reviewed\` | 审核人查询已复核的审核报告 |\
\| 完成审核 | POST | \`/issue-audit/complete\` | 复核人完成审核操作 |\
\
\## 请求参数说明\
\
\### 公共查询参数\
\
\| 参数名 | 类型 | 是否必填 | 描述 | 格式 |\
\|--------|------|----------|------|------|\
\| startDate | LocalDate | 是 | 开始日期 | yyyy-MM-dd |\
\| endDate | LocalDate | 是 | 结束日期 | yyyy-MM-dd |\
\
\### 发起审核参数 (InitiateAuditRequest)\
\
\`\`\`json\
{\
&#x20; "meetingId": "string",\
&#x20; "troopId": "string",\
&#x20; "commitIds": [1, 2, 3],\
&#x20; "rtcIds": ["rtc1", "rtc2"]\
}\
\`\`\`\
\
\| 字段名 | 类型 | 是否必填 | 验证规则 | 描述 |\
\|--------|------|----------|----------|------|\
\| meetingId | String | 是 | @NotBlank(message = "无审批会议信息") | 会议 ID（仅展示，可为空） |\
\| troopId | String | 是 | @NotBlank(message = "未传递TroopId") | 团队 ID |\
\| commitIds | List\<Long> | 是 | @NotEmpty(message = "请选择提交记录") | 要审核的提交记录 ID 列表 |\
\| rtcIds | List\<String> | 是 | @NotEmpty(message = "请选择复核人") | 复核人 RTC ID 列表 |\
\
\### 完成审核参数 (CompleteAuditRequest)\
\
\`\`\`json\
{\
&#x20; "commitIds": [1, 2, 3]\
}\
\`\`\`\
\
\| 字段名 | 类型 | 是否必填 | 描述 |\
\|--------|------|----------|------|\
\| commitIds | List\<Long> | 是 | 要完成审核的提交记录 ID 列表 |\
\
\## 响应参数说明\
\
\### 统一响应格式 (Response\<T>)\
\
\`\`\`json\
{\
&#x20; "code": 0,\
&#x20; "message": "success",\
&#x20; "data": null\
}\
\`\`\`\
\
\| 字段名 | 类型 | 描述 |\
\|--------|------|------|\
\| code | Integer | 响应码 |\
\| message | String | 响应消息 |\
\| data | T | 响应数据 |\
\
\### 审核响应数据类型\
\
\#### CommitReviewAuditResponseDto\
\
\| 字段名 | 类型 | 描述 |\
\|--------|------|------|\
\| commitId | Long | 提交记录 ID |\
\| commitScanReportSummaryDtos | List\<CommitScanReportSummaryDto> | 扫描报告摘要列表 |\
\| auditReview | List\<AuditReviewDto> | 审核明细列表 |\
\
\#### AuditReviewDto\
\
\| 字段名 | 类型 | 描述 | 格式 |\
\|--------|------|------|------|\
\| batchId | String | 批次 ID ||\
\| meetingId | String | 会议 ID ||\
\| meetingDate | LocalDate | 会议日期 | yyyy-MM-dd |\
\| rtcId | String | RTC ID ||\
\| commitId | Long | 提交记录 ID ||\
\| status | AuditDetailStatusEnum | 审核状态 | 枚举值 |\
\| initiatedAt | LocalDateTime | 发起时间 | yyyy-MM-dd HH:mm:ss |\
\| reviewedAt | LocalDateTime | 审核时间 | yyyy-MM-dd HH:mm:ss |\
\
\#### InitiateAuditResponse\
\
\| 字段名 | 类型 | 描述 |\
\|--------|------|------|\
\| batchId | String | 批次 ID |\
\| meetingId | String | 会议 ID |\
\| meetingDate | LocalDate | 会议日期 | yyyy-MM-dd |\
\| commitCount | int | 提交记录数量 |\
\| reviewerCount | int | 复核人数量 |\
\| notifiedCount | int | 已通知人数 |\
\
\## 审核状态枚举 (AuditDetailStatusEnum)\
\
\| 枚举值 | 描述 |\
\|--------|------|\
\| PENDING | 未完成（邀请已发送，复核人尚未完成审核） |\
\| REVIEWED | 已完成（复核人已完成审核） |\
\
\## 请求示例\
\
\### 1. 发起审核\
\
\*\*请求\*\*:\
\`\`\`bash\
POST /issue-audit/initiate\
Content-Type: application/json\
\
{\
&#x20; "meetingId": "meeting_001",\
&#x20; "troopId": "troop_001",\
&#x20; "commitIds": [1001, 1002, 1003],\
&#x20; "rtcIds": ["rtc001", "rtc002"]\
}\
\`\`\`\
\
\*\*响应\*\*:\
\`\`\`json\
{\
&#x20; "code": 0,\
&#x20; "message": "success",\
&#x20; "data": {\
&#x20;   "batchId": "batch_001",\
&#x20;   "meetingId": "meeting_001",\
&#x20;   "meetingDate": "2023-11-18",\
&#x20;   "commitCount": 3,\
&#x20;   "reviewerCount": 2,\
&#x20;   "notifiedCount": 0\
&#x20; }\
}\
\`\`\`\
\
\### 2. 完成审核\
\
\*\*请求\*\*:\
\`\`\`bash\
POST /issue-audit/complete\
Content-Type: application/json\
\
{\
&#x20; "commitIds": [1001, 1002, 1003]\
}\
\`\`\`\
\
\*\*响应\*\*:\
\`\`\`json\
{\
&#x20; "code": 0,\
&#x20; "message": "success",\
&#x20; "data": null\
}\
\`\`\`\
\
\### 3. 查询待发起审核列表\
\
\*\*请求\*\*:\
\`\`\`bash\
POST /issue-audit/ready/my\
Content-Type: application/x-www-form-urlencoded\
\
startDate=2023-11-01&endDate=2023-11-30\
\`\`\`\
\
\*\*响应\*\*:\
\`\`\`json\
{\
&#x20; "code": 0,\
&#x20; "message": "success",\
&#x20; "data": [\
&#x20;   {\
&#x20;     "commitId": 1001,\
&#x20;     "commitScanReportSummaryDtos": [],\
&#x20;     "auditReview": []\
&#x20;   }\
&#x20; ]\
}\
\`\`\`\
\
\## 错误码说明\
\
\| 错误码 | 描述 | 可能原因 |\
\|--------|------|----------|\
\| 0 | 成功 | - |\
\| 400 | 参数错误 | 请求参数校验失败 |\
\| 401 | 未授权 | 登录状态失效 |\
\| 500 | 服务器错误 | 系统内部错误 |\
\
\## 注意事项\
\
1\. \*\*日期范围\*\*: 所有查询接口都需要提供开始日期和结束日期，格式为 \`yyyy-MM-dd\`\
2\. \*\*参数校验\*\*: 所有必填字段都有相应的校验规则，前端应做好验证\
3\. \*\*分页处理\*\*: 当前接口均为不分页实现，如需分页请与开发确认\
4\. \*\*审核状态\*\*: 审核状态为枚举值，前端应使用下拉选择器展示\
5\. \*\*时间格式\*\*: 所有日期时间字段都使用指定格式，前端应统一处理\
\
\## 相关文件\
\
\- Controller: \`src/main/java/com/cmbchina/cmbkb/service/issueaudit/adapter/api/IssueAuditController.java\`\
\- DTOs: \
&#x20; \- \`src/main/java/com/cmbchina/cmbkb/service/issueaudit/adapter/api/dto/CommitReviewAuditResponseDto.java\`\
&#x20; \- \`src/main/java/com/cmbchina/cmbkb/service/issueaudit/adapter/api/dto/AuditReviewDto.java\`\
&#x20; \- \`src/main/java/com/cmbchina/cmbkb/service/issueaudit/adapter/api/dto/InitiateAuditRequest.java\`\
&#x20; \- \`src/main/java/com/cmbchina/cmbkb/service/issueaudit/adapter/api/dto/InitiateAuditResponse.java\`\
\- 枚举: \`src/main/java/com/cmbchina/cmbkb/service/issueaudit/domain/enums/AuditDetailStatusEnum.java\`



后端更新了一版接口文档，你看看