@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.bat first.
  pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" simulator\tools\preflight.py --interactive
  if errorlevel 1 (
    pause
    exit /b 1
  )
  pause
  exit /b 0
)
".venv\Scripts\python.exe" simulator\tools\preflight.py %*
exit /b %errorlevel%
