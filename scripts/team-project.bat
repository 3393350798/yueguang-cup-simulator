@echo off
cd /d "%~dp0.."
.venv\Scripts\python.exe simulator\tools\team_project.py
if errorlevel 1 pause
