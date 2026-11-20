@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat before opening the assembly check.
  if "%~1"=="" pause
  exit /b 1
)
if "%~1"=="" goto gui
".venv\Scripts\python.exe" simulator\tools\assembly_check.py %*
exit /b %ERRORLEVEL%
:gui
".venv\Scripts\python.exe" simulator\tools\assembly_check.py --gui
set "ASSEMBLY_RESULT=%ERRORLEVEL%"
if not "%ASSEMBLY_RESULT%"=="0" pause
exit /b %ASSEMBLY_RESULT%
