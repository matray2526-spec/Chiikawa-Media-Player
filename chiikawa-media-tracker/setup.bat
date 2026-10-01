@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3 was not found. Install Python 3.10 or newer, then run this again.
    exit /b 1
)

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo Python 3.10 or newer is required.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the app's private Python environment...
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)

set "REQUIREMENTS_HASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -LiteralPath requirements.txt -Algorithm SHA256).Hash"`) do set "REQUIREMENTS_HASH=%%H"
if not defined REQUIREMENTS_HASH (
    echo Could not check the dependency list.
    exit /b 1
)

set "INSTALLED_HASH="
if exist ".venv\requirements.sha256" set /p INSTALLED_HASH=<".venv\requirements.sha256"
if /I not "%INSTALLED_HASH%"=="%REQUIREMENTS_HASH%" (
    echo Installing or updating app dependencies...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 exit /b 1
    >".venv\requirements.sha256" echo %REQUIREMENTS_HASH%
)

exit /b 0