@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe tools\hardware_editor.py
if errorlevel 1 pause
