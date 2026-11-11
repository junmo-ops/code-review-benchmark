完整源码交付包

版本：4d1f7207f819e6e3897c3dafa8b8d0a36de440f3
范围：当前分支完整受版本控制源码，包含服务端 Agent 接入以来及最新使用指南改动。
本地校验：npm run package:changes 内置 verify 通过（lint、类型检查、构建及现有回归）；未运行 E2E、真实模型或行内部署验收。

迁移说明
1. 先备份原项目源码和本地配置。建议停止工作台后覆盖，避免前后端版本混用；如有测评正在执行，先等待结束。
2. ZIP 根目录直接对应项目根目录。保留原环境的 runtime、runs、repos、ui-state、case-repositories、case-datasets、模型密钥配置及 bin、lcr、runtimes 等运行依赖；本包不包含这些本机文件，也不包含 .git、.venv、node_modules 或前端构建产物。
3. 对照 DELETE_FILES.txt 删除废弃源码（本次为旧登录组件）。不要删除运行数据。
4. 依赖文件为当前分支版本，未为本次打包修改。请先备份行内特有的依赖文件及配置；若有差异，按项目 README 的行内依赖流程核对，避免直接覆盖已验证的行内定制。
5. 本地安装/更新依赖使用 npm run setup。开发运行 npm run dev；生产式本地运行先 npm run build，再 npm start。不要复制其他电脑的 .venv 或 node_modules。
6. 服务端 Agent 的本地配置示例为 agent.company-local.example.json（127.0.0.1:8000）；行内服务示例为 agent.company.example.json。按实际地址使用配置，参见 docs 下服务端 Agent 接入文档。

包内历史 TRANSFER_MANIFEST.json 等文件来自仓库，本次交付范围以本说明和 DELIVERY_MANIFEST.json 为准。
