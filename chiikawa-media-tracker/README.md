# Chiikawa Desktop Club

A native Windows desktop companion prototype. Choose one source at a time: a local audio track, one microphone, one app's process tree, or a speaker's complete system-audio mix. The selected character reacts to estimated tempo and energy.

The main settings window can be hidden to the system tray while the transparent, always-on-top character window keeps running. Quit from the tray menu to stop the app completely. Refresh the app list while an app has an audio session, then select it to capture only that process tree. Selecting Chrome or Opera captures audio from that browser process tree, not an individual tab. The all-desktop-audio option captures the selected speaker's complete mix.

Drag the desktop character with the mouse to place it anywhere on the screen. Its dropped position is saved between launches.

Per-app capture uses the Windows process-loopback API and requires Windows 10 build 20348 or newer.

Standing character artwork is loaded from `assets/characters/<Character>.webp`. Use **Add / change artwork…** to override a preset with PNG, animated GIF, JPG/JPEG, or WebP art; **Use preset** restores the bundled image. Custom art is saved per character in `%APPDATA%\Chiikawa Desktop Club\settings.json` and appears in both the preview and desktop pet. Ensure you have permission to distribute the bundled artwork.

Enable **Character outline** in settings to add a silhouette border. Use the color swatch to choose its color; the outline is applied to still images and each frame of animated GIFs.

Choose **Bottom stroll** to keep the character in the lower part of the screen, or **Whole desktop** to let it roam from top to bottom. The choice is saved and restored when the app starts.

Optional motion GIFs in `assets/characters` are auto-selected by filename: `<Character>-walking.gif`, `<Character>-idle.gif`, `<Character>-<mood>.gif`, and `<Character>-<mood>-idle.gif` (mood names: `dreamy`, `chill`, `upbeat`, `energetic`). Missing animations fall back to the character's selected or bundled standing artwork.

To build a custom character, select **Custom** in Settings, then open **Custom animations…**. Assign separate image or GIF files for standing, walking, idle, each mood, and each mood's idle state. Missing states fall back to another assigned state or the standing image.

## Run on Windows

Python 3.10 or newer is required. Tkinter must be included with the Python installation.

For the easiest launch, double-click `run.bat`. On first run it creates a project-local environment and installs dependencies; later launches start the app directly. No manual environment activation is needed.

In VS Code, open **Run and Debug**, choose **Run Chiikawa Desktop Club**, and press the play button. The pre-launch task prepares the environment automatically. After that setup, the Python file's Run button also uses the project environment.

## Build a Downloadable App

On Windows, double-click `build.bat`. It installs PyInstaller into the project environment if needed and creates `dist\ChiikawaDesktopClub\ChiikawaDesktopClub.exe`. Distribute the entire `dist\ChiikawaDesktopClub` folder, or zip that folder for a GitHub release; the EXE needs its neighboring files and bundled character assets.

### macOS

On a Mac with Python 3.10+ and Tk support, run `bash ./run-macos.sh` from Terminal. This creates `.venv` and installs the cross-platform dependencies without Windows-only packages. In VS Code, use **Run and Debug → Run Chiikawa Desktop Club**; the macOS setup task is selected automatically.

To build a Mac app bundle, run `bash ./build-macos.sh` on the Mac. It creates `dist/ChiikawaDesktopClub.app`; double-click it to run, or zip the `.app` bundle for distribution. Build on macOS because PyInstaller cannot cross-build Mac apps from Windows. macOS supports audio files and input devices. For system output, install a virtual input such as BlackHole and select it under **Input**. Per-app audio capture is currently Windows-only.

To prepare the environment manually without activating it:

```powershell
py -m venv .venv
\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
.\.venv\Scripts\python.exe main.py
```

Microphone access may require Windows privacy permission. Local audio formats are decoded by libsndfile; MP3 support depends on the libsndfile build.