# Code Review Benchmark

代码检视测评工作台。前端使用 React、TypeScript 和 Ant Design；Python 后端提供页面、API、测评任务执行、评分及 COS 存储。生产环境由同一个 Python Web 服务提供页面和 API。

## 项目结构

```text
code-review-benchmark/
├── frontend/                  React 页面与 Vite 开发配置
│   ├── src/                   页面、组件、hooks、接口类型
│   └── package.json           前端依赖与构建命令
├── backend/
│   ├── src/
│   │   ├── webui/             API、任务队列、模型与资源管理
│   │   ├── runner/            进程、Git、存储和公共运行契约
│   │   ├── reviewers/         LCR、服务端 Agent 等检视工具适配
│   │   ├── suites/            案例集加载与原始评分适配
│   │   ├── scoring/           评分扩展与版本校验
│   │   ├── evaluators/        通用裁判适配
│   │   ├── comparison/        多模型对比
│   │   ├── multiround/        MCR 多轮执行
│   │   ├── executable/        c-CRAB 执行验证
│   │   ├── framework/         案例导入与协议
│   │   ├── benchmark/         测评记录与分析
│   │   └── deployment/        服务启动和 COS 恢复/备份
│   ├── tests/                 已有后端测试
│   ├── pyproject.toml         Python 包、版本及命令入口
│   ├── requirements.txt       统一后端依赖
│   └── Dockerfile             后端镜像（构建 React 并由 Python 提供页面）
├── scripts/                   跨平台安装、启动与停止
├── tools/                     案例打包、导入、诊断和项目校验
├── data/                      案例配置、官方标注和来源说明
├── vendor/                    固定版本的第三方基准源码
├── deployment/                行内部署配置与说明
├── docs/                      操作指南与技术文档
├── examples/                  示例输入和配置
└── package.json               项目级命令
```

`frontend/dist/`、`.venv/`、LCR 安装、仓库历史、上传包、模型密钥和运行记录都是本地产物，不进入源码仓库。服务端可写数据放在 `BENCH_DATA_ROOT` 指定的目录；现有本地数据路径保持兼容。目录调整不删除运行结果或私有模型配置。

## 首次准备

需要 Node 22.22、Python 3.12 和 Git。在项目根目录执行：

```bash
npm run setup
```

此命令创建 `.venv`，安装后端 Python 包及前端锁定依赖。它使用当前电脑配置的 Python/npm 包源，不调用模型。行内请先配置公司包源。LCR 与案例仓库资源按 [操作文档](docs/README.md) 准备。

## 启动

开发模式：

```bash
npm run dev
```

同时启动 Python API 和 Vite，浏览器打开 `http://127.0.0.1:5173`。前端修改自动更新；后端修改后重启。可用 `BENCH_API_PORT`、`BENCH_FRONTEND_PORT` 修改默认端口，启动器自动同步代理地址。Ctrl+C 停止本次前后端及 API 所管理的任务。

使用编译后的页面：

```bash
npm run build
npm start
```

浏览器打开 `http://127.0.0.1:4173`。Python 本地工作台同时提供编译后的 React 页面和 API。Windows 和 Mac 使用同一组命令；根目录双击脚本也调用 `npm start`。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `npm run setup` | 安装前后端依赖 |
| `npm run dev` | 同时启动开发前端和 API |
| `npm start` | 启动 Python 工作台，提供编译后的前端和 API |
| `npm run backend -- --port 8765` | 单独启动本地 API |
| `npm run frontend` | 单独启动开发前端，后端需另行启动 |
| `npm run build` | 前端类型检查和生产构建 |
| `npm run check` | 后端语法、目录及改动函数复杂度校验，前端 lint |
| `npm run cli -- --timeout-seconds 900 status --run-id 批次编号` | 调用原测评命令行 |

安装后的后端还提供 `bench-api`、`bench-cli`、`bench-cloud`、`bench-mcr`、`bench-ccrab` 命令，使用前需激活 `.venv`。后端包依赖仓库内的 `data/` 和 `vendor/`，生产镜像包含这些资源。

## 部署

- 镜像：`backend/Dockerfile`，构建上下文为项目根目录。
- 镜像构建阶段使用 Node 22 编译 React；运行阶段由 Python 同时提供页面和 `/api`。
- 平台只需一个 Web 服务单元，现有访问域名直接指向后端服务。
- 模型、认证、COS 和任务执行配置继续由后端管理。

详见 [单服务部署](deployment/单服务部署.md)。从旧项目更新时，根目录 Python 入口及 Dockerfile 已迁移，需要同步移除行内仓库的旧文件，并把流水线 Dockerfile 路径改为 `backend/Dockerfile`。

历史批次的运行记录与日志保留。适配器源文件位置调整后，旧批次可能触发既有版本一致性保护；新测评使用新目录冻结版本，不自动放宽旧批次评分校验。

开发校验和 ZIP 交付统一流程见 [开发校验与交付](docs/开发校验与交付.md)：改动后运行 `npm run check`，发布前运行 `npm run verify`，未提交改动包使用 `npm run package:changes`。
