@echo off
REM GeoSyntra Bridge - full commercial mode on this computer: website, sign-in, credits, owner console.
REM The owner account is the e-mail in owner_email.txt (one line, not committed): sign up with it on the website and you get the owner console.
REM Stripe / Google / GitHub keys are optional here - see deploy\commercial-settings.example.
cd /d "%~dp0"
set GSB_AUTH_MODE=accounts
if "%GSB_OWNER_EMAILS%"=="" if exist "%~dp0owner_email.txt" set /p GSB_OWNER_EMAILS=<"%~dp0owner_email.txt"
if "%GSB_DATA_DIR%"=="" set GSB_DATA_DIR=%~dp0data_commercial
call start_windows.bat %*
