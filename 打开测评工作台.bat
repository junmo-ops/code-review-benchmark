@echo off
setlocal
cd /d "%~dp0"
where npm >nul 2>nul
if errorlevel 1 (
  echo 请安装 Node 22。首次使用请执行 npm run setup 和 npm run build。
  pause
  exit /b 1
)
call npm run start
pause
