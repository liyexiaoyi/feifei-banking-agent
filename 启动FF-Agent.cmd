@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
if exist "runtime\python.exe" (
  set "FF_PY=runtime\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "FF_PY=.venv\Scripts\python.exe"
) else (
  echo 未找到运行环境：缺少 runtime\python.exe 或 .venv\Scripts\python.exe。
  echo 请使用完整的便携压缩包，或按 README.md 的安装步骤操作。
  pause
  endlocal
  exit /b 1
)
"%FF_PY%" -I "scripts\portable_bootstrap.py" %*
set "FF_EXIT=%ERRORLEVEL%"
if not "%FF_EXIT%"=="0" pause
endlocal & exit /b %FF_EXIT%
