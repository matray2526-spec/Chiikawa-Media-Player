from __future__ import annotations

import json
import math
import os
import queue
import random
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
import ctypes
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox
import tkinter as tk
from typing import Any

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

if IS_WINDOWS:
    # The Windows audio backends share COM on the UI thread.
    setattr(sys, "coinit_flags", 0)

import numpy as np
import pystray
import psutil
import soundcard as sc
import sounddevice as sd
import soundfile as sf
import ttkbootstrap as tb
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageOps, ImageSequence, ImageTk

if IS_WINDOWS:
    from pycaw.pycaw import AudioUtilities
    from process_loopback import capture_process_audio
else:
    AudioUtilities = None
    capture_process_audio = None


APP_NAME = "Chiikawa Desktop Club"
BACKGROUND = "#f5f7f3"
INK = "#202a28"
GREEN = "#187e69"
MUTED = "#77827c"
TRANSPARENT = "#ff2600"
PRESET_CHARACTERS = (
    "Chiikawa",
    "Hachiware",
    "Usagi",
    "Shisa",
    "Rakko",
    "Momonga",
    "KuriManju",
    "Furuhonya",
)
CHARACTERS = (*PRESET_CHARACTERS, "Custom")
ROAMING_AREAS = ("Bottom stroll", "Whole desktop")
CHARACTER_ART_DIR = Path(__file__).resolve().parent / "assets" / "characters"
MOOD_TEXT = {
    "dreamy": "Dreamy",
    "chill": "Chill",
    "upbeat": "Upbeat",
    "energetic": "Energetic",
}
CUSTOM_ANIMATION_SECTIONS = (
    ("Movement", (("standing", "Standing fallback"), ("walking", "Walking"), ("idle", "Idle"))),
    ("Dreamy", (("dreamy", "Walking / active"), ("dreamy-idle", "Resting"))),
    ("Chill", (("chill", "Walking / active"), ("chill-idle", "Resting"))),
    ("Upbeat", (("upbeat", "Walking / active"), ("upbeat-idle", "Resting"))),
    ("Energetic", (("energetic", "Walking / active"), ("energetic-idle", "Resting"))),
)
CUSTOM_ANIMATION_STATES = tuple(state for _section, states in CUSTOM_ANIMATION_SECTIONS for state in states)


def settings_path() -> Path:
    if IS_MACOS:
        root = Path.home() / "Library" / "Application Support" / "Chiikawa Desktop Club"
    else:
        root = Path(os.environ.get("APPDATA", Path.home())) / "Chiikawa Desktop Club"
    root.mkdir(parents=True, exist_ok=True)
    return root / "settings.json"


def outlined_image(image: Image.Image, enabled: bool, color: str) -> Image.Image:
    rgba = image.convert("RGBA")
    if not enabled:
        return rgba
    alpha = rgba.getchannel("A")
    expanded_alpha = alpha.filter(ImageFilter.MaxFilter(7))
    outline_alpha = ImageChops.subtract(expanded_alpha, alpha)
    outline_layer = Image.new("RGBA", rgba.size, color)
    outline_layer.putalpha(outline_alpha)
    return Image.alpha_composite(outline_layer, rgba)


def matte_color_key_edges(image: Image.Image, key_color: str) -> Image.Image:
    rgba = np.asarray(image.convert("RGBA")).copy()
    alpha = rgba[:, :, 3]
    faint_alpha = (alpha > 0) & (alpha <= 8)
    rgba[faint_alpha, :3] = ImageColor.getrgb(key_color)
    rgba[faint_alpha, 3] = 0
    antialiased_edges = (alpha > 8) & (alpha < 255)
    rgba[antialiased_edges, 3] = 255
    return Image.fromarray(rgba, "RGBA")


class BeatAnalyzer:
    def __init__(self) -> None:
        self.reset(44100)

    def reset(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.sample_count = 0
        self.previous_bass = 0.0
        self.previous_spectrum: np.ndarray | None = None
        self.last_beat = -10.0
        self.last_spectral_onset = -1.0
        self.last_signal_time: float | None = None
        self.beats: deque[float] = deque(maxlen=9)
        self.flux_history: deque[float] = deque(maxlen=48)
        self.spectral_flux_history: deque[float] = deque(maxlen=48)
        self.onset_times: deque[float] = deque()
        self.brightness_history: deque[tuple[float, float]] = deque()
        self.energy_history: deque[tuple[float, float, int]] = deque()
        self.bpm: float | None = None
        self.brightness = 0.0
        self.onset_density = 0.0

    def process(self, samples: np.ndarray) -> tuple[float | None, float, str | None]:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.size < 256:
            return self.bpm, 0.0, None

        audio = np.nan_to_num(audio)
        rms = float(np.sqrt(np.mean(audio * audio)))
        window = np.hanning(audio.size)
        spectrum = np.abs(np.fft.rfft(audio * window)) / audio.size
        frequencies = np.fft.rfftfreq(audio.size, 1 / self.sample_rate)
        bass_band = spectrum[(frequencies >= 45) & (frequencies <= 180)]
        bass = float(np.sqrt(np.mean(bass_band * bass_band))) if bass_band.size else 0.0
        onset = max(0.0, bass - self.previous_bass)
        now = self.sample_count / self.sample_rate
        if rms >= 0.0035:
            self.last_signal_time = now
        elif self.last_signal_time is not None and now - self.last_signal_time > 3.0:
            self.bpm = None
            self.beats.clear()
            self.onset_times.clear()
            self.onset_density = 0.0

        bass_threshold = max(0.0006, float(np.mean(self.flux_history)) * 1.75 if self.flux_history else 0.0006)
        if onset > bass_threshold and bass > 0.0015 and now - self.last_beat > 0.26:
            self.last_beat = now
            self.beats.append(now)
            if len(self.beats) >= 4:
                intervals = np.diff(np.asarray(self.beats))
                intervals = intervals[(intervals > 0.28) & (intervals < 3.0)]
                if intervals.size >= 3:
                    estimate = 60.0 / float(np.median(intervals))
                    while estimate > 180:
                        estimate /= 2
                    self.bpm = estimate

        power = spectrum * spectrum
        high_band = (frequencies >= 2000) & (frequencies <= 8000)
        high_band_power = float(np.sum(power[high_band]) * 2.0)
        window_rms = float(np.sqrt(np.mean(window * window)))
        brightness_sample = max(0.0, min(1.0, math.sqrt(high_band_power) / max(rms * window_rms, 1e-8)))
        self.brightness_history.append((now, brightness_sample))
        while self.brightness_history and now - self.brightness_history[0][0] > 1.2:
            self.brightness_history.popleft()
        self.brightness = sum(value for _timestamp, value in self.brightness_history) / len(self.brightness_history)

        if self.previous_spectrum is not None and self.previous_spectrum.shape == spectrum.shape:
            positive_flux = np.maximum(spectrum - self.previous_spectrum, 0.0)
            spectral_flux = float(np.sum(positive_flux) / max(float(np.sum(self.previous_spectrum)), 1e-8))
        else:
            spectral_flux = 0.0
        spectral_threshold = max(
            0.12,
            float(np.mean(self.spectral_flux_history)) * 1.5 if self.spectral_flux_history else 0.12,
        )
        if spectral_flux > spectral_threshold and rms > 0.002 and now - self.last_spectral_onset >= 0.12:
            self.onset_times.append(now)
            self.last_spectral_onset = now
        while self.onset_times and now - self.onset_times[0] > 3.0:
            self.onset_times.popleft()
        self.onset_density = len(self.onset_times) / 3.0

        self.flux_history.append(onset)
        self.spectral_flux_history.append(spectral_flux)
        self.previous_spectrum = spectrum
        self.previous_bass = bass
        self.sample_count += audio.size
        self.energy_history.append((now, float(np.mean(audio * audio)), audio.size))
        while self.energy_history and now - self.energy_history[0][0] > 1.0:
            self.energy_history.popleft()
        window_samples = sum(sample_count for _timestamp, _power, sample_count in self.energy_history)
        window_power = sum(power * sample_count for _timestamp, power, sample_count in self.energy_history) / max(window_samples, 1)
        smoothed_rms = math.sqrt(window_power)
        energy = max(0.0, min(1.0, (20 * math.log10(max(smoothed_rms, 1e-6)) + 48) / 36))
        mood = self.mood_for(self.bpm, energy, self.brightness, self.onset_density)
        return self.bpm, energy, mood

    @staticmethod
    def mood_for(
        bpm: float | None,
        energy: float,
        brightness: float,
        onset_density: float,
    ) -> str | None:
        if bpm is None:
            return None

        tempo_drive = max(0.0, min(1.0, (bpm - 60.0) / 100.0))
        density_drive = max(0.0, min(1.0, onset_density / 5.0))
        valence = max(0.0, min(1.0, brightness))
        arousal = 0.5 * tempo_drive + 0.4 * density_drive + 0.1 * energy

        if arousal >= 0.72:
            return "energetic"
        if valence >= 0.16 and (arousal >= 0.36 or bpm >= 100):
            return "upbeat"
        if bpm < 95 and energy < 0.32 and valence < 0.14 and onset_density < 1.2:
            return "dreamy"
        return "chill"


class AudioController:
    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object, int]] = queue.Queue(maxsize=8)
        self.analyzer = BeatAnalyzer()
        self.token = 0
        self.active = False
        self.input_stream: sd.InputStream | None = None
        self.output_stream: sd.OutputStream | None = None
        self.loop_stop = threading.Event()
        self.loop_thread: threading.Thread | None = None

    def _post(self, kind: str, value: object, token: int) -> None:
        if token != self.token:
            return
        try:
            self.events.put_nowait((kind, value, token))
        except queue.Full:
            try:
                self.events.get_nowait()
                self.events.put_nowait((kind, value, token))
            except queue.Empty:
                pass

    def _analyze(self, samples: np.ndarray, token: int) -> None:
        if token == self.token:
            self._post("analysis", self.analyzer.process(samples), token)

    def stop(self) -> None:
        self.token += 1
        self.active = False
        self.loop_stop.set()
        for stream in (self.input_stream, self.output_stream):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except (sd.PortAudioError, RuntimeError):
                    pass
        self.input_stream = None
        self.output_stream = None
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=0.5)
        self.loop_thread = None
        self.analyzer.reset(44100)

    def start_microphone(self, device_id: int) -> None:
        self.stop()
        token = self.token
        device = sd.query_devices(device_id)
        channels = min(2, int(device["max_input_channels"]))
        sample_rate = int(device["default_samplerate"])
        self.analyzer.reset(sample_rate)
        self.input_stream = sd.InputStream(
            device=device_id,
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=1024,
            callback=lambda data, _frames, _time, _status: self._analyze(data.copy(), token),
        )
        self.input_stream.start()
        self.active = True

    def start_system_audio(self, speaker_name: str) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("Desktop loopback capture is not available on macOS. Choose a virtual audio input such as BlackHole from the Input source.")
        self.stop()
        token = self.token
        self.loop_stop.clear()
        self.analyzer.reset(48000)
        self.active = True

        def capture() -> None:
            try:
                loopback = sc.get_microphone(id=speaker_name, include_loopback=True)
                while not self.loop_stop.is_set() and token == self.token:
                    chunk = loopback.record(numframes=2048, samplerate=48000, channels=2)
                    self._analyze(chunk, token)
            except Exception as error:
                self._post("error", str(error), token)

        self.loop_thread = threading.Thread(target=capture, name="system-audio-capture", daemon=True)
        self.loop_thread.start()

    def start_process_audio(self, process_id: int) -> None:
        if not IS_WINDOWS or capture_process_audio is None:
            raise RuntimeError("Per-app audio capture is currently available on Windows only.")
        self.stop()
        token = self.token
        self.loop_stop.clear()
        self.analyzer.reset(44100)
        self.active = True

        def capture() -> None:
            try:
                capture_process_audio(
                    process_id,
                    self.loop_stop,
                    lambda chunk: self._analyze(chunk, token),
                )
            except Exception as error:
                self._post("error", str(error), token)

        self.loop_thread = threading.Thread(target=capture, name=f"app-audio-{process_id}", daemon=True)
        self.loop_thread.start()

    def load_audio_file(self, path: str) -> None:
        self.stop()
        token = self.token

        def load() -> None:
            try:
                samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
                if samples.shape[1] > 2:
                    samples = samples[:, :2]
                self._post("file-loaded", (samples, int(sample_rate), path), token)
            except Exception as error:
                self._post("error", f"Could not open that audio file: {error}", token)

        threading.Thread(target=load, name="audio-file-loader", daemon=True).start()

    def play_loaded_file(self, samples: np.ndarray, sample_rate: int, path: str) -> None:
        token = self.token
        self.analyzer.reset(sample_rate)
        self.active = True
        cursor = 0
        reported_end = False

        def play(data: np.ndarray, frames: int, _time: object, _status: object) -> None:
            nonlocal cursor, reported_end
            end = min(cursor + frames, len(samples))
            count = end - cursor
            data.fill(0)
            if count > 0:
                data[:count] = samples[cursor:end]
                self._analyze(samples[cursor:end], token)
                cursor = end
            if cursor >= len(samples) and not reported_end:
                reported_end = True
                self._post("finished", None, token)
                raise sd.CallbackStop

        self.output_stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=samples.shape[1],
            dtype="float32",
            blocksize=1024,
            callback=play,
        )
        self.output_stream.start()
        self._post("started", Path(path).name, token)


class PetWindow:
    def __init__(self, root: tk.Misc, image_changed: Callable[[str], None]) -> None:
        self.root = root
        self.image_changed = image_changed
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.configure(cursor="openhand" if IS_MACOS else "fleur")
        except tk.TclError:
            pass
        if IS_MACOS:
            self.window.configure(background="systemTransparent")
            self.window.attributes("-transparent", True)
            canvas_background = "systemTransparent"
        else:
            self.window.configure(background=TRANSPARENT)
            try:
                self.window.attributes("-transparentcolor", TRANSPARENT)
            except tk.TclError:
                pass
            canvas_background = TRANSPARENT
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        self.window.geometry(f"180x190+{max(0, screen_width - 240)}+{max(0, screen_height - 230)}")
        self.canvas = tk.Canvas(self.window, width=180, height=190, bg=canvas_background, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.image_item = self.canvas.create_image(90, 105, anchor="center")
        self.placeholder_background = self.canvas.create_oval(48, 57, 132, 141, fill="#fff8e8", outline="#ffffff", width=3)
        self.placeholder = self.canvas.create_text(90, 91, text="+", font=("Segoe UI", 26, "bold"), fill=GREEN)
        self.placeholder_caption = self.canvas.create_text(90, 124, text="ADD ARTWORK", font=("Segoe UI", 7, "bold"), fill=MUTED)
        self.photo: ImageTk.PhotoImage | None = None
        self.visible = True
        self._drag_offset: tuple[int, int] | None = None
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.window.bind("<Button-3>", lambda _event: self.image_changed("menu"))
        self.ensure_topmost()

    def ensure_topmost(self) -> None:
        self.window.attributes("-topmost", True)
        self.window.update_idletasks()
        if os.name == "nt":
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            get_ancestor = user32.GetAncestor
            get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
            get_ancestor.restype = wintypes.HWND
            hwnd = get_ancestor(wintypes.HWND(self.window.winfo_id()), 2) or self.window.winfo_id()
            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = (
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            )
            set_window_pos.restype = wintypes.BOOL
            set_window_pos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-1),
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0010 | 0x0040,
            )

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_offset = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())
        self.canvas.grab_set()
        self.image_changed("drag-start")

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag_offset:
            x = event.x_root - self._drag_offset[0]
            y = event.y_root - self._drag_offset[1]
            self.window.geometry(f"+{x}+{y}")
            self.image_changed("drag")

    def _drag_end(self, _event: tk.Event) -> None:
        self._drag_offset = None
        try:
            self.canvas.grab_release()
        except tk.TclError:
            pass
        self.window.update_idletasks()
        self.image_changed("drag-end")

    def set_photo(self, photo: ImageTk.PhotoImage | None) -> None:
        self.photo = photo
        if photo is None:
            self.canvas.itemconfigure(self.image_item, image="")
            self.canvas.itemconfigure(self.placeholder_background, state="normal")
            self.canvas.itemconfigure(self.placeholder, state="normal")
            self.canvas.itemconfigure(self.placeholder_caption, state="normal")
        else:
            self.canvas.itemconfigure(self.image_item, image=photo)
            self.canvas.itemconfigure(self.placeholder_background, state="hidden")
            self.canvas.itemconfigure(self.placeholder, state="hidden")
            self.canvas.itemconfigure(self.placeholder_caption, state="hidden")

    def set_placeholder(self, character_name: str) -> None:
        self.canvas.itemconfigure(self.placeholder_background, state="normal")
        self.canvas.itemconfigure(self.placeholder, text="+", state="normal")
        self.canvas.itemconfigure(self.placeholder_caption, text=f"{character_name.upper()} ART", state="normal")
        self.canvas.itemconfigure(self.image_item, image="")
        self.photo = None

    def move_pet(self, x: float, y: float) -> None:
        width, height = self.window.winfo_width(), self.window.winfo_height()
        self.window.geometry(f"{width}x{height}+{int(x)}+{int(y)}")

    def close(self) -> None:
        self.window.destroy()


class DesktopCompanion:
    def __init__(self) -> None:
        self.root = tb.Window(themename="flatly")
        self.root.title(APP_NAME)
        self.root.geometry("1050x690")
        self.root.minsize(760, 590)
        self.root.configure(background=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_settings)
        self.style = self.root.style
        self.style.configure("TLabel", font=("Segoe UI", 10))
        self.style.configure("Title.TLabel", font=("Trebuchet MS", 22, "bold"), foreground=INK)
        self.style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"), foreground=INK)
        self.style.configure("Muted.TLabel", font=("Segoe UI", 9), foreground=MUTED)

        self.settings_file = settings_path()
        self.settings = self._load_settings()
        self.audio = AudioController()
        self.character_var = tk.StringVar(value=self.settings.get("character", "Chiikawa"))
        self.behavior_var = tk.StringVar(value=self.settings.get("behavior", "Follow the beat"))
        saved_roaming_area = self.settings.get("roaming_area", "Bottom stroll")
        if saved_roaming_area not in ROAMING_AREAS:
            saved_roaming_area = "Bottom stroll"
        self.roaming_area_var = tk.StringVar(value=saved_roaming_area)
        self.outline_enabled = tk.BooleanVar(value=bool(self.settings.get("outline_enabled", False)))
        saved_outline_color = self.settings.get("outline_color", "#ffffff")
        try:
            ImageColor.getrgb(str(saved_outline_color))
            self.outline_color = str(saved_outline_color)
        except ValueError:
            self.outline_color = "#ffffff"
        self.source_var = tk.StringVar(value="file")
        self.status_var = tk.StringVar(value="Ready when you are")
        self.bpm_var = tk.StringVar(value="--")
        self.mood_var = tk.StringVar(value="Waiting for a song")
        self.energy_var = tk.DoubleVar(value=0)
        self.audio_path = ""
        self.current_mood: str | None = None
        self.current_bpm: float | None = None
        self.energy = 0.0
        self.gif_frames: list[Image.Image] = []
        self.gif_durations: list[int] = []
        self.gif_index = 0
        self.gif_last_tick = 0.0
        self.current_photo: ImageTk.PhotoImage | None = None
        self.pet_photo: ImageTk.PhotoImage | None = None
        self.loaded_art_path: Path | None = None
        self.listen_started = 0.0
        self.pet_speed_x = -24.0
        self.pet_speed_y = -8.0
        self.pet_facing_left = True
        self.pet_x = float(max(0, self.root.winfo_screenwidth() - 240))
        self.pet_y = float(max(0, self.root.winfo_screenheight() - 230))
        saved_position = self.settings.get("pet_position")
        if (
            isinstance(saved_position, (list, tuple))
            and len(saved_position) == 2
            and all(isinstance(value, (int, float)) and math.isfinite(value) for value in saved_position)
        ):
            min_y, max_y = self._roaming_vertical_bounds()
            self.pet_x = float(max(0, min(self.root.winfo_screenwidth() - 180, saved_position[0])))
            self.pet_y = float(max(min_y, min(max_y, saved_position[1])))
        self.last_animation_tick = time.monotonic()
        self.motion_state = "walking"
        self.motion_state_until = self.last_animation_tick + random.uniform(18.0, 36.0)
        self.next_turn_at = self.last_animation_tick + self._next_turn_delay(False)
        self.motion_music_active = False
        self.audio_devices_loaded = False
        self.is_dragging = False
        self.demo_active = False
        self.tray_icon: Any = None
        self.pet_window = PetWindow(self.root, self._pet_menu_action)
        self.pet_window.move_pet(self.pet_x, self.pet_y)
        self._build_interface()
        self._build_settings_window()
        self._load_character_art()
        self._start_tray_icon()
        self.root.after(80, self._poll_audio)
        self.root.after(80, self._animate)

    def _load_settings(self) -> dict[str, object]:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        self.settings["character"] = self.character_var.get()
        self.settings["behavior"] = self.behavior_var.get()
        self.settings["roaming_area"] = self.roaming_area_var.get()
        self.settings["outline_enabled"] = self.outline_enabled.get()
        self.settings["outline_color"] = self.outline_color
        self.settings["pet_position"] = [round(self.pet_x), round(self.pet_y)]
        self.settings_file.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def _build_interface(self) -> None:
        shell = tb.Frame(self.root, padding=(26, 18, 26, 12))
        shell.pack(fill="both", expand=True)

        header = tb.Frame(shell)
        header.pack(fill="x", pady=(0, 18))
        brand = tb.Frame(header)
        brand.pack(side="left")
        tb.Label(brand, text="c.", font=("Trebuchet MS", 18, "bold"), foreground="#3a3424", background="#f3c75f", padding=(9, 5)).pack(side="left", padx=(0, 10))
        title_stack = tb.Frame(brand)
        title_stack.pack(side="left")
        tb.Label(title_stack, text="chiikawa", font=("Trebuchet MS", 14, "bold"), foreground=INK).pack(anchor="w")
        tb.Label(title_stack, text="DESKTOP CLUB", font=("Segoe UI", 8, "bold"), foreground=MUTED).pack(anchor="w")
        tb.Label(header, textvariable=self.status_var, style="Muted.TLabel").pack(side="right", padx=(8, 0))
        self.status_dot = tb.Label(header, text="●", foreground="#aab5ae")
        self.status_dot.pack(side="right")
        self.settings_button = tb.Button(header, text="Settings", bootstyle="secondary-outline", command=self.toggle_settings_window)
        self.settings_button.pack(side="right", padx=(0, 16))

        intro = tb.Frame(shell)
        intro.pack(fill="x", pady=(0, 16))
        tb.Label(intro, text="A friend for the soundtrack.", style="Title.TLabel").pack(side="left")
        self.run_button = tb.Button(intro, text="▶  Start listening", bootstyle="success", command=self.toggle_listening)
        self.run_button.pack(side="right", ipadx=8, ipady=4)

        content = tb.Frame(shell)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=3, minsize=430)
        content.columnconfigure(1, weight=2, minsize=310)
        content.rowconfigure(0, weight=1)

        preview = tb.Frame(content, padding=14, bootstyle="light")
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self._build_preview(preview)

        controls = tb.Frame(content, padding=16, bootstyle="light")
        controls.grid(row=0, column=1, sticky="nsew")
        controls.columnconfigure(0, weight=1)
        self._build_controls(controls)

        footer = tb.Frame(shell)
        footer.pack(fill="x", pady=(12, 0))
        tb.Label(footer, text="Audio is analyzed locally. Closing settings keeps your companion in the tray.", style="Muted.TLabel").pack(side="left")

    def _build_preview(self, parent: tb.Frame) -> None:
        bar = tb.Frame(parent)
        bar.pack(fill="x", pady=(0, 10))
        tb.Label(bar, text="PET PREVIEW", font=("Segoe UI", 9, "bold"), foreground=MUTED).pack(side="left")
        self.preview_mood = tb.Label(bar, textvariable=self.mood_var, font=("Segoe UI", 9, "bold"), foreground=GREEN)
        self.preview_mood.pack(side="right")
        self.scene = tk.Canvas(parent, height=390, background="#dcebe1", highlightthickness=0)
        self.scene.pack(fill="both", expand=True)
        self.scene.create_oval(380, 34, 440, 94, fill="#f4ce78", outline="")
        self.scene.create_rectangle(58, 62, 222, 210, fill="#edf4ef", outline="#ffffff", width=3)
        self.scene.create_line(140, 62, 140, 210, fill="#ffffff", width=3)
        self.scene.create_line(58, 137, 222, 137, fill="#ffffff", width=3)
        self.scene.create_rectangle(0, 274, 900, 390, fill="#b6d5c4", outline="")
        self.scene.create_line(0, 274, 900, 274, fill="#8fb8a1", width=2)
        self.scene.create_text(320, 150, text="a little music break", fill="#72877a", font=("Trebuchet MS", 16, "bold"))
        self.scene.create_text(320, 178, text="your desktop, but a little cozier", fill="#81988a", font=("Segoe UI", 10))
        self.scene_art_backing = self.scene.create_oval(222, 270, 312, 360, fill="#fff8e8", outline="#ffffff", width=3)
        self.scene_sprite = self.scene.create_text(267, 301, text="+", font=("Segoe UI", 27, "bold"), fill=GREEN)
        self.scene_art_name = self.scene.create_text(267, 335, text="ADD CHIIKAWA ART", font=("Segoe UI", 8, "bold"), fill=MUTED)
        self.scene_image = self.scene.create_image(267, 315)
        lower = tb.Frame(parent, padding=(2, 13, 2, 2))
        lower.pack(fill="x")
        tb.Label(lower, text="TEMPO", font=("Segoe UI", 8, "bold"), foreground=MUTED).pack(side="left")
        tb.Label(lower, textvariable=self.bpm_var, font=("Trebuchet MS", 24, "bold"), foreground=INK).pack(side="left", padx=(8, 3))
        tb.Label(lower, text="BPM", font=("Segoe UI", 9, "bold"), foreground=MUTED).pack(side="left", pady=(6, 0))
        tb.Label(lower, text="ENERGY", font=("Segoe UI", 8, "bold"), foreground=MUTED).pack(side="left", padx=(28, 8))
        self.energy_bar = tb.Progressbar(lower, variable=self.energy_var, maximum=100, length=130, bootstyle="warning-striped")
        self.energy_bar.pack(side="left", fill="x", expand=True, pady=12)

    def _build_controls(self, parent: tb.Frame) -> None:
        tb.Label(parent, text="01  Choose a sound", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        tb.Label(parent, text="One source at a time. No audio pile-up.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 10))
        self.source_tabs = tb.Frame(parent)
        self.source_tabs.grid(row=2, column=0, sticky="ew")
        source_choices = [("file", "Audio file")]
        if IS_WINDOWS:
            source_choices.insert(1, ("system", "Desktop audio"))
            source_choices.append(("mic", "Mic"))
        else:
            source_choices.append(("mic", "Input"))
        for value, label in source_choices:
            tb.Radiobutton(self.source_tabs, text=label, variable=self.source_var, value=value, bootstyle="success-toolbutton", command=self.change_source).pack(side="left", expand=True, fill="x")

        self.source_panel = tb.Frame(parent, padding=(0, 10, 0, 4))
        self.source_panel.grid(row=3, column=0, sticky="ew")
        self.file_panel = tb.Frame(self.source_panel)
        self.file_button = tb.Button(self.file_panel, text="Choose an audio file…", bootstyle="secondary-outline", command=self.choose_audio)
        self.file_button.pack(fill="x")
        self.file_name = tb.Label(self.file_panel, text="MP3, WAV, FLAC and more", style="Muted.TLabel", wraplength=300)
        self.file_name.pack(anchor="w", pady=(6, 0))

        self.device_panel = tb.Frame(self.source_panel)
        device_header = tb.Frame(self.device_panel)
        device_header.pack(fill="x")
        self.device_title = tb.Label(device_header, text="INPUT DEVICE", font=("Segoe UI", 8, "bold"), foreground=MUTED)
        self.device_title.pack(side="left")
        self.refresh_devices_button = tb.Button(device_header, text="Refresh", bootstyle="link", command=self.refresh_audio_targets)
        self.refresh_devices_button.pack(side="right")
        self.device_var = tk.StringVar()
        self.device_picker = tb.Combobox(self.device_panel, textvariable=self.device_var, state="readonly")
        self.device_picker.pack(fill="x", pady=(5, 0))
        self.source_description = tb.Label(parent, text="Select a local track; it will play through this app and be analyzed live.", style="Muted.TLabel", wraplength=330)
        self.source_description.grid(row=4, column=0, sticky="w", pady=(0, 9))
        tb.Separator(parent).grid(row=5, column=0, sticky="ew", pady=(3, 12))
        self.demo_button = tb.Button(parent, text="Try a demo beat", bootstyle="warning-outline", command=self.toggle_demo)
        self.demo_button.grid(row=6, column=0, sticky="w")
        source_hint = (
            "Choose one app, microphone, or output mix to track."
            if IS_WINDOWS
            else "Choose a microphone or virtual input such as BlackHole."
        )
        tb.Label(parent, text=source_hint, style="Muted.TLabel", wraplength=330).grid(row=7, column=0, sticky="w", pady=(11, 0))
        self.device_picker.bind("<<ComboboxSelected>>", self.change_source)
        self.change_source()

    def _build_settings_window(self) -> None:
        self.settings_window = tb.Toplevel(self.root)
        self.settings_window.title("Character Settings")
        self.settings_window.geometry("360x430")
        self.settings_window.resizable(False, False)
        self.settings_window.transient(self.root)
        self.settings_window.protocol("WM_DELETE_WINDOW", self.hide_settings_window)
        self.settings_window.withdraw()

        panel = tb.Frame(self.settings_window, padding=20)
        panel.pack(fill="both", expand=True)
        tb.Label(panel, text="Character settings", style="Section.TLabel").pack(anchor="w")
        tb.Label(panel, text="Customize your desktop companion.", style="Muted.TLabel").pack(anchor="w", pady=(3, 16))

        tb.Label(panel, text="CHARACTER", font=("Segoe UI", 8, "bold"), foreground=MUTED).pack(anchor="w")
        self.character_picker = tb.Combobox(panel, textvariable=self.character_var, values=list(CHARACTERS), state="readonly")
        self.character_picker.pack(fill="x", pady=(5, 8))
        self.character_picker.bind("<<ComboboxSelected>>", self.change_character)

        art_controls = tb.Frame(panel)
        art_controls.pack(fill="x", pady=(0, 14))
        tb.Button(art_controls, text="Add / change artwork…", bootstyle="secondary-outline", command=self.choose_character_art).pack(side="left", fill="x", expand=True)
        tb.Button(art_controls, text="Use preset", bootstyle="secondary-outline", command=self.remove_character_art).pack(side="left", padx=(6, 0))
        self.custom_animation_button = tb.Button(
            panel,
            text="Custom animations…",
            bootstyle="success-outline",
            command=self.open_custom_animation_editor,
            state="normal" if self.character_var.get() == "Custom" else "disabled",
        )
        self.custom_animation_button.pack(fill="x", pady=(0, 14))

        tb.Label(panel, text="BEHAVIOR", font=("Segoe UI", 8, "bold"), foreground=MUTED).pack(anchor="w")
        self.behavior_picker = tb.Combobox(panel, textvariable=self.behavior_var, values=("Follow the beat", "Stay still"), state="readonly")
        self.behavior_picker.pack(fill="x", pady=(5, 13))
        self.behavior_picker.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        tb.Label(panel, text="ROAMING AREA", font=("Segoe UI", 8, "bold"), foreground=MUTED).pack(anchor="w")
        self.roaming_picker = tb.Combobox(panel, textvariable=self.roaming_area_var, values=ROAMING_AREAS, state="readonly")
        self.roaming_picker.pack(fill="x", pady=(5, 13))
        self.roaming_picker.bind("<<ComboboxSelected>>", self.change_roaming_area)

        outline_controls = tb.Frame(panel)
        outline_controls.pack(fill="x", pady=(0, 16))
        tb.Checkbutton(
            outline_controls,
            text="Character outline",
            variable=self.outline_enabled,
            bootstyle="success-round-toggle",
            command=self.update_outline,
        ).pack(side="left")
        tb.Label(outline_controls, text="Color", style="Muted.TLabel").pack(side="right", padx=(7, 0))
        self.outline_swatch = tk.Button(
            outline_controls,
            text="  ",
            background=self.outline_color,
            activebackground=self.outline_color,
            relief="solid",
            borderwidth=1,
            command=self.choose_outline_color,
        )
        self.outline_swatch.pack(side="right", ipadx=3, ipady=2)

        tb.Separator(panel).pack(fill="x", pady=(0, 12))
        tb.Button(panel, text="Reset settings", bootstyle="secondary-outline", command=self.reset_settings).pack(anchor="e")

    def open_custom_animation_editor(self) -> None:
        if self.character_var.get() != "Custom":
            return
        if getattr(self, "custom_animation_window", None) is not None:
            try:
                if self.custom_animation_window.winfo_exists():
                    self.custom_animation_window.deiconify()
                    self.custom_animation_window.lift()
                    return
            except tk.TclError:
                pass

        window = tb.Toplevel(self.settings_window)
        self.custom_animation_window = window
        window.title("Custom Character Animations")
        window.geometry("560x640")
        window.minsize(520, 520)
        window.transient(self.settings_window)
        window.protocol("WM_DELETE_WINDOW", window.withdraw)

        panel = tb.Frame(window, padding=18)
        panel.pack(fill="both", expand=True)
        tb.Label(panel, text="Custom character animations", style="Section.TLabel").pack(anchor="w")
        tb.Label(
            panel,
            text="Choose a picture or GIF for each state. Missing files fall back to the standing image.",
            style="Muted.TLabel",
            wraplength=510,
        ).pack(anchor="w", pady=(4, 12))

        rows = tb.Frame(panel)
        rows.pack(fill="both", expand=True)
        self.custom_animation_labels: dict[str, tb.Label] = {}
        row = 0
        for section, states in CUSTOM_ANIMATION_SECTIONS:
            tb.Label(rows, text=section.upper(), font=("Segoe UI", 8, "bold"), foreground=MUTED).grid(
                row=row,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(8, 4),
            )
            row += 1
            for state, label in states:
                tb.Label(rows, text=label, width=18).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
                path_label = tb.Label(rows, text="Not set", style="Muted.TLabel", width=24, anchor="w")
                path_label.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=3)
                self.custom_animation_labels[state] = path_label
                tb.Button(
                    rows,
                    text="Choose…",
                    bootstyle="secondary-outline",
                    command=lambda animation_state=state: self.choose_custom_animation(animation_state),
                ).grid(row=row, column=2, sticky="e", pady=3)
                tb.Button(
                    rows,
                    text="×",
                    width=3,
                    bootstyle="secondary-outline",
                    command=lambda animation_state=state: self.clear_custom_animation(animation_state),
                ).grid(row=row, column=3, sticky="e", padx=(4, 0), pady=3)
                row += 1
        rows.columnconfigure(1, weight=1)
        self._refresh_custom_animation_labels()

    def _refresh_custom_animation_labels(self) -> None:
        animations = self.settings.get("custom_animations", {})
        if not isinstance(animations, dict):
            animations = {}
        for state, label in getattr(self, "custom_animation_labels", {}).items():
            path = animations.get(state)
            label.configure(text=Path(path).name if path else "Not set")

    def choose_custom_animation(self, state: str) -> None:
        path = filedialog.askopenfilename(
            parent=self.custom_animation_window,
            title=f"Choose {dict(CUSTOM_ANIMATION_STATES)[state]} artwork",
            filetypes=(("Pictures and GIFs", "*.png *.gif *.jpg *.jpeg *.webp"), ("All files", "*.*")),
        )
        if not path:
            return
        animations = self.settings.setdefault("custom_animations", {})
        if not isinstance(animations, dict):
            animations = {}
            self.settings["custom_animations"] = animations
        animations[state] = path
        self._save_settings()
        self._refresh_custom_animation_labels()
        if self.character_var.get() == "Custom":
            self.loaded_art_path = None
            self._load_character_art()

    def clear_custom_animation(self, state: str) -> None:
        animations = self.settings.get("custom_animations", {})
        if isinstance(animations, dict):
            animations.pop(state, None)
        self._save_settings()
        self._refresh_custom_animation_labels()
        if self.character_var.get() == "Custom":
            self.loaded_art_path = None
            self._load_character_art()

    def toggle_settings_window(self) -> None:
        if self.settings_window.state() == "normal":
            self.hide_settings_window()
            return
        self.root.update_idletasks()
        width, height = 360, 430
        x = min(self.settings_button.winfo_rootx(), self.root.winfo_screenwidth() - width - 12)
        y = self.settings_button.winfo_rooty() + self.settings_button.winfo_height() + 6
        if y + height > self.root.winfo_screenheight():
            y = max(0, self.settings_button.winfo_rooty() - height - 6)
        self.settings_window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.settings_window.deiconify()
        self.settings_window.lift()
        self.settings_window.focus_force()

    def hide_settings_window(self) -> None:
        self._save_settings()
        self.settings_window.withdraw()
        if getattr(self, "custom_animation_window", None) is not None:
            self.custom_animation_window.withdraw()

    def _refresh_audio_devices(self) -> None:
        self.input_devices: dict[str, int] = {}
        try:
            for index, device in enumerate(sd.query_devices()):
                name = str(device["name"])
                if int(device["max_input_channels"]) < 1 or "mapper" in name.lower() or "primary sound" in name.lower():
                    continue
                label = f"{name}  ·  {index}"
                self.input_devices[label] = index
        except Exception:
            self.input_devices = {}

        try:
            if not IS_WINDOWS:
                raise RuntimeError("Speaker loopback is Windows-only in this app.")
            speakers = [speaker.name for speaker in sc.all_speakers()]
            default_speaker = sc.default_speaker().name
            self.speakers = list(dict.fromkeys(speakers))
            self.default_speaker = default_speaker if default_speaker in self.speakers else (self.speakers[0] if self.speakers else "")
        except Exception:
            self.speakers = []
            self.default_speaker = ""

        self.app_targets: dict[str, tuple[str, int | str]] = self._discover_app_targets()
        self.system_targets: dict[str, str] = {}
        if IS_WINDOWS:
            for speaker in self.speakers:
                label = f"All desktop audio · {speaker}"
                self.system_targets[label] = speaker
                self.app_targets[label] = ("system", speaker)
        self.default_system_target = next(
            (label for label, speaker in self.system_targets.items() if speaker == self.default_speaker),
            next(iter(self.system_targets), ""),
        )
        self.audio_devices_loaded = True

    def _discover_app_targets(self) -> dict[str, tuple[str, int | str]]:
        if not IS_WINDOWS or AudioUtilities is None:
            return {}
        processes: dict[int, tuple[str, str]] = {}
        try:
            sessions = AudioUtilities.GetAllSessions()
        except Exception:
            return {}

        for session in sessions:
            try:
                process_id = int(session.ProcessId)
                if process_id <= 0 or process_id == os.getpid() or int(session.State) == 2:
                    continue
                process = session.Process
                if process is None:
                    continue
                root_process = process
                while True:
                    parent = root_process.parent()
                    if parent is None or parent.name().casefold() != root_process.name().casefold():
                        break
                    root_process = parent
                display_name = str(session.DisplayName or "").strip()
                if not display_name or display_name.startswith("@%"):
                    display_name = Path(process.name()).stem.title()
                processes.setdefault(root_process.pid, (display_name, root_process.name()))
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, ValueError):
                continue

        labels: dict[str, tuple[str, int | str]] = {}
        name_counts: dict[str, int] = {}
        for display_name, _process_name in processes.values():
            name_counts[display_name] = name_counts.get(display_name, 0) + 1
        for process_id, (display_name, process_name) in processes.items():
            suffix = f" · PID {process_id}" if name_counts[display_name] > 1 else ""
            label = f"{display_name} ({Path(process_name).stem}){suffix}"
            labels[label] = ("process", process_id)
        return labels

    def refresh_audio_targets(self) -> None:
        self._refresh_audio_devices()
        self.change_source()

    def change_source(self, _event: tk.Event | None = None) -> None:
        if self.demo_active:
            self.demo_active = False
            self.demo_button.configure(text="Try a demo beat")
        self.audio.stop()
        source = self.source_var.get()
        if source in {"system", "mic"} and not self.audio_devices_loaded:
            self.status_var.set("Loading audio sources…")
            self._refresh_audio_devices()
        self.file_panel.pack_forget()
        self.device_panel.pack_forget()
        if source == "file":
            self.file_panel.pack(fill="x")
            self.source_description.configure(text="Choose one local track. It plays through this app and is analyzed live.")
        elif source == "mic":
            self.device_panel.pack(fill="x")
            input_labels = list(self.input_devices)
            self.device_title.configure(text="AUDIO INPUT" if IS_MACOS else "MICROPHONE INPUT")
            self.refresh_devices_button.configure(text="Refresh inputs")
            self.device_picker.configure(values=input_labels)
            if self.device_var.get() not in self.input_devices:
                self.device_var.set(next(iter(self.input_devices), ""))
            if IS_MACOS:
                self.source_description.configure(text="Choose a microphone or virtual audio input such as BlackHole for desktop audio.")
            else:
                self.source_description.configure(text="Only this microphone is captured; desktop playback is not mixed in.")
        else:
            self.device_panel.pack(fill="x")
            self.device_title.configure(text="APP OR OUTPUT")
            self.refresh_devices_button.configure(text="Refresh apps")
            self.device_picker.configure(values=list(self.app_targets))
            if self.device_var.get() not in self.app_targets:
                self.device_var.set(self.default_system_target or next(iter(self.app_targets), ""))
            self.source_description.configure(text="App choices capture that app's process tree. Output choices include all audio on that speaker.")
        self._reset_readings()
        self.status_var.set("Ready when you are")
        self.run_button.configure(text="▶  Start listening", bootstyle="success")

    def choose_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an audio track",
            filetypes=(("Audio files", "*.mp3 *.wav *.flac *.ogg *.aiff *.aif"), ("All files", "*.*")),
        )
        if path:
            self.audio_path = path
            self.file_name.configure(text=Path(path).name)
            self.status_var.set("Track ready")

    def choose_character_art(self) -> None:
        path = filedialog.askopenfilename(
            title=f"Choose artwork for {self.character_var.get()}",
            filetypes=(("Character artwork", "*.png *.gif *.jpg *.jpeg *.webp"), ("All files", "*.*")),
        )
        if not path:
            return
        self.settings.setdefault("artwork", {})[self.character_var.get()] = path
        self._save_settings()
        self._load_character_art()

    def remove_character_art(self) -> None:
        artwork = self.settings.get("artwork", {})
        if isinstance(artwork, dict):
            artwork.pop(self.character_var.get(), None)
        self._save_settings()
        self._load_character_art()

    def change_character(self, _event: tk.Event | None = None) -> None:
        is_custom = self.character_var.get() == "Custom"
        self.custom_animation_button.configure(state="normal" if is_custom else "disabled")
        if not is_custom and getattr(self, "custom_animation_window", None) is not None:
            self.custom_animation_window.withdraw()
        self._save_settings()
        self._load_character_art()

    def _load_character_art(self) -> None:
        path = self._art_path_for_current_state()
        if path == self.loaded_art_path and self.gif_frames:
            return
        self.gif_frames = []
        self.gif_durations = []
        self.loaded_art_path = path
        if path:
            try:
                with Image.open(path) as image:
                    for frame in ImageSequence.Iterator(image):
                        copy = frame.convert("RGBA")
                        copy.thumbnail((142, 142), Image.Resampling.LANCZOS)
                        self.gif_frames.append(copy.copy())
                        self.gif_durations.append(max(60, int(frame.info.get("duration", 100))))
                self.gif_index = 0
                self._show_frame()
                return
            except (OSError, ValueError):
                messagebox.showwarning(APP_NAME, "That image could not be loaded.")
        self.current_photo = None
        self.scene.itemconfigure(self.scene_image, image="")
        self.scene.itemconfigure(self.scene_art_backing, state="normal")
        self.scene.itemconfigure(self.scene_sprite, text="+", state="normal")
        self.scene.itemconfigure(self.scene_art_name, text=f"ADD {self.character_var.get().upper()} ART", state="normal")
        self.pet_window.set_placeholder(self.character_var.get())

    def _art_path_for_current_state(self) -> Path | None:
        character = self.character_var.get()
        music_active = self._music_is_detected()
        candidates: list[Path] = []
        if character == "Custom":
            if music_active and self.current_mood:
                if self.motion_state == "resting":
                    custom_states = (f"{self.current_mood}-idle", "idle", self.current_mood, "walking", "standing")
                else:
                    custom_states = (self.current_mood, "walking", "standing", "idle")
            elif self.motion_state == "resting":
                custom_states = ("idle", "standing", "walking")
            else:
                custom_states = ("walking", "standing", "idle")

            custom_animations = self.settings.get("custom_animations", {})
            if isinstance(custom_animations, dict):
                for state in custom_states:
                    custom_path = custom_animations.get(state)
                    if custom_path and Path(custom_path).is_file():
                        return Path(custom_path)

        if music_active and self.current_mood:
            if self.motion_state == "resting":
                candidates.append(CHARACTER_ART_DIR / f"{character}-{self.current_mood}-idle.gif")
                candidates.append(CHARACTER_ART_DIR / f"{character}-idle.gif")
            candidates.append(CHARACTER_ART_DIR / f"{character}-{self.current_mood}.gif")
        elif self.motion_state == "resting":
            candidates.append(CHARACTER_ART_DIR / f"{character}-idle.gif")
        else:
            candidates.append(CHARACTER_ART_DIR / f"{character}-walking.gif")

        for candidate in candidates:
            if candidate.is_file():
                return candidate

        artwork = self.settings.get("artwork", {})
        custom_path = artwork.get(character) if isinstance(artwork, dict) else None
        if custom_path and Path(custom_path).is_file():
            return Path(custom_path)

        preset_path = CHARACTER_ART_DIR / f"{character}.webp"
        return preset_path if preset_path.is_file() else None

    def _music_is_detected(self) -> bool:
        return self.demo_active or (self.audio.active and self.current_mood is not None)

    def _show_frame(self) -> None:
        if not self.gif_frames:
            return
        frame = outlined_image(
            self.gif_frames[self.gif_index],
            self.outline_enabled.get(),
            self.outline_color,
        )
        self.current_photo = ImageTk.PhotoImage(frame)
        self.scene.itemconfigure(self.scene_image, image=self.current_photo)
        self.scene.itemconfigure(self.scene_art_backing, state="hidden")
        self.scene.itemconfigure(self.scene_sprite, state="hidden")
        self.scene.itemconfigure(self.scene_art_name, state="hidden")
        pet_frame = matte_color_key_edges(frame, TRANSPARENT)
        if self.pet_facing_left:
            pet_frame = ImageOps.mirror(pet_frame)
        self.pet_photo = ImageTk.PhotoImage(pet_frame)
        self.pet_window.set_photo(self.pet_photo)

    def update_outline(self) -> None:
        self._save_settings()
        self._show_frame()

    def choose_outline_color(self) -> None:
        _rgb, selected_color = colorchooser.askcolor(
            color=self.outline_color,
            title="Choose character outline color",
            parent=self.root,
        )
        if selected_color:
            self.outline_color = selected_color
            self.outline_swatch.configure(background=selected_color, activebackground=selected_color)
            self._save_settings()
            self._show_frame()

    def _set_mood(self, mood: str | None) -> None:
        mood_changed = mood != self.current_mood
        self.current_mood = mood
        if mood:
            self.mood_var.set(MOOD_TEXT[mood])
        else:
            self.mood_var.set("Waiting for a song")
        if mood_changed or mood is None:
            self._load_character_art()

    def _reset_readings(self) -> None:
        self.current_bpm = None
        self.energy = 0.0
        self.bpm_var.set("--")
        self.energy_var.set(0)
        self.status_dot.configure(foreground="#aab5ae")
        self._set_mood(None)

    def toggle_listening(self) -> None:
        if self.demo_active:
            self.demo_active = False
            self.demo_button.configure(text="Try a demo beat")
            self.run_button.configure(text="▶  Start listening", bootstyle="success")
            self.status_var.set("Ready when you are")
            self._reset_readings()
            return
        if self.audio.active:
            self.audio.stop()
            self.run_button.configure(text="▶  Start listening", bootstyle="success")
            self.status_var.set("Paused")
            self._reset_readings()
            return
        try:
            source = self.source_var.get()
            if source == "file":
                if not self.audio_path:
                    self.choose_audio()
                if self.audio_path:
                    self.status_var.set("Loading track…")
                    self.audio.load_audio_file(self.audio_path)
            elif source == "mic":
                device_id = self.input_devices.get(self.device_var.get())
                if device_id is None:
                    raise RuntimeError("No microphone input is available.")
                self.audio.start_microphone(device_id)
                self._listening_started("Microphone")
            else:
                target = self.app_targets.get(self.device_var.get())
                if target is None:
                    raise RuntimeError("Refresh the app list and choose an app or output.")
                target_type, target_value = target
                if target_type == "process":
                    self.audio.start_process_audio(int(target_value))
                    self._listening_started(self.device_var.get())
                else:
                    self.audio.start_system_audio(str(target_value))
                    self._listening_started(f"Desktop audio · {target_value}")
        except Exception as error:
            messagebox.showerror(APP_NAME, str(error))
            self.audio.stop()

    def _listening_started(self, label: str) -> None:
        self.listen_started = time.monotonic()
        self.status_var.set(f"Listening · {label}")
        self.run_button.configure(text="■  Stop listening", bootstyle="danger")
        self.status_dot.configure(foreground="#31a880")

    def _poll_audio(self) -> None:
        latest_analysis: tuple[float | None, float, str | None] | None = None
        while True:
            try:
                kind, value, token = self.audio.events.get_nowait()
            except queue.Empty:
                break
            if token != self.audio.token:
                continue
            if kind == "analysis":
                latest_analysis = value  # type: ignore[assignment]
            elif kind == "file-loaded":
                samples, sample_rate, path = value  # type: ignore[misc]
                self.audio.play_loaded_file(samples, sample_rate, path)
            elif kind == "started":
                self._listening_started(str(value))
            elif kind == "finished":
                self.audio.stop()
                self.run_button.configure(text="▶  Start listening", bootstyle="success")
                self.status_var.set("Track finished")
                self._reset_readings()
            elif kind == "error":
                self.audio.stop()
                self.run_button.configure(text="▶  Start listening", bootstyle="success")
                self.status_var.set("Audio source stopped")
                self._reset_readings()
                messagebox.showerror(APP_NAME, str(value))
        if latest_analysis:
            bpm, energy, mood = latest_analysis
            self.energy = energy
            if bpm is not None:
                self.current_bpm = bpm
                self.bpm_var.set(str(round(bpm)))
            else:
                self.current_bpm = None
                self.bpm_var.set("--")
            self.energy_var.set(round(energy * 100))
            self._set_mood(mood)
            self.status_dot.configure(foreground="#31a880")
        self.root.after(80, self._poll_audio)

    def toggle_demo(self) -> None:
        self.audio.stop()
        if getattr(self, "demo_active", False):
            self.demo_active = False
            self.demo_button.configure(text="Try a demo beat")
            self.run_button.configure(text="▶  Start listening", bootstyle="success")
            self.status_var.set("Ready when you are")
            self._reset_readings()
            return
        self.demo_active = True
        self.demo_button.configure(text="Stop demo beat")
        self.status_var.set("Demo beat · no audio captured")
        self._listening_started("Demo beat")
        self._demo_index = 0
        self._demo_tick()

    def _demo_tick(self) -> None:
        if not getattr(self, "demo_active", False):
            return
        samples = (
            (72, 0.18, 0.08, 0.8),
            (112, 0.48, 0.32, 2.5),
            (138, 0.82, 0.45, 4.2),
            (96, 0.35, 0.12, 0.9),
        )
        bpm, energy, brightness, onset_density = samples[self._demo_index % len(samples)]
        self._demo_index += 1
        self.current_bpm = float(bpm)
        self.energy = energy
        self.bpm_var.set(str(bpm))
        self.energy_var.set(round(energy * 100))
        self._set_mood(BeatAnalyzer.mood_for(float(bpm), energy, brightness, onset_density))
        self.root.after(2400, self._demo_tick)

    def _walk_duration(self, music_active: bool) -> float:
        return random.uniform(12.0, 24.0) if music_active else random.uniform(18.0, 36.0)

    def _random_vertical_heading(self) -> float:
        max_slope = 0.85 if self.roaming_area_var.get() == "Whole desktop" else 0.35
        return random.uniform(-max_slope, max_slope)

    def _roaming_vertical_bounds(self) -> tuple[int, int]:
        screen_height = self.root.winfo_screenheight()
        max_y = max(0, screen_height - 190)
        if self.roaming_area_var.get() == "Whole desktop":
            return 0, max_y
        return min(int(screen_height * 0.62), max_y), max_y

    def change_roaming_area(self, _event: tk.Event | None = None) -> None:
        min_y, max_y = self._roaming_vertical_bounds()
        self.pet_y = float(max(min_y, min(max_y, self.pet_y)))
        self._save_settings()

    def _next_turn_delay(self, music_active: bool) -> float:
        if self.roaming_area_var.get() == "Whole desktop":
            return random.uniform(10.0, 16.0) if music_active else random.uniform(12.0, 20.0)
        return random.uniform(12.0, 18.0) if music_active else random.uniform(16.0, 24.0)

    def _choose_heading(self) -> None:
        if self.roaming_area_var.get() == "Whole desktop":
            angle = random.uniform(0.0, math.tau)
            self.pet_speed_x = math.cos(angle)
            self.pet_speed_y = math.sin(angle)
        else:
            self.pet_speed_x = float(random.choice((-1, 1)))
            self.pet_speed_y = self._random_vertical_heading()
        self.pet_facing_left = self.pet_speed_x < 0
        self._show_frame()

    def _start_walking(self, now: float, music_active: bool) -> None:
        self.motion_state = "walking"
        self.motion_state_until = now + self._walk_duration(music_active)
        self.next_turn_at = now + self._next_turn_delay(music_active)
        self._choose_heading()
        self._load_character_art()

    def _start_resting(self, now: float, music_active: bool) -> None:
        self.motion_state = "resting"
        self.motion_state_until = now + (random.uniform(1.2, 2.8) if music_active else random.uniform(2.0, 4.5))
        self._load_character_art()

    def _animate(self) -> None:
        now = time.monotonic()
        elapsed = min(0.2, max(0.0, now - self.last_animation_tick))
        self.last_animation_tick = now
        if self.is_dragging:
            self.root.after(80, self._animate)
            return
        still = self.behavior_var.get() == "Stay still"
        music_active = self._music_is_detected()
        if music_active != self.motion_music_active:
            self.motion_music_active = music_active
            self._start_walking(now, music_active)

        if not still and now >= self.motion_state_until:
            if self.motion_state == "walking":
                if random.random() < 0.35:
                    self._start_walking(now, music_active)
                else:
                    self._start_resting(now, music_active)
            else:
                self._start_walking(now, music_active)

        if self.gif_frames and not still and now - self.gif_last_tick >= self.gif_durations[self.gif_index] / 1000:
            self.gif_index = (self.gif_index + 1) % len(self.gif_frames)
            self.gif_last_tick = now
            self._show_frame()

        if not still and self.motion_state == "walking":
            if now >= self.next_turn_at:
                self._choose_heading()
                self.next_turn_at = now + self._next_turn_delay(music_active)
            if music_active:
                speed = {"dreamy": 48, "chill": 42, "upbeat": 82, "energetic": 115}.get(self.current_mood, 65)
            else:
                speed = 36
            velocity_length = math.hypot(self.pet_speed_x, self.pet_speed_y) or 1.0
            self.pet_speed_x = self.pet_speed_x / velocity_length * speed
            self.pet_speed_y = self.pet_speed_y / velocity_length * speed
            facing_left = self.pet_speed_x < 0
            if facing_left != self.pet_facing_left:
                self.pet_facing_left = facing_left
                self._show_frame()
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            max_x = max(0, screen_width - 180)
            min_y, max_y = self._roaming_vertical_bounds()
            self.pet_x += self.pet_speed_x * elapsed
            self.pet_y += self.pet_speed_y * elapsed
            if self.pet_x <= 0 or self.pet_x >= max_x:
                self.pet_x = max(0, min(max_x, self.pet_x))
                self.pet_speed_x *= -1
                self.pet_facing_left = self.pet_speed_x < 0
                self._show_frame()
            if self.pet_y <= min_y or self.pet_y >= max_y:
                self.pet_y = max(min_y, min(max_y, self.pet_y))
                self.pet_speed_y *= -1
            if not self.is_dragging:
                self.pet_window.move_pet(self.pet_x, self.pet_y)
            scene_x = 267 + math.sin(now * 0.55) * 20
            self.scene.coords(self.scene_sprite, scene_x, 301)
            self.scene.coords(self.scene_art_backing, scene_x - 45, 270, scene_x + 45, 360)
            self.scene.coords(self.scene_art_name, scene_x, 335)
            self.scene.coords(self.scene_image, scene_x, 315)
        self.root.after(80, self._animate)

    def _start_tray_icon(self) -> None:
        icon_image = Image.new("RGBA", (64, 64), (243, 199, 95, 255))
        draw = ImageDraw.Draw(icon_image)
        draw.rounded_rectangle((7, 7, 57, 57), radius=15, fill=(243, 199, 95, 255))
        draw.text((15, 10), "c.", fill=(58, 52, 36, 255), font_size=35)
        menu = pystray.Menu(
            pystray.MenuItem("Open settings", lambda _icon, _item: self.root.after(0, self.show_settings)),
            pystray.MenuItem("Show / hide pet", lambda _icon, _item: self.root.after(0, self.toggle_pet)),
            pystray.MenuItem("Quit", lambda _icon, _item: self.root.after(0, self.quit)),
        )
        self.tray_icon = pystray.Icon("chiikawa-desktop-club", icon_image, APP_NAME, menu)
        self.tray_icon.run_detached()

    def _pet_menu_action(self, action: str) -> None:
        if action == "menu":
            self.root.after(0, self.show_settings)
        elif action == "drag-start":
            self.is_dragging = True
        elif action == "drag":
            self.pet_x = float(self.pet_window.window.winfo_x())
            self.pet_y = float(self.pet_window.window.winfo_y())
        elif action == "drag-end":
            self.pet_x = float(self.pet_window.window.winfo_x())
            self.pet_y = float(self.pet_window.window.winfo_y())
            self.is_dragging = False
            now = time.monotonic()
            music_active = self.audio.active or self.demo_active
            self.motion_state = "walking"
            self.motion_state_until = now + self._walk_duration(music_active)
            self.next_turn_at = now + self._next_turn_delay(music_active)
            self._save_settings()

    def toggle_pet(self) -> None:
        if self.pet_window.visible:
            self.pet_window.window.withdraw()
        else:
            self.pet_window.window.deiconify()
            self.pet_window.ensure_topmost()
        self.pet_window.visible = not self.pet_window.visible

    def hide_settings(self) -> None:
        self.hide_settings_window()
        self._save_settings()
        self.root.withdraw()
        if self.pet_window.visible:
            self.pet_window.window.deiconify()
            self.pet_window.ensure_topmost()
            self.pet_window.window.lift()
        self.status_var.set("Running in the system tray")

    def show_settings(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def reset_settings(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Reset saved character and behavior settings?"):
            return
        self.settings = {}
        self.character_var.set("Chiikawa")
        self.behavior_var.set("Follow the beat")
        self.roaming_area_var.set("Bottom stroll")
        self.outline_enabled.set(False)
        self.outline_color = "#ffffff"
        self.outline_swatch.configure(background=self.outline_color, activebackground=self.outline_color)
        self.custom_animation_button.configure(state="disabled")
        if getattr(self, "custom_animation_window", None) is not None:
            self.custom_animation_window.withdraw()
            self._refresh_custom_animation_labels()
        self.audio_path = ""
        self.file_name.configure(text="MP3, WAV, FLAC and more")
        self._save_settings()
        self._load_character_art()

    def quit(self) -> None:
        self.audio.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.pet_window.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DesktopCompanion().run()