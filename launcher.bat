@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher-menu.ps1" %*
exit /b %errorlevel%
