@echo off
REM GeoSyntra Bridge - with accounts: landing page, sign-up / sign-in, credits, plans and the owner console.
REM Runs on http://127.0.0.1:8610 with its own data folder (data_accounts), next to the local version (start_windows.bat).
set GSB_AUTH_MODE=accounts
if exist "%~dp0owner_email.txt" set /p GSB_OWNER_EMAILS=<"%~dp0owner_email.txt"
set GSB_DATA_DIR=%~dp0data_accounts
REM First run creates a local virtual environment; requirements are re-installed whenever requirements.txt changes.
cd /d "%~dp0"
call :prepare || goto :eof
set OPEN=--open
:run
REM exit code 3 = the program files were updated: start again on the new version (no new browser tab)
".venv\Scripts\python.exe" -m geosyntra_bridge.server --port 8610 %OPEN% --watch %*
if errorlevel 3 if not errorlevel 4 (
  set OPEN=
  echo Restarting on the updated version...
  goto :run
)
goto :eof

:prepare
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
)
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
  echo Installing / updating dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
  copy /y requirements.txt ".venv\requirements.installed" >nul
)
exit /b 0

:nopython
echo Python 3.9+ was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
pause
exit /b 1
:fail
echo Dependency installation failed - see the messages above.
pause
exit /b 1
