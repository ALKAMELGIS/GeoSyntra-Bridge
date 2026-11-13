@echo off
REM GeoSyntra Bridge - full commercial mode on this computer: website, sign-in, credits, owner console.
REM The owner: the first account created on this computer becomes the owner and gets the owner console.
REM Stripe / Google / GitHub keys are optional here - see deploy\commercial-settings.example.
cd /d "%~dp0"
set GSB_AUTH_MODE=accounts
if "%GSB_DATA_DIR%"=="" set GSB_DATA_DIR=%~dp0data_commercial
call start_windows.bat %*
