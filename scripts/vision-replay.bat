@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" simulator\tools\vision_replay.py gui
  if errorlevel 1 pause
) else (
  ".venv\Scripts\python.exe" simulator\tools\vision_replay.py %*
)
exit /b %errorlevel%
