@echo off
setlocal
cd /d "%~dp0"

call "%~dp0setup.bat"
if errorlevel 1 (
    echo.
    echo Environment setup failed.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo Installing the packaging tool...
    ".venv\Scripts\python.exe" -m pip install "pyinstaller>=6.0"
    if errorlevel 1 (
        echo.
        echo Could not install PyInstaller.
        pause
        exit /b 1
    )
)

echo Building the standalone Windows app...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name ChiikawaDesktopClub --add-data "assets\characters;assets\characters" main.py
if errorlevel 1 (
    echo.
    echo The build failed. Review the output above.
    pause
    exit /b 1
)

echo.
echo Build complete: dist\ChiikawaDesktopClub\ChiikawaDesktopClub.exe
echo Distribute the entire dist\ChiikawaDesktopClub folder, not just the EXE.
pause