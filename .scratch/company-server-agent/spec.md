# 公司服务端 Agent 接入

按 2026-11-05 用户逐项确认接入真实协议：无鉴权，暂定 codereview.paas.cmbchina.cn；repo_name 使用 LV01.30_项目名；from_to 直接比较起止版本，意见行号属于 end；异常中断返回 FAIL。服务模型仅确认 DeepSeek 系列。

复用现有服务端执行、恢复、统一评分流程，保留原模拟协议。创建前计算唯一共同祖先并保存实际请求；恢复查询原报告，不重复 POST。成功返回完整意见后转答卷，FAIL、null 报告、范围不符及无效意见不作零问题评分。未知模型版本、引擎和用量不得伪造。

验证使用本地 Git 和模拟 HTTP 响应，不访问行内服务、真实模型或 COS。不新增测试文件；运行已有检查及临时专项验证。真实 Checkmate 单例仍需行内验收。

## 本地验证结果

- npm run check 通过（Python 语法/复杂度、ESLint、TypeScript）。前端 bundle 构建通过，git diff --check 通过。
- 既有 test_server_agent.py 10 项全部通过，包括模拟 HTTP 服务和 CLI 流程。
- 临时 MockTransport 验证：共同祖先、无 Authorization、PROCESSING/SUCCESS/FAIL、null 报告、业务失败、范围错误、空行号、创建响应丢失不重复 POST、无本地仓库恢复查询。未新增测试文件。
- 真实 CLI init 使用任意页面模型时仍冻结为 DeepSeek（具体版本未确认）；真实本地 Checkmate 历史预览得到此前确认的两条 SHA。
- 完整 verify 尚未通过：37 项 Node 检查中 36 项通过，DSH source-closure 检查报 Linked runtime package is not a source snapshot；未修改该检查或 DSH 源码。后续阶段不能据此声称完整 verify 成功。
- 未执行浏览器 E2E（沿用用户不跑 E2E 的要求），未调用行内接口、真实模型或 COS。
