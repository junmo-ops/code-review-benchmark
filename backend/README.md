# Backend

Python API、Worker 和测评执行代码统一位于 `src/`。生产与本地构建模式下，Python Web 服务同时提供 React 页面和 API；开发模式由 Vite 提供页面并代理 API。

依赖统一维护在 `requirements.txt`，`pyproject.toml` 引用它并定义安装后的命令。项目根目录执行 `npm run setup`，也可执行 `.venv/bin/python -m pip install -e backend`。

本地开发：`npm run dev`；运行构建后的 React 工作台：`npm start`。行内：使用本目录 Dockerfile，以仓库根目录构建；镜像构建时通过 Node 22 生成 React 静态页面，运行 `python -m deployment.container`，由 Python 提供页面/API 并按环境配置启动 Worker。

数据和第三方源码仍在仓库根目录，由 `runner.paths.PROJECT_ROOT` 统一定位。`BENCH_PROJECT_ROOT`、`PYTHONPATH` 由统一启动器和镜像设置；业务数据位置由 `BENCH_DATA_ROOT` 控制。禁止在源码中写入模型密钥或 COS 凭证。

`webui/` 是后端工作台模块的沿用名称，包含 API、任务管理和 React 静态文件服务；旧的独立 Node 生产服务已删除。现有测试迁入 `tests/`，此次没有新增测试用例。
