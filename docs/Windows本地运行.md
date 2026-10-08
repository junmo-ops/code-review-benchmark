# Windows 本地运行（AACR 五例）

目标是在一台行内 Windows x64 电脑上启动本地测评工作台，使用 LCR 跑完 AACR 五例并生成评分报告。工作台和结果都在这台电脑上；浏览器关闭后后台进程继续运行，电脑关机或退出工作台会中断任务。当前实现和资源尚未在 Windows 实机验收。

## 运行条件

- Windows 10/11 x64、Python 3.11、Git、Node.js 20 或更新版本。
- 可访问的 LCR 检视模型和裁判接口。模型令牌仅保存在本地模型配置中。
- 可访问公司的 npm 源和 Python 包源。
- AACR 案例 Git bundle，包含五例所需提交历史。

Mac `.venv` 不能复用到 Windows；运行时由行内包源安装。

## 准备 Windows 资源

Windows 运行时从公司 npm 源安装固定版本的 LCR，从公司 Python 包源安装项目依赖，无需另行交叉编译或制作 wheelhouse。运行时版本记录在 `reviewers/lcr/windows-runtime.json`。

在工程根目录运行 `tools/build_delivery.py`，生成 `code-review-benchmark-source.zip`。该 ZIP 包含源码和 AACR 五例所需的 Cline Git 历史，不含模型配置或令牌。将这个文件带入行内电脑即可。

## 安装和启动

在 PowerShell 中进入工程根目录，确认 `py -3.11 --version` 显示 Python 3.11，然后准备环境。脚本会使用电脑已配置的公司 Python 和 npm 包源：

```powershell
py -3.11 tools/prepare_windows.py
```

复制 `examples/model.local.example.json` 为工程根目录的 `model.local.json`，再填写可访问的模型检视与裁判接口及令牌，并限制本机文件访问权限。不要提交配置文件或粘贴到命令行。

双击 `打开测评工作台.bat`。工作台打开后，创建 AACR 单模型批次，选择 `pilot5.jsonl`，先运行检查，再启动测评。任务进度、日志、答卷、报告保存在本机 `ui-state/`、`runs/` 和 `local-command-logs/`。

完成后可在批次详情查看中文报告。要迁移到行内服务器时，重新打包源码并使用 Linux x86_64 / Python 3.11 资源包；Windows 的 `.venv`、LCR 可执行文件和运行批次不能直接当作 Linux 部署资源。

## 当前验收边界

本地代码已补 Windows Python 路径、`.cmd` LCR 调用和子进程树终止处理。五例完整检视、取消/超时清理、结果恢复、模型接口与工作台交互仍需在目标 Windows 电脑完成实机验收后，才能称为 Windows 版已跑通。
