@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.bat first.
  if "%~1"=="" pause
  exit /b 1
)
if not "%~1"=="" goto direct
".venv\Scripts\python.exe" simulator\tools\firmware_bench.py --interactive
set "BENCH_RESULT=%ERRORLEVEL%"
pause
exit /b %BENCH_RESULT%
:direct
".venv\Scripts\python.exe" simulator\tools\firmware_bench.py %*
exit /b %ERRORLEVEL%
