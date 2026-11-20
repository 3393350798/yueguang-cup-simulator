@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  if "%~1"=="" pause
  exit /b 1
)
".venv\Scripts\python.exe" simulator\tools\telemetry_record.py %*
if errorlevel 1 (
  if "%~1"=="" pause
  exit /b 1
)
