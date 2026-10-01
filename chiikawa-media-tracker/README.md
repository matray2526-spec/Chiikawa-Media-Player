# Chiikawa Desktop Club

A native Windows desktop companion prototype. Choose one source at a time: a local audio track, one microphone, one app's process tree, or a speaker's complete system-audio mix. The selected character reacts to estimated tempo and energy.

The main settings window can be hidden to the system tray while the transparent, always-on-top character window keeps running. Quit from the tray menu to stop the app completely. Refresh the app list while an app has an audio session, then select it to capture only that process tree. Selecting Chrome or Opera captures audio from that browser process tree, not an individual tab. The all-desktop-audio option captures the selected speaker's complete mix.

Drag the desktop character with the mouse to place it anywhere on the screen. Its dropped position is saved between launches.

Per-app capture uses the Windows process-loopback API and requires Windows 10 build 20348 or newer.

Standing character artwork is loaded from `assets/characters/<Character>.webp`. Use **Add / change artwork…** to override a preset with PNG, animated GIF, JPG/JPEG, or WebP art; **Use preset** restores the bundled image. Custom art is saved per character in `%APPDATA%\Chiikawa Desktop Club\settings.json` and appears in both the preview and desktop pet. Ensure you have permission to distribute the bundled artwork.

Enable **Character outline** in settings to add a silhouette border. Use the color swatch to choose its color; the outline is applied to still images and each frame of animated GIFs.

Choose **Bottom stroll** to keep the character in the lower part of the screen, or **Whole desktop** to let it roam from top to bottom. The choice is saved and restored when the app starts.

Optional motion GIFs in `assets/characters` are auto-selected by filename: `<Character>-walking.gif`, `<Character>-idle.gif`, `<Character>-<mood>.gif`, and `<Character>-<mood>-idle.gif` (mood names: `dreamy`, `chill`, `upbeat`, `energetic`). Missing animations fall back to the character's selected or bundled standing artwork.

## Run on Windows

Python 3.10 or newer is required. Tkinter must be included with the Python installation.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Microphone access may require Windows privacy permission. Local audio formats are decoded by libsndfile; MP3 support depends on the libsndfile build.