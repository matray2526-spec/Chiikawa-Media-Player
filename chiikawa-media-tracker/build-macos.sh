#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
bash ./setup-macos.sh

if ! .venv/bin/python -c 'import PyInstaller' >/dev/null 2>&1; then
    echo "Installing the packaging tool..."
    .venv/bin/python -m pip install --disable-pip-version-check 'pyinstaller>=6.0'
fi

echo "Building the macOS app bundle..."
.venv/bin/python -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --onedir \
    --name ChiikawaDesktopClub \
    --osx-bundle-identifier com.chiikawa.desktopclub \
    --exclude-module pycaw \
    --exclude-module process_loopback \
    --add-data "assets/characters:assets/characters" \
    main.py

PLIST="dist/ChiikawaDesktopClub.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string Chiikawa Desktop Club uses audio input to estimate music tempo and mood." "$PLIST"

echo "Build complete: dist/ChiikawaDesktopClub.app"
echo "Build this bundle on a Mac; PyInstaller cannot cross-build macOS apps from Windows."