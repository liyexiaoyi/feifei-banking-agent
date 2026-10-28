@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 尚未安装运行环境，请先阅读 README.md 的安装步骤。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\launch.py"
if errorlevel 1 pause
