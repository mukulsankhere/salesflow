@echo off
REM SalesFlow one-click start (Windows)
REM Creates a local virtual environment, installs dependencies, starts the app,
REM and opens http://localhost:8000 in your browser.
cd /d "%~dp0"
echo ==============================================
echo   SalesFlow - starting
echo ==============================================

REM 1. Find Python 3
set PY=
where python >nul 2>nul && set PY=python
if defined PY goto gotpython
where py >nul 2>nul && set PY=py
if defined PY goto gotpython
echo.
echo ERROR: Python was not found.
echo   Install Python 3.10 or newer from https://www.python.org/downloads/
echo   IMPORTANT: tick "Add python.exe to PATH" on the first installer screen.
echo   Then run this file again.
echo.
pause
exit /b 1

:gotpython
%PY% -c "import sys; exit(0 if sys.version_info[0]==3 and sys.version_info[1]>=10 else 1)" || (
  echo ERROR: Python 3.10+ is required.
  pause
  exit /b 1
)
for /f "tokens=*" %%v in ('%PY% --version') do echo [1/3] Python found: %%v

REM 2. Virtual environment + dependencies
if not exist .venv\Scripts\python.exe (
  echo [2/3] Creating virtual environment (.venv)...
  %PY% -m venv .venv || (
    echo ERROR: could not create a venv. Re-run Python installer and enable venv.
    pause
    exit /b 1
  )
)
echo [2/3] Installing dependencies (first run only, ~30 seconds)...
.venv\Scripts\pip install -q --disable-pip-version-check -r backend\requirements.txt || (
  echo ERROR: pip install failed.
  echo   - Check your internet connection / corporate proxy.
  echo   - Retry manually:  .venv\Scripts\pip install -r backend\requirements.txt
  pause
  exit /b 1
)

REM 3. Start
echo [3/3] Starting server on http://localhost:8000
echo       Login: admin@salesflow.app / Demo@123  (or use the demo buttons)
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --port 8000 --app-dir backend
pause
