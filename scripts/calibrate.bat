@echo off
cd /d "%~dp0.."
.venv\Scripts\python.exe simulator\tools\calibration.py
if errorlevel 1 pause
