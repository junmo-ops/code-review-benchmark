> 项目结构和日常启动已统一，见 [项目 README](../README.md)。容器单服务部署见 [Python 单服务部署](单服务部署.md)；本页保留 systemd 单机部署配置。

# 单机内网部署

Python 3.12 行内容器试运行参见 [容器试运行](容器试运行.md)。下文仍是有持久化目录的 systemd 部署流程。

状态：服务器模式已实现；本机做代码与进程验证。目标 Linux、公司认证、Docker x86 镜像和真实模型接口仍须在行内验收。现有 `backend/src/ui.py` 保持本机模式，不会自动切换到服务器队列。

## 运行结构

浏览器 → HTTPS 认证入口 → Gunicorn / Flask Web → SQLite 队列 → 独立 Worker → 现有测评入口。

一个代码仓库，两个 systemd 服务；第一版只有一个 Worker，多个批次排队，单个案例顺序执行。同一批次不能重复排队。Web 和 Worker 不共享内存状态。Web 重启不停止 Worker；关闭浏览器不影响任务。不开启自动付费重试。

Web API 仅绑定 127.0.0.1:8766，由反向代理对外提供 HTTPS。`backend/src/ui.py` 是本机工具，不作为服务器入口。当前所有已认证用户共享项目结果；不是多租户平台。

## 目录与前提

建议 Linux x86_64、Python 3.11、Git、Node.js、systemd 和 Nginx。现有离线资源包固定 Linux x86_64 / Python 3.11；其他 Python 或 CPU 架构必须重新准备依赖及 LCR 二进制。

- `/opt/review-lab`：代码、`.venv`、`bin/lcr`、LCR 二进制、固定案例素材、离线依赖。
- `/var/lib/review-lab`：`runs/`、`repos/`、`ui-state/queue.sqlite3`、任务日志、`local-command-logs/`。
- `/etc/review-lab/models`：`model*.local.json`，沿用已有字段。
- `/etc/review-lab/server.env`：服务环境，权限 600，由服务账号读取。
- `/var/lib/review-lab/.leancodereview`：服务账号 HOME 下的 LCR 会话与配置；确认 HOME 指向 `/var/lib/review-lab`。

服务账号 `reviewlab` 不使用 root。代码只由发布人员更新，运行数据由服务账号写入。不要把模型密钥写进源码或下发到浏览器。SQLite 放本机持久磁盘，不放 NFS/SMB；多机方案不在此版本范围。

## 离线安装顺序

1. 运维创建服务账号、上述目录和权限，上传对应版本源码包与 Linux 资源包。不要复制 Mac `.venv` 或 `local-runtime.json`。
2. 用现有 `tools/prepare_assets.py --archive <资源包绝对路径>` 校验并解压资源。服务端新增依赖在 `server-wheelhouse/`。
3. 配置并导出 `BENCH_DATA_ROOT=/var/lib/review-lab`，执行下列命令。`tools/setup.py` 把仓库还原到持久化目录；默认验证 AACR pilot5，其他清单需分别准备。

```bash
cd /opt/review-lab
export BENCH_DATA_ROOT=/var/lib/review-lab
python3.11 tools/setup.py --suite aacr --dataset data/pilot5.jsonl
.venv/bin/python -m pip install --no-index --find-links server-wheelhouse -r deployment/requirements.txt
```

4. 将 `deployment/server.env.example` 复制到 `/etc/review-lab/server.env`，修改域名及配置路径。用 `secrets.token_urlsafe(48)` 分别生成代理校验密钥与 CSRF Token；不要沿用模板字符串。
5. 将行内模型配置写入 `/etc/review-lab/models/model.local.json`，设置服务账号可读。配置文件名须符合 `model*.local.json`；支持平台环境变量作为模型来源。
6. 配置 HTTPS 认证入口。已有公司 SSO 时由网关校验用户，然后覆盖注入 `X-Bench-User` 和 `X-Bench-Role`（viewer/operator），同时注入 `X-Bench-Proxy-Token`。**不能透传浏览器自带的身份头**。代理 Token 须与服务环境一致。
7. 没有 SSO 的小范围试用可用 `nginx.conf.example` 的 HTTPS Basic Auth 模板，账号文件由运维创建。该模板所有认证账号都是 operator；只读角色须在网关单独映射。SSO 协议适配未实现，不能把示例称为已接入公司登录。
8. 安装两个 service 文件，先 `systemctl daemon-reload`，再启动服务：

```bash
sudo systemctl enable --now review-lab-worker review-lab-web
sudo systemctl status review-lab-worker review-lab-web
```

服务从同一个 EnvironmentFile 加载环境。命令行人工操作前，也必须导出同一组变量。`deployment/check.py` 可检查运行时基础条件，不调用模型。Nginx 配置与证书安装按公司运维流程执行。

## c-CRAB 的额外条件

服务器 Docker 使用 `BENCH_DOCKER_CONTEXT=default`，不安装 Colima。实际执行器必须具备 Docker 访问权限；容器资源、权限和网络按公司规范限制。Docker 权限应仅授予专用任务账号，不能开放 Docker API 到网络。

- 目标架构重新构建 Python 测试镜像，或通过获准的内网镜像仓库/镜像离线包导入；ARM 镜像不能当作 x86 验收结果。
- `repos/networkx__networkx` 放在数据目录，包含 base/head/reference 所需提交。
- 固定素材 `data/sources/ccrab` 和 `history/ccrab-upstream` 必须齐全。后者未包含在常规源码包，缺失会导致检查失败；其上游许可尚未确认，不在本次自动扩大发布范围。未获准交付该素材时，先验收其他案例集。
- 完成依赖后，在导出 server.env 的终端执行 `backend/src/ccrab.py check`。镜像构建需要内网可达的基础镜像和 Python 依赖源。
- 停止或异常后检查 Docker 容器。systemd 能清理子进程，但 Docker daemon 中的容器需要额外核实。

## 停止、中断与恢复

- 页面“取消排队”只取消尚未开始的任务；“停止”会向 Worker 写入停止请求，由执行器清理本任务进程。
- Web 服务可单独重启，不停止 Worker。
- Worker 正常停止会保留已完成结果，当前任务标记取消；不会自动从中间继续。
- Worker 异常退出时，systemd 以 `KillMode=control-group` 清理该服务进程组。Worker 再启动将残留 running/cancelling 标记为 interrupted，并暂停领取任务，防止旧进程尚在运行时重复请求；Worker 保持心跳，网页仍可查看历史结果。确认旧任务进程和容器已停止后，操作员可在网页恢复队列。
- 管理员先停 Worker 服务，检查旧检视进程及相关容器已结束，在相同环境下以服务账号执行：

```bash
/opt/review-lab/.venv/bin/python -m webui.worker --ack-interrupted
```

此命令只清除阻塞，不重跑中断任务；随后重启 Worker。对具体批次用已有恢复/显式重试功能处理；c-CRAB 仍需新建批次。

systemd 自动拉起与“自动重试测评”不同。启动失败或队列暂停时查看 `journalctl -u review-lab-worker`；Web 状态栏会提示 Worker 不就绪或队列需要确认恢复。

## 配置、升级和备份

排队时保存模型/接口配置摘要，执行前重新校验。排队期间改模型或接口，任务会拒绝运行；凭据可轮换。队列只保存配置名称与摘要，不保存密钥。

版本更新先停止接收/提交新任务，等待 Worker 空闲，再停止 Worker、备份数据、发布代码、重启服务。不要在 Worker 执行时原地替换代码。历史批次保留原冻结信息，源码变化后可能只读，不重写哈希伪装同一版本。

数据库备份使用 SQLite 在线备份 API，或停 Web 与 Worker 后整体备份数据库及其伴随文件；不要只复制运行中的 `.sqlite3`。同时备份 `runs/`、仓库快照、LCR 会话及配置，配置按密钥管理要求处理。旧 Mac 批次含绝对路径与工具哈希，可保留查看，服务器执行用新批次。

## 验收

1. 无认证不能打开页面或 API；viewer 不能启动/停止任务，伪造身份头和跨站请求被拒绝。
2. 连续提交两个批次，按序执行；重复提交同一批次被拒绝。
3. 关闭浏览器、重启 Web 后任务继续；日志和结果可再次读取。
4. 取消排队不调用模型；停止运行任务后无残留检视进程/测试容器。
5. 杀掉 Worker/重启机器后不自动重跑付费任务，队列进入待确认状态；解除阻塞后仅继续未启动任务。
6. 一例 AACR 完整检视和评分通过，再验证 SWE、CodeFuse、MCR。c-CRAB 要同时验证未修复失败、参考修复通过，并如实保留无意见/未修复状态。

本次本机验证不替代 Linux、认证网关、公司模型接口、c-CRAB x86 或系统重启验收。没有运行 Playwright，也没有为部署验证调用付费模型。

技术依据：[Flask 的 Gunicorn 部署说明](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)、[SQLite WAL 单机存储限制](https://www.sqlite.org/wal.html)。

## 本机验证记录（2026-10-01）

- 全套 78 项测试通过（包括队列持久化、取消、配置冻结、访问角色和跨站拒绝）。
- `tools/server_smoke.py` 启动真实 Gunicorn 与独立 Worker；Web 重启后队列保留，Worker 完成 AACR 初始化，外置数据/配置目录生效；模型调用 0 次。证据在 `ui-state/server-validation.json`。
- Python 编译与 JavaScript 语法检查通过。未运行 Playwright。
- 已准备服务器扩展依赖的 Linux x86_64 / CPython 3.11 wheels，位于 `server-wheelhouse/`；不是 Linux 上已安装验收。
- 暂未重新生成整个大体积交付归档；`tools/build_delivery.py` 已加入 deployment 与 server-wheelhouse，正式交付需按当前源码重新打包并验证资源清单。
