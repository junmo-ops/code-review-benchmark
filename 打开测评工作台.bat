@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到项目 Python 环境。请先按 docs\Windows本地运行.md 准备 Windows 资源。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" ui.py
pause
