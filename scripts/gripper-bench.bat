@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat before opening the gripper bench.
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
if "%~1"=="" (
  ".venv\Scripts\python.exe" simulator\tools\gripper_bench.py --gui
  if errorlevel 1 pause
) else (
  ".venv\Scripts\python.exe" simulator\tools\gripper_bench.py %*
)
exit /b %errorlevel%
