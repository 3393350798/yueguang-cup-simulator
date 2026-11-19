@echo off
if "%~1"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0export-diagnostics.ps1" -OpenFolder
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0export-diagnostics.ps1" %*
exit /b %errorlevel%
