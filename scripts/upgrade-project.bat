@echo off
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
chcp 65001 >nul
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" simulator\tools\upgrade_project.py %*
  goto done
)
where.exe py >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11) do (
    py -%%V -c "pass" >nul 2>nul
    if not errorlevel 1 (
      py -%%V simulator\tools\upgrade_project.py %*
      goto done
    )
  )
)
where.exe python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul
  if not errorlevel 1 (
    python simulator\tools\upgrade_project.py %*
    goto done
  )
)
echo Install Python 3.11-3.13 and run setup.bat first.
cmd /c exit 1
:done
set "UPGRADE_RESULT=%ERRORLEVEL%"
if "%~1"=="" if not "%UPGRADE_RESULT%"=="0" pause
exit /b %UPGRADE_RESULT%
