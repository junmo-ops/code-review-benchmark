# 测评工作台前端

React + TypeScript + Vite + Ant Design。生产页面由 Python 后端提供，Node/Vite 仅用于安装前端依赖、开发热更新和构建静态文件。

- `src/pages/`：测评、执行记录、模型、仓库、案例集和多模型实验。
- `src/components/`：创建测评、批次详情与共用组件。
- `src/hooks/`：轮询、文件读取和异步操作状态。
- `src/api.ts`：登录、页面令牌、上传进度与下载。
- `src/types.ts`：接口类型。

## 本地开发

需要 Node 22.22 和 Python 3.12。在项目根目录执行：

```bash
npm run setup
npm run dev
```

开发时 Vite 提供页面并将 `/api` 代理到本机 Python 服务，浏览器打开 `http://127.0.0.1:5173`。前端修改自动更新；后端 API 默认监听 8765。

## 构建与本地运行

```bash
npm run build
npm start
```

构建结果写入 `frontend/dist/`。`npm start` 启动 Python 本地工作台，由同一端口提供 React 页面和 API，默认地址为 `http://127.0.0.1:4173`。

`npm run lint` 检查 React Hooks 和每函数复杂度上限 15。源码格式化使用 `npm run format`。构建产物和 `node_modules` 不提交 Git。

## 行内部署

使用项目根目录构建上下文和 `backend/Dockerfile`。Docker 构建阶段用 Node 22 编译 React，Python 运行服务提供页面与 API；平台不需要单独的前端服务单元。部署详情见 [单服务部署](../deployment/单服务部署.md)。
