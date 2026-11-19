@echo off
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first, then:
  echo .venv\Scripts\python.exe -m pip install -r requirements\serial.txt
  if "%~1"=="" pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" simulator\tools\check_serial.py --interactive
  if errorlevel 1 (
    pause
    exit /b 1
  )
  pause
  exit /b 0
)
".venv\Scripts\python.exe" simulator\tools\check_serial.py %*
exit /b %errorlevel%
