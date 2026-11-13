@echo off
rem ShotFlow launcher. ASCII only: cmd.exe misparses UTF-8 Korean lines in batch files.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ShotFlow] Not installed yet. Run the install script in this folder first.
  pause
  exit /b 1
)

if not exist "frontend\dist\index.html" (
  echo [ShotFlow] Building the user interface for the first time...
  pushd frontend
  call npm run build
  popd
)

echo [ShotFlow] Running at http://127.0.0.1:8710 - close this window to stop.
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8710/"
cd backend
set PYTHONIOENCODING=utf-8
..\.venv\Scripts\python.exe -m shotflow serve
