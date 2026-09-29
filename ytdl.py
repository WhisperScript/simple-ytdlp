#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["imageio-ffmpeg", "certifi", "sv-ttk", "darkdetect"]
# ///
"""
ytdl.py - cross-platform frontend for yt-dlp (macOS, Linux, Windows).

yt-dlp and deno (the JS engine YouTube needs) are downloaded into the user's data folder
on first start and can be updated with one click. ffmpeg is bundled (imageio-ffmpeg)
if no system ffmpeg is installed.

Easiest way (only uv needed):
    uv run ytdl.py
    ./ytdl.py                      (macOS/Linux, after chmod +x)

Without uv, using an existing Python (then: pip install imageio-ffmpeg certifi sv-ttk darkdetect):
    python3 ytdl.py "https://youtube.com/watch?v=XXXX"
    python3 ytdl.py -a urls.txt --mode mp3
    python3 ytdl.py --gui

Ready-made programs (Windows/macOS/Linux): see the README, section "Ready-made programs".
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

__version__ = "dev"          # the release workflow replaces this with the git tag (v1.2 -> "1.2")
REPO = "WhisperScript/ytdl"  # GitHub repo that hosts the releases (used for the update hint)

APP_NAME = "ytdl"
DEFAULT_OUT = Path.home() / "Downloads" / "yt-dlp"

MODES = {
    "video":      "Video - best quality",
    "video1080":  "Video - max. 1080p",
    "video720":   "Video - max. 720p",
    "mp3":        "Audio - mp3",
    "m4a":        "Audio - m4a",
    "opus":       "Audio - opus",
}
AUDIO_MODES = ("mp3", "m4a", "opus")
ALSO_AUDIO_NONE = "None"

BROWSERS = ["", "chrome", "firefox", "safari", "edge", "brave", "chromium", "vivaldi", "opera"]


# ---------------------------------------------------------------- Folders & settings

def data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


BIN_DIR = data_dir() / "bin"
SETTINGS_FILE = data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    """Merge data into the stored settings (unknown keys are kept)."""
    try:
        merged = {**load_settings(), **data}
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    except Exception:
        pass


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _no_window() -> dict:
    """On Windows, keep console windows from flashing up."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ---------------------------------------------------------------- Fetching tools

def _machine() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def _ytdlp_asset() -> str:
    arm = _machine() == "arm64"
    if os.name == "nt":
        return "yt-dlp_arm64.exe" if arm else "yt-dlp.exe"
    if sys.platform == "darwin":
        return "yt-dlp_macos"
    return "yt-dlp_linux_aarch64" if arm else "yt-dlp_linux"


def _deno_triple() -> str:
    arm = _machine() == "arm64"
    if os.name == "nt":
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin":
        return "aarch64-apple-darwin" if arm else "x86_64-apple-darwin"
    return "aarch64-unknown-linux-gnu" if arm else "x86_64-unknown-linux-gnu"


def _ssl_context() -> ssl.SSLContext:
    try:                                   # packaged Pythons often lack CA certificates
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch(url: str, dest: Path, log) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r, tmp.open("wb") as fh:
        shutil.copyfileobj(r, fh, 1 << 16)
    tmp.replace(dest)


def _make_executable(path: Path) -> None:
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def managed_ytdlp() -> Path:
    return BIN_DIR / _exe("yt-dlp")


def managed_deno() -> Path:
    return BIN_DIR / _exe("deno")


_tools_lock = threading.Lock()


def install_ytdlp(log=print) -> bool:
    """Download the latest yt-dlp standalone binary into the user data folder."""
    with _tools_lock:
        url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{_ytdlp_asset()}"
        log("Downloading yt-dlp ...")
        try:
            _fetch(url, managed_ytdlp(), log)
            _make_executable(managed_ytdlp())
        except Exception as e:
            log(f"ERROR downloading yt-dlp: {e}")
            return False
        log("yt-dlp ready.")
        return True


def update_ytdlp(log=print) -> bool:
    """Update yt-dlp (downloads it if it is not there yet)."""
    if not managed_ytdlp().exists():
        return install_ytdlp(log)
    log("Checking for yt-dlp update ...")
    return _stream([str(managed_ytdlp()), "-U"], log) == 0


def install_deno(log=print) -> bool:
    with _tools_lock:
        url = f"https://github.com/denoland/deno/releases/latest/download/deno-{_deno_triple()}.zip"
        zpath = BIN_DIR / "deno.zip"
        log("Downloading deno (JS engine for YouTube) ...")
        try:
            _fetch(url, zpath, log)
            with zipfile.ZipFile(zpath) as zf:
                zf.extract(_exe("deno"), BIN_DIR)
            zpath.unlink(missing_ok=True)
            _make_executable(managed_deno())
        except Exception as e:
            log(f"ERROR downloading deno: {e}")
            return False
        log("deno ready.")
        return True


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def check_app_update() -> tuple[str, str] | None:
    """(latest version, release page URL) if a newer ytdl release exists, else None.

    Silent on any failure (offline, private repo, rate limit) - it is only a hint.
    """
    if __version__ == "dev":
        return None
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
            data = json.load(r)
        latest = str(data["tag_name"])
        if _version_tuple(latest) > _version_tuple(__version__):
            return latest.lstrip("v"), str(data.get("html_url") or f"https://github.com/{REPO}/releases")
    except Exception:
        pass
    return None


def ensure_tools(log=print) -> bool:
    """Make sure yt-dlp and a JS engine are present. False = yt-dlp is missing."""
    if find_ytdlp() is None and not install_ytdlp(log):
        return False
    if find_js_runtime() is None:
        install_deno(log)                  # not critical: only affects high-resolution formats
    return True


# ---------------------------------------------------------------- Locating tools

def find_ytdlp() -> list[str] | None:
    """Return the command prefix for yt-dlp, or None."""
    if managed_ytdlp().exists():
        return [str(managed_ytdlp())]
    candidates = []
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        candidates.append([exe])
    if not getattr(sys, "frozen", False):
        candidates.append([sys.executable, "-m", "yt_dlp"])
    for cmd in candidates:
        try:
            r = subprocess.run(cmd + ["--version"], capture_output=True,
                               text=True, timeout=30, **_no_window())
            if r.returncode == 0:
                return cmd
        except Exception:
            continue
    return None


def find_ffmpeg() -> tuple[str | None, str]:
    """(path, source) of the ffmpeg to use. source: system | bundled | none."""
    exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if exe:
        return exe, "system"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe(), "bundled"
    except Exception:
        return None, "none"


def find_js_runtime() -> list[str] | None:
    """yt-dlp arguments for the JS engine (YouTube needs one for all formats)."""
    if managed_deno().exists():
        return ["--js-runtimes", f"deno:{managed_deno()}"]
    for name in ("deno", "node", "bun"):
        path = shutil.which(name) or shutil.which(name + ".exe")
        if path:
            return ["--js-runtimes", f"{name}:{path}"]
    return None


def ffmpeg_hint() -> str:
    if sys.platform == "darwin":
        return "ffmpeg missing - install with:  brew install ffmpeg"
    if os.name == "nt":
        return "ffmpeg missing - install with:  winget install Gyan.FFmpeg"
    return "ffmpeg missing - install with:  sudo apt install ffmpeg  (or dnf/pacman)"


# ---------------------------------------------------------------- Building arguments

def build_args(mode: str, out_dir: Path, *, subs=False, thumb=False,
               archive=False, cookies_browser="", sort_by_uploader=False,
               no_playlist=False, also_audio="",
               extra: list[str] | None = None) -> list[str]:
    out_dir = Path(out_dir).expanduser()
    tmpl = "%(uploader)s/%(title)s.%(ext)s" if sort_by_uploader else "%(title)s.%(ext)s"

    args = [
        "-o", str(out_dir / tmpl),
        "--no-overwrites",
        "--retries", "3",
        "--embed-metadata",
        "--newline",              # one progress line per update -> works well in the log
        "--progress",
    ]

    if mode in AUDIO_MODES:
        args += ["-x", "--audio-format", mode, "--audio-quality", "0"]
    elif mode == "video1080":
        args += ["-f", "bv*[height<=1080]+ba/b[height<=1080]/b"]
    elif mode == "video720":
        args += ["-f", "bv*[height<=720]+ba/b[height<=720]/b"]
    else:
        args += ["-f", "bv*+ba/b"]

    if also_audio and mode not in AUDIO_MODES:   # keep the video AND save a separate audio file
        args += ["-x", "--audio-format", also_audio, "--audio-quality", "0", "-k"]

    if subs and mode not in AUDIO_MODES:      # subtitles cannot be embedded in audio files
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", "en,de", "--embed-subs",
                 "--sleep-subtitles", "3",         # YouTube answers quick subtitle requests with HTTP 429
                 "--ignore-errors"]                # ...so a failed subtitle track must not fail the video
    if thumb:
        args += ["--embed-thumbnail"]
    if archive:
        args += ["--download-archive", str(out_dir / "archive.txt")]
    if cookies_browser:
        args += ["--cookies-from-browser", cookies_browser]
    if no_playlist:
        args += ["--no-playlist"]
    if extra:
        args += extra
    return args


# ---------------------------------------------------------------- Running

def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and its children (yt-dlp spawns ffmpeg)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, **_no_window())
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _stream(cmd: list[str], log, stop_flag=None) -> int:
    """Run cmd and pass every output line to log(). Returns the return code."""
    extra = _no_window()
    if os.name != "nt":
        extra["start_new_session"] = True     # own process group -> can be cancelled cleanly
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace", **extra
        )
    except OSError as e:
        log(f"ERROR: {e}")
        return 127

    if stop_flag is not None:
        def watch():                          # works even while no output is coming in
            while proc.poll() is None:
                if stop_flag.wait(0.3):
                    _kill_tree(proc)
                    log("-- cancelled --")
                    return
        threading.Thread(target=watch, daemon=True).start()

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            log(line.rstrip())
    except KeyboardInterrupt:
        _kill_tree(proc)
        raise
    return proc.wait()


INTERMEDIATE_RE = re.compile(r"\.f\d[0-9A-Za-z_-]*\.[A-Za-z0-9]+$")   # e.g. "Title.f251.webm"


def _intermediates(folder: Path) -> set[Path]:
    """Per-stream files yt-dlp leaves behind when told to keep the video (-k)."""
    return {p for p in folder.rglob("*") if p.is_file() and INTERMEDIATE_RE.search(p.name)}


def download(urls: list[str], args: list[str], out_dir: Path, log=print, stop_flag=None,
             clean_intermediates: bool = False) -> tuple[int, int]:
    """Download every URL. clean_intermediates removes the raw stream files that -k leaves
    behind (only files created by this run, so nothing that was already there is touched)."""
    if not ensure_tools(log):
        log("Could not download yt-dlp. Check your internet connection and try again.")
        return (0, len(urls))
    base = find_ytdlp()
    assert base is not None

    ffmpeg, source = find_ffmpeg()
    if source == "bundled":
        args = args + ["--ffmpeg-location", ffmpeg]
    elif source == "none":
        log("WARNING: " + ffmpeg_hint())

    js = find_js_runtime()
    if js:
        args = args + js
    else:
        log("Note: no JS engine found - high-resolution formats may be missing.")

    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    failed_log = out_dir / "failed.log"

    ok = bad = 0
    for i, url in enumerate(urls, 1):
        if stop_flag is not None and stop_flag.is_set():
            log("-- cancelled --")
            break
        log(f"\n[{i}/{len(urls)}] {url}")
        before = _intermediates(out_dir) if clean_intermediates else set()
        rc = _stream(base + args + ["--", url], log, stop_flag)
        if clean_intermediates:
            for leftover in _intermediates(out_dir) - before:
                try:
                    leftover.unlink()
                except OSError:
                    pass
        if stop_flag is not None and stop_flag.is_set():
            break
        if rc == 0:
            ok += 1
        else:
            bad += 1
            log(f"FAILED ({rc}): {url}")
            with failed_log.open("a", encoding="utf-8") as fh:
                fh.write(url + "\n")

    log(f"\nDone. {ok} succeeded, {bad} failed.")
    if bad:
        log(f"List of failures: {failed_log}")
    return (ok, bad)


def read_url_file(path: str | Path) -> list[str]:
    urls = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def open_folder(path: Path) -> None:
    path = Path(path).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))               # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------- GUI

UPDATE_INTERVAL = 24 * 3600      # how often (seconds) to silently check for yt-dlp updates at startup


def notify(title: str, message: str, root=None) -> None:
    """Announce the end of a run without extra dependencies."""
    try:
        if sys.platform == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.Popen(["osascript", "-e", script])
        elif os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                            ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                            ("dwTimeout", wintypes.DWORD)]

            if root is not None:              # taskbar icon flashes until the window is focused
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x0000000F | 0x0000000C, 3, 0)
                ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message])
    except Exception:
        pass
    if root is not None:
        try:
            root.bell()
        except Exception:
            pass


def looks_like_url(text: str) -> bool:
    text = text.strip()
    return bool(text) and "\n" not in text and re.match(r"https?://\S+$", text) is not None


PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
ITEM_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s")


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("Tkinter is not installed.")
        print("Linux:  sudo apt install python3-tk   (or python3-tkinter)")
        return 1

    cfg = load_settings()

    root = tk.Tk()
    root.title("yt-dlp Downloader" + ("" if __version__ == "dev" else f"  v{__version__}"))
    root.geometry("820x740")
    root.minsize(700, 600)

    try:                                       # modern theme; without sv-ttk the default Tk look stays
        import sv_ttk
    except ImportError:
        sv_ttk = None

    def system_theme() -> str:
        try:
            import darkdetect
            return "dark" if darkdetect.isDark() else "light"
        except Exception:
            return "light"

    theme = (cfg.get("theme") or system_theme()) if sv_ttk is not None else "light"
    text_widgets: list = []

    def apply_theme(name: str) -> None:
        nonlocal theme
        theme = name
        if sv_ttk is not None:
            sv_ttk.set_theme(name)
        dark = name == "dark"
        for w in text_widgets:                 # ttk themes do not cover tk.Text
            w.configure(background="#2b2b2b" if dark else "#ffffff",
                        foreground="#e6e6e6" if dark else "#1a1a1a",
                        insertbackground="#e6e6e6" if dark else "#1a1a1a",
                        highlightbackground="#4a4a4a" if dark else "#c8c8c8",
                        highlightcolor="#60cdff" if dark else "#0067c0")

    pad = {"padx": 8, "pady": 5}
    main = ttk.Frame(root, padding=14)
    main.pack(fill="both", expand=True)
    main.columnconfigure(0, weight=1)

    head = ttk.Frame(main)
    head.grid(row=0, column=0, columnspan=2, sticky="ew")
    ttk.Label(head, text="URLs (one per line):").pack(side="left")
    def toggle_theme() -> None:
        apply_theme("light" if theme == "dark" else "dark")
        save_settings({"theme": theme})

    if sv_ttk is not None:                     # without sv-ttk, toggling would only recolor the text boxes
        ttk.Button(head, text="Light/Dark", command=toggle_theme).pack(side="right")
    ttk.Button(head, text="From clipboard",
               command=lambda: paste_clipboard(force=True)).pack(side="right", padx=6)

    url_box = tk.Text(main, height=7, wrap="none", relief="flat", borderwidth=0,
                      highlightthickness=1, padx=6, pady=6)
    text_widgets.append(url_box)
    url_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6, 10))
    main.rowconfigure(1, weight=1)

    last_clip = {"text": ""}

    def paste_clipboard(force: bool = False) -> None:
        """Insert a URL from the clipboard (automatically only when the box is empty)."""
        try:
            clip = root.clipboard_get().strip()
        except tk.TclError:
            return
        if not looks_like_url(clip):
            if force:
                messagebox.showinfo("Clipboard", "There is no URL in the clipboard.")
            return
        current = url_box.get("1.0", "end")
        if clip in current or (not force and (current.strip() or clip == last_clip["text"])):
            return
        last_clip["text"] = clip
        url_box.insert("end", ("\n" if current.strip() else "") + clip)

    root.bind("<FocusIn>", lambda e: paste_clipboard() if e.widget is root else None)

    opts = ttk.Frame(main)
    opts.grid(row=2, column=0, columnspan=2, sticky="ew")
    opts.columnconfigure(1, weight=1)

    ttk.Label(opts, text="Mode:").grid(row=0, column=0, sticky="w", **pad)
    mode_var = tk.StringVar(value=MODES.get(cfg.get("mode", ""), MODES["video"]))
    ttk.Combobox(opts, textvariable=mode_var, values=list(MODES.values()),
                 state="readonly").grid(row=0, column=1, columnspan=2, sticky="ew", **pad)

    ttk.Label(opts, text="Output folder:").grid(row=1, column=0, sticky="w", **pad)
    out_var = tk.StringVar(value=cfg.get("out") or str(DEFAULT_OUT))
    ttk.Entry(opts, textvariable=out_var).grid(row=1, column=1, sticky="ew", **pad)

    def pick_dir():
        d = filedialog.askdirectory(initialdir=out_var.get() or str(Path.home()))
        if d:
            out_var.set(d)

    ttk.Button(opts, text="Browse ...", command=pick_dir).grid(row=1, column=2, **pad)

    ttk.Label(opts, text="Cookies from browser:").grid(row=2, column=0, sticky="w", **pad)
    cookie_var = tk.StringVar(value=cfg.get("cookies", ""))
    ttk.Combobox(opts, textvariable=cookie_var, values=BROWSERS,
                 state="readonly", width=14).grid(row=2, column=1, sticky="w", **pad)

    ttk.Label(opts, text="Also save audio as:").grid(row=3, column=0, sticky="w", **pad)
    also_var = tk.StringVar(value=cfg.get("also_audio") or ALSO_AUDIO_NONE)
    also_row = ttk.Frame(opts)
    also_row.grid(row=3, column=1, columnspan=2, sticky="w", **pad)
    ttk.Combobox(also_row, textvariable=also_var, values=[ALSO_AUDIO_NONE, *AUDIO_MODES],
                 state="readonly", width=14).pack(side="left")
    ttk.Label(also_row, text="  (video modes: keeps the video and adds a separate audio file)",
              foreground="gray").pack(side="left")

    checks = ttk.Frame(main)
    checks.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 8))
    subs_var = tk.BooleanVar(value=cfg.get("subs", False))
    thumb_var = tk.BooleanVar(value=cfg.get("thumb", False))
    arch_var = tk.BooleanVar(value=cfg.get("archive", True))
    uploader_var = tk.BooleanVar(value=cfg.get("uploader", False))
    single_var = tk.BooleanVar(value=cfg.get("single", True))
    for i, (text, var) in enumerate((("Subtitles (en/de)", subs_var),
                                     ("Embed thumbnail", thumb_var),
                                     ("Archive (skip already downloaded)", arch_var),
                                     ("Sort into folders by channel", uploader_var),
                                     ("Single video only (no playlist)", single_var))):
        ttk.Checkbutton(checks, text=text, variable=var).grid(
            row=i // 3, column=i % 3, sticky="w", padx=6, pady=3)

    btns = ttk.Frame(main)
    btns.grid(row=4, column=0, columnspan=2, sticky="ew")
    start_btn = ttk.Button(btns, text="Start download")
    start_btn.pack(side="left")
    stop_btn = ttk.Button(btns, text="Cancel", state="disabled")
    stop_btn.pack(side="left", padx=6)
    ttk.Button(btns, text="Open folder",
               command=lambda: open_folder(Path(out_var.get() or DEFAULT_OUT))).pack(side="left")
    update_btn = ttk.Button(btns, text="Update yt-dlp")
    update_btn.pack(side="right")

    status_var = tk.StringVar(value="")
    ttk.Label(main, textvariable=status_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
    bar = ttk.Progressbar(main, maximum=100)
    bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 0))

    log_box = tk.Text(main, height=14, wrap="none", state="disabled", relief="flat",
                      borderwidth=0, highlightthickness=1, padx=6, pady=6)
    text_widgets.append(log_box)
    apply_theme(theme)
    log_box.grid(row=7, column=0, sticky="nsew", pady=(8, 0))
    main.rowconfigure(7, weight=2)
    sb = ttk.Scrollbar(main, command=log_box.yview)
    sb.grid(row=7, column=1, sticky="ns", pady=(8, 0))
    log_box.configure(yscrollcommand=sb.set)

    news = ttk.Frame(main)                     # update hint, only shown when a newer release exists
    news.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    news_label = ttk.Label(news, text="")
    news_label.pack(side="left")
    news_btn = ttk.Button(news, text="Download")
    news_btn.pack(side="right")
    news.grid_remove()

    def show_app_update(version: str, url: str) -> None:
        news_label.configure(text=f"A new version of ytdl is available: v{version}")
        news_btn.configure(command=lambda: webbrowser.open(url))
        news.grid()
        gui_log(f"New version available: v{version}  ->  {url}")

    def gui_log(msg: str) -> None:
        def _append():
            m = PROGRESS_RE.search(str(msg))
            if m:
                bar["value"] = float(m.group(1))
            m = ITEM_RE.match(str(msg))
            if m:
                status_var.set(f"Download {m.group(1)} of {m.group(2)}")
                bar["value"] = 0
            log_box.configure(state="normal")
            log_box.insert("end", str(msg) + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, _append)

    stop_flag = threading.Event()

    def mode_key() -> str:
        for k, v in MODES.items():
            if v == mode_var.get():
                return k
        return "video"

    def set_busy(busy: bool) -> None:
        start_btn.configure(state="disabled" if busy else "normal")
        update_btn.configure(state="disabled" if busy else "normal")
        stop_btn.configure(state="normal" if busy else "disabled")

    def on_start():
        urls = [l.strip() for l in url_box.get("1.0", "end").splitlines() if l.strip()]
        urls = [u for u in urls if not u.startswith("#")]
        if not urls:
            messagebox.showwarning("No URLs", "Please enter at least one URL.")
            return
        save_settings({"mode": mode_key(), "out": out_var.get(), "cookies": cookie_var.get(),
                       "subs": subs_var.get(), "thumb": thumb_var.get(), "archive": arch_var.get(),
                       "uploader": uploader_var.get(), "single": single_var.get(),
                       "also_audio": also_var.get(), "theme": theme})
        stop_flag.clear()
        set_busy(True)
        bar["value"] = 0
        status_var.set("")
        out_dir = Path(out_var.get() or DEFAULT_OUT)
        mode = mode_key()
        also_audio = "" if also_var.get() == ALSO_AUDIO_NONE else also_var.get()
        args = build_args(mode, out_dir, subs=subs_var.get(), thumb=thumb_var.get(),
                          archive=arch_var.get(), cookies_browser=cookie_var.get(),
                          sort_by_uploader=uploader_var.get(), no_playlist=single_var.get(),
                          also_audio=also_audio)

        def work():
            try:
                ok, bad = download(urls, args, out_dir, gui_log, stop_flag,
                                   clean_intermediates=also_audio and mode not in AUDIO_MODES)
                summary = f"{ok} succeeded, {bad} failed"
                root.after(0, lambda: status_var.set("Done: " + summary))
                if not stop_flag.is_set():
                    root.after(0, lambda: notify("ytdl", "Done: " + summary, root))
            finally:
                root.after(0, lambda: set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def on_update():
        set_busy(True)
        stop_btn.configure(state="disabled")

        def work():
            try:
                if update_ytdlp(gui_log):
                    save_settings({"last_update": time.time()})
                if find_js_runtime() is None:
                    install_deno(gui_log)
            finally:
                root.after(0, lambda: set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    start_btn.configure(command=on_start)
    update_btn.configure(command=on_update)
    stop_btn.configure(command=lambda: (stop_flag.set(), gui_log("Cancelling ...")))

    gui_log(f"Ready. Output folder: {out_var.get()}")
    _, _src = find_ffmpeg()
    if _src == "none":
        gui_log("Note: " + ffmpeg_hint())

    # At startup, in the background: fetch missing tools, otherwise check for yt-dlp updates once a day
    # (YouTube changes often; an outdated yt-dlp is the most common reason downloads fail).
    missing = find_ytdlp() is None or find_js_runtime() is None
    update_due = (managed_ytdlp().exists()
                  and time.time() - float(cfg.get("last_update", 0)) > UPDATE_INTERVAL)
    if missing or update_due:
        set_busy(True)
        stop_btn.configure(state="disabled")
        status_var.set("Checking for updates ..." if not missing else "First start: downloading tools ...")

        def startup():
            try:
                if missing:
                    ensure_tools(gui_log)
                    ok = find_ytdlp() is not None
                else:
                    ok = update_ytdlp(gui_log)
                if ok:
                    save_settings({"last_update": time.time()})
            finally:
                root.after(0, lambda: (set_busy(False), status_var.set("")))

        threading.Thread(target=startup, daemon=True).start()

    def app_update_check():                    # newer ytdl release on GitHub? (silent if not)
        found = check_app_update()
        if found:
            root.after(0, lambda: show_app_update(*found))

    threading.Thread(target=app_update_check, daemon=True).start()

    root.mainloop()
    return 0


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Cross-platform yt-dlp wrapper (CLI + GUI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 ytdl.py URL1 URL2\n"
               "  python3 ytdl.py -a urls.txt -m mp3 -o ~/Music\n"
               "  python3 ytdl.py --gui\n",
    )
    p.add_argument("urls", nargs="*", help="One or more URLs")
    p.add_argument("-a", "--batch-file", help="Text file with one URL per line (# = comment)")
    p.add_argument("-m", "--mode", choices=list(MODES), default="video",
                   help="Download mode (default: video)")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="Output folder")
    p.add_argument("--also-audio", choices=AUDIO_MODES, default="", metavar="FORMAT",
                   help="with a video mode: also save a separate audio file (mp3, m4a or opus)")
    p.add_argument("--subs", action="store_true", help="Download subtitles (en/de)")
    p.add_argument("--thumb", action="store_true", help="Embed thumbnail")
    p.add_argument("--archive", action="store_true", help="Keep an archive.txt (skip already downloaded videos)")
    p.add_argument("--by-uploader", action="store_true", help="Sort into one subfolder per channel")
    p.add_argument("--cookies-from-browser", default="", metavar="BROWSER",
                   help="e.g. chrome, firefox, safari - for private/age-restricted videos")
    p.add_argument("--gui", action="store_true", help="Start the graphical interface")
    p.add_argument("--update", action="store_true", help="Install/update yt-dlp and exit")

    args, unknown = p.parse_known_args(argv)

    if args.update:
        return 0 if update_ytdlp() else 1
    if args.gui or (not args.urls and not args.batch_file):
        return run_gui()

    urls = list(args.urls)
    if args.batch_file:
        urls += read_url_file(args.batch_file)
    if not urls:
        p.error("No URLs given.")

    out_dir = Path(args.out).expanduser()
    ytdlp_args = build_args(args.mode, out_dir, subs=args.subs, thumb=args.thumb,
                            archive=args.archive, cookies_browser=args.cookies_from_browser,
                            sort_by_uploader=args.by_uploader, also_audio=args.also_audio,
                            extra=unknown)
    ok, bad = download(urls, ytdlp_args, out_dir,
                       clean_intermediates=bool(args.also_audio) and args.mode not in AUDIO_MODES)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
