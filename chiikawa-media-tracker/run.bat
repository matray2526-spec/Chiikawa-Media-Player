@echo off
setlocal
cd /d "%~dp0"

call "%~dp0setup.bat"
if errorlevel 1 (
    echo.
    echo Setup failed. Check the message above and try again.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo The app closed with an error.
    pause
)