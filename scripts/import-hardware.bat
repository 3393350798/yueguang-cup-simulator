@echo off
cd /d "%~dp0.."
.venv\Scripts\python.exe simulator\tools\hardware_editor.py
if errorlevel 1 pause
