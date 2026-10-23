# 07: 显式 POD 内 DSH 运行与单文件依赖

Status: ready-for-human

用户在真实 Kylin V10 x86_64 POD 中查到公司源中的 Bubblewrap，下载和签名校验通过，但实际非特权 bwrap 创建 namespace 被拒绝。用户明确选择方案 2：改为 POD 内直接执行，不额外套 Bubblewrap。不是原 sandbox 路径失败后自动降级；此确认修订旧规格的强制 OS 隔离条款，见 ADR 0002。

## Acceptance

- 显式 sandbox/pod 配置，非法值拒绝；默认 CLI 不降级，行内容器明确选择 pod。
- pod 不安装或调用 Bubblewrap/RPM；保留工具/路径 guard、原始 Skill 加载证据、独立案例会话、固定模型转发、时限及进程树清理。
- 队列、冻结配置、执行身份、答卷、页面与报告披露模式和无 OS 沙盒边界；模式变化停止，不把旧批次升级为新条件。
- Git-only 流水线可以使用单个经校验的 npm 依赖归档；完整锁定离线安装，不复制开发机 node_modules，不下载补缺包。
- check/verify、受影响后端和原 sandbox 回归、本地 e2e/explore；不调用真实模型/COS，不自动提交、推送或生成源码交付 ZIP。

## Comments

- 初始红灯：`test_dsh_pod` 的模式断言缺字段，POD 预检在缺 bwrap 的 PATH 下仍要求 Bubblewrap；真实退出码分别为 error/fail。首轮实现后 6 项 POD 专项通过，包含真实固定 DSH、完整 Skill/资源加载、恢复零重复调用、模式切换拒绝与父进程退出后的 detached 子进程回收。
- 已发现并处理官方旧版 bwrap 不支持 `--clearenv` 的兼容问题：sandbox 由受控启动环境清空继承变量，不依赖新参数；不关闭 OS 隔离。
- 校验中真实暴露过前端两函数复杂度 16 和离线网络隔离测试中的只读 /tmp 问题，分别通过复用风险提示组件及显式指向测试可写临时目录修复；不提高阈值或绕过校验。
- 单包已生成：`deployment/offline/dsh-npm.tar.gz`，130902940 bytes，SHA-256 `6d24a25a186101accaa0ec9993fcab7b45f3c79990506bcba2e13aba45756939`；npm 清单 SHA-256 `33692416807824f1aeb6c0f15ca5f4b6c2cb7df31078d373bbdd6021b2b6951b`。默认 Git 忽略，未暂存；行内需批准入库并确认单文件大小限制，LFS 必须实际取回内容。
- `npm run verify` 最终生产源码已通过，含真正 lint、Hook、TypeScript、9 项 Node 测试、Vite 构建与 diff 检查。部署/归档/容器 20 项 OK（1 项联网 npm 准备 opt-in 跳过）；另行实际执行单包无网络安装全部 615 项，且缺少可选 ripgrep 包时拒绝安装。实际 Docker 根上下文导出专项 1 项通过并核对归档摘要。标准回归 38 项、共享 cloud/dashboard/server_agent 23 项通过；不是本轮完整后端 discovery 声明。
- POD 实际 namespace 系统调用拒绝专项通过（18.010 秒）：测试用 libseccomp 明确拒绝 unshare 和 namespace clone，确认实际 unshare/bwrap 失败后启动真实固定 DSH，读取案例并完成答卷（本地模型 2 次）。这是本机故障注入，不复刻或认证公司完整 seccomp 策略，不向生产引入 libseccomp 依赖。
- 浏览器最终普通回归 15 项通过：`output/playwright/2026-10-23T02-51-11-821Z/`。探索回归 35 项通过：`output/playwright/2026-10-23T02-56-15-708Z/`；均使用 `E2E_DSH=1 E2E_DSH_RUNTIME_MODE=pod E2E_SIMULATED_LCR=1`，真实 DSH、本地确定性模型、模拟 LCR 与本地存储，无真实模型/COS。最终编译 UI 的 DSH Skill 专项 1 项通过：`output/playwright/2026-10-23T03-02-46-429Z/`，已查看移动端截图，提示文字正常换行。
- 探索故障注入边界：60 秒 soak 共 95 次迭代；上传块响应丢失 4.756 秒、完成响应丢失 4.388 秒，各 1 次持久化后丢响应。DSH 使用合成 50 例清单，挂起第 2 例后用户停止（47.616 秒）和 Worker 中断恢复（47.060 秒）；本地模型累计请求分别为 3 和 6，恢复没有增加请求，已完成答卷保留，进程回收 0.015/0.016 秒。不是完成真实官方 50 例，也不能从有限场景数推出不存在其他故障。
- 首轮完整 `npm run verify:dsh` 为 46 项、1 失败、1 跳过（757.145 秒），证据 `output/playwright/dsh-contract-2026-10-23T02-56-14-532Z/`。失败原文为“DSH 执行期间冻结依赖发生变化”；该轮运行期间确实编辑过 supervisor 源码。对应真实 sandbox 基线单项重跑通过（18.518 秒），但不以单项代替整套。
- 固定最终生产源码后的完整 `npm run verify:dsh` 退出码 0：47 项 OK（46 通过、1 项显式离线安装 opt-in 跳过），748.608 秒；证据 `output/playwright/dsh-contract-2026-10-23T03-09-01-952Z/`。该 opt-in 已在上面的部署专项实际执行，不依靠跳过声明离线安装成功。此次整套同时覆盖保留的实际 Bubblewrap 隔离和新增 POD 路径，新增 namespace 拒绝用例实际运行而未跳过。
- 无凭证本地独立预检退出码 0：`BENCH_DSH_ENABLED=1 BENCH_DSH_RUNTIME_MODE=pod ... deployment.dsh_preflight --check-runtime`，返回 `status=ready`、`os_isolation=false`、`runtime_namespace=not_applicable`、`software_guards=passed`。本机为 WSL2 Linux、UID 1000、glibc 2.39、Node v25.8.2，不是目标 Kylin/Node 22 镜像。
- 本地预览通过 `npm start` 在 `http://127.0.0.1:4185` 启动，首页 HTTP 200；清空继承环境后仅配置 POD 模式和三个 local 存储模式，数据/配置隔离在 `output/pod-preview/`，不载入真实模型或 COS 凭证。该预览有意保持运行，测试命令均已终止。
- 不改写 04/06 的历史完成状态；源码和依赖二进制均未提交或推送，未生成源码交付 ZIP。实际 Kylin 新镜像构建、公司 Git 接收此大文件、Node 22 ABI、实际 COS/模型与官方 50 例仍属行内人工验收，不在本地通过范围内。
