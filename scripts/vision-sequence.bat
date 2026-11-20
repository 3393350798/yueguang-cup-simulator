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
if not "%~1"=="" goto arguments
".venv\Scripts\python.exe" simulator\tools\vision_sequence.py serve
if not errorlevel 1 exit /b 0
pause
exit /b 1
:arguments
".venv\Scripts\python.exe" simulator\tools\vision_sequence.py %*
exit /b %errorlevel%
