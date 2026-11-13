@echo off
rem ShotFlow installer. ASCII only: cmd.exe misparses UTF-8 Korean lines in batch files.
rem Everything is installed inside this folder (.venv, frontend\node_modules, data).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.10 -m venv .venv 2>nul
  if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo [ShotFlow] Python 3.10 or newer is required: https://www.python.org
  if not "%NOPAUSE%"=="1" pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe backend\install.py
set ERR=%ERRORLEVEL%
if not "%NOPAUSE%"=="1" pause
exit /b %ERR%
