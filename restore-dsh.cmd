@echo off
setlocal
cd /d "%~dp0"
where powershell.exe >nul 2>nul
if errorlevel 1 goto failed
if exist "deployment\offline\dsh-npm.tar.gz" (
  call :verify "deployment\offline\dsh-npm.tar.gz"
  if errorlevel 1 goto failed
  goto done
)
for %%F in ("deployment\offline\dsh\transfer-part01" "deployment\offline\dsh\transfer-part02" "deployment\offline\dsh\transfer-part03") do if not exist "%%~F" goto failed
if exist "deployment\offline\dsh-npm.tar.gz.pending" goto failed
copy /b "deployment\offline\dsh\transfer-part01"+"deployment\offline\dsh\transfer-part02"+"deployment\offline\dsh\transfer-part03" "deployment\offline\dsh-npm.tar.gz.pending" >nul
if errorlevel 1 goto failed
call :verify "deployment\offline\dsh-npm.tar.gz.pending"
if errorlevel 1 (
  del /q "deployment\offline\dsh-npm.tar.gz.pending"
  goto failed
)
move "deployment\offline\dsh-npm.tar.gz.pending" "deployment\offline\dsh-npm.tar.gz" >nul
if errorlevel 1 goto failed
:done
echo DSH dependency file restored and SHA256 verified.
echo Next: read IMPORT.txt. DSH is still disabled until you enable the Docker build.
pause
exit /b 0
:verify
powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference='Stop'; if ((Get-FileHash -LiteralPath '%~1' -Algorithm SHA256).Hash -ne '6d24a25a186101accaa0ec9993fcab7b45f3c79990506bcba2e13aba45756939') { Write-Error 'SHA256 mismatch'; exit 1 }"
exit /b %errorlevel%
:failed
echo Restore failed. Extract ALL FOUR archives to the SAME new folder first.
echo No existing dependency file has been overwritten.
pause
exit /b 1
