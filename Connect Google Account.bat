@echo off
title Drive Uploader - Connect Google Account
cd /d "%~dp0"
echo.
echo   ==============================================
echo    Connect your Google Account - one time only
echo   ==============================================
echo.
echo   A browser window will open in a moment.
echo     1. Log in with the Google account that owns your Drive
echo     2. Click "Allow" when Google asks
echo   Then come back here - this window confirms success.
echo.
tools\rclone.exe listremotes 2>nul | findstr /C:"gdrive:" >nul
if errorlevel 1 (
    tools\rclone.exe config create gdrive drive scope drive
) else (
    echo Account already set up - refreshing the login...
    tools\rclone.exe config reconnect gdrive:
)
echo.
if errorlevel 1 (
    echo   Something went wrong. Read the message above, then run this file again.
) else (
    echo   ==========================================
    echo   Success! You can close this window now.
    echo   ==========================================
)
echo.
pause
