@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-firmware.ps1
pause
