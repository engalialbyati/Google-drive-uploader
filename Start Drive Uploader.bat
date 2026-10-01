@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    start "" python uploader.py
) else (
    start "" pythonw uploader.py
)
