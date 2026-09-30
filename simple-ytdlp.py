#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["imageio-ffmpeg", "certifi", "sv-ttk", "darkdetect", "pillow"]
# ///
"""
simple-ytdlp.py - cross-platform frontend for yt-dlp (macOS, Linux, Windows).

yt-dlp and deno (the JS engine YouTube needs) are downloaded into the user's data folder
on first start and can be updated with one click. ffmpeg is bundled (imageio-ffmpeg)
if no system ffmpeg is installed.

Easiest way (only uv needed):
    uv run simple-ytdlp.py
    ./simple-ytdlp.py                      (macOS/Linux, after chmod +x)

Without uv, using an existing Python (then: pip install imageio-ffmpeg certifi sv-ttk darkdetect pillow):
    python3 simple-ytdlp.py "https://youtube.com/watch?v=XXXX"
    python3 simple-ytdlp.py -a urls.txt --mode mp3
    python3 simple-ytdlp.py --gui

Ready-made programs (Windows/macOS/Linux): see the README, section "Ready-made programs".
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
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
REPO = "WhisperScript/simple-ytdlp"  # GitHub repo that hosts the releases (used for the update hint)

APP_NAME = "simple-ytdlp"
LEGACY_APP_NAME = "ytdl"     # the app used to be called ytdl
DEFAULT_OUT = Path.home() / "Downloads" / "yt-dlp"

MODES = {
    "video":      "Video - best quality",
    "video2160":  "Video - max. 4K",
    "video1440":  "Video - max. 1440p",
    "video1080":  "Video - max. 1080p",
    "video720":   "Video - max. 720p",
    "video480":   "Video - max. 480p",
    "mp3":        "Audio - mp3",
    "m4a":        "Audio - m4a",
    "opus":       "Audio - opus",
}
AUDIO_MODES = ("mp3", "m4a", "opus")
ALSO_AUDIO_NONE = "None"

NO_BROWSER = "None"
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


def migrate_legacy_data() -> None:
    """Carry settings, history and downloaded tools over from the folder of the old app name (ytdl)."""
    new = data_dir()
    old = new.with_name(LEGACY_APP_NAME)
    try:
        if old.is_dir() and not new.exists():
            old.rename(new)
    except OSError:
        pass                                   # the old folder simply stays unused


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


def _release_asset_name() -> str | None:
    """Name of this platform's file in a release (None = no self-update for this platform)."""
    if os.name == "nt":
        return "simple-ytdlp-windows.zip"
    if sys.platform == "darwin":
        return "simple-ytdlp-macos.zip" if _machine() == "arm64" else "simple-ytdlp-macos-intel.zip"
    return "simple-ytdlp-linux.tar.gz"


def check_app_update() -> dict | None:
    """Info about a newer simple-ytdlp release, or None.

    Returns {"version", "page", "asset_url", "asset_name", "digest"}; asset_url is None when
    the release has no file for this platform. Silent on any failure (offline, private repo,
    rate limit) - it is only a hint.
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
        if _version_tuple(latest) <= _version_tuple(__version__):
            return None
        wanted = _release_asset_name()
        asset = next((a for a in data.get("assets", []) if a.get("name") == wanted), None)
        url = str(asset["browser_download_url"]) if asset else None
        if url and not url.startswith(f"https://github.com/{REPO}/releases/download/"):
            url = None                          # only ever fetch from our own releases
        return {"version": latest.lstrip("v"), "asset_name": wanted, "asset_url": url,
                "digest": str(asset.get("digest") or "") if asset else "",
                "page": str(data.get("html_url") or f"https://github.com/{REPO}/releases")}
    except Exception:
        return None


def can_self_update() -> bool:
    """Self-update only makes sense for the packaged program, not for `uv run simple-ytdlp.py`."""
    return bool(getattr(sys, "frozen", False)) and _release_asset_name() is not None


def _relaunch_env() -> dict:
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # the new process must not reuse this one's temp folder
    return env


def cleanup_old_versions() -> None:
    """Remove the leftovers of a previous self-update (the renamed old program)."""
    if not getattr(sys, "frozen", False):
        return
    exe = Path(sys.executable)
    target = exe.parents[2] if sys.platform == "darwin" else exe
    old = target.with_name(target.name + ".old")
    try:
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)
    except OSError:
        pass                                   # still locked -> next start


def install_app_update(update: dict, log=print, *, exe: Path | None = None, start_new: bool = True) -> bool:
    """Download the new release, swap it in for the running program and start it.

    Returns True when the new version has been started (the caller should then quit).
    The running program is renamed to '<name>.old' first - Windows locks a running .exe against
    overwriting but allows renaming it - and removed by the new version on its next start.
    """
    if not update.get("asset_url"):
        log("No download for this platform in the release.")
        return False
    exe = Path(exe or sys.executable)
    work = data_dir() / "update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / update["asset_name"]
    try:
        log(f"Downloading simple-ytdlp v{update['version']} ...")
        _fetch(update["asset_url"], archive, log)
        digest = update.get("digest", "")
        if digest.startswith("sha256:"):
            import hashlib
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest.split(":", 1)[1]:
                log("ERROR: the downloaded file does not match its checksum - update aborted.")
                return False

        if os.name == "nt":
            with zipfile.ZipFile(archive) as zf:
                zf.extract("simple-ytdlp.exe", work)
            new, target = work / "simple-ytdlp.exe", exe
            launch = [str(target)]
        elif sys.platform == "darwin":
            subprocess.run(["ditto", "-x", "-k", str(archive), str(work)], check=True)   # keeps permissions
            new, target = work / "simple-ytdlp.app", exe.parents[2]                      # .../simple-ytdlp.app
            launch = ["open", "-n", str(target)]
        else:
            import tarfile
            new = work / "simple-ytdlp"
            with tarfile.open(archive) as tf, tf.extractfile("simple-ytdlp") as src, new.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            new.chmod(0o755)
            target = exe
            launch = [str(target)]

        old = target.with_name(target.name + ".old")
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)
        target.rename(old)
        try:
            shutil.move(str(new), str(target))
        except Exception:
            old.rename(target)                 # put the working version back
            raise
        if os.name != "nt":
            try:
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
            except OSError:
                pass
        shutil.rmtree(work, ignore_errors=True)
    except Exception as e:
        log(f"ERROR: update failed: {e}")
        return False

    if start_new:
        extra = ({"creationflags": 0x00000008 | 0x00000200} if os.name == "nt"      # DETACHED | NEW_GROUP
                 else {"start_new_session": True})
        subprocess.Popen(launch, env=_relaunch_env(), close_fds=True, **extra)
    log("Update installed - restarting ...")
    return True


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
               no_playlist=False, also_audio="", limit_rate="", sponsorblock=False,
               sub_langs="en,de", format_override="", section="", exact_cut=False,
               embed_chapters=False, split_chapters=False, proxy="", name_template="",
               extra: list[str] | None = None) -> list[str]:
    out_dir = Path(out_dir).expanduser()
    name = name_template.strip() or "%(title)s"
    if "%(ext)s" not in name:
        name += ".%(ext)s"
    tmpl = ("%(uploader)s/" if sort_by_uploader else "") + name

    args = [
        "-o", str(out_dir / tmpl),
        "--no-overwrites",
        "--retries", "3",
        "--embed-metadata",
        "--newline",              # one progress line per update -> works well in the log
        "--progress",
    ]

    if format_override:
        args += ["-f", format_override]
    elif mode not in AUDIO_MODES:
        m = re.fullmatch(r"video(\d+)", mode)
        if m:
            h = m.group(1)
            args += ["-f", f"bv*[height<={h}]+ba/b[height<={h}]/b"]
        else:
            args += ["-f", "bv*+ba/b"]
    if mode in AUDIO_MODES:
        args += ["-x", "--audio-format", mode, "--audio-quality", "0"]

    if also_audio and mode not in AUDIO_MODES:   # keep the video AND save a separate audio file
        args += ["-x", "--audio-format", also_audio, "--audio-quality", "0", "-k"]

    if subs and mode not in AUDIO_MODES:      # subtitles cannot be embedded in audio files
        langs = sub_langs if re.fullmatch(r"[A-Za-z0-9_.*,-]+", sub_langs or "") else "en,de"
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", langs, "--embed-subs",
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
    if limit_rate:
        args += ["--limit-rate", limit_rate]
    if sponsorblock:
        args += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]
    if section:
        args += ["--download-sections", section]
        if exact_cut:
            args += ["--force-keyframes-at-cuts"]
    if embed_chapters:
        args += ["--embed-chapters"]
    if split_chapters:
        args += ["--split-chapters"]
    if proxy:
        args += ["--proxy", proxy]
    if extra:
        args += extra
    return args


def parse_clock(text: str) -> float | None:
    """Seconds from '90', '1:30', '01:02:03' or '1:02.5'; None if it is not a time."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", text):
        return None
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def section_arg(start: str, end: str) -> tuple[str, str]:
    """(--download-sections value, error) for a start/end pair; both may be empty (= no cut)."""
    if not start.strip() and not end.strip():
        return "", ""
    a = parse_clock(start) if start.strip() else 0.0
    b = parse_clock(end) if end.strip() else None
    if a is None or (end.strip() and b is None):
        return "", "Times look like 90, 1:30 or 01:02:03."
    if b is not None and b <= a:
        return "", "The end must be after the start."
    return f"*{fmt_clock(a)}-{fmt_clock(b) if b is not None else 'inf'}", ""


def fmt_clock(seconds: float) -> str:
    whole = int(seconds)
    frac = seconds - whole
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02d}:{s:02d}"
    return text + (f"{frac:.2f}"[1:].rstrip("0") if frac else "")


FRIENDLY_ERRORS = [
    (r"confirm your age|age-restricted|age restricted",
     "Age-restricted - pick your browser under Options > Cookies from"),
    (r"private video|this video is private",
     "Private video - sign in in your browser and pick it under Options > Cookies from"),
    (r"not a bot|sign in to confirm",
     "YouTube wants a login - use Options > Cookies from, or update yt-dlp"),
    (r"http error 429|too many requests",
     "Rate limited (HTTP 429) - wait a few minutes, lower Parallel, or use cookies"),
    (r"not available in your country|geo.?restrict|blocked it in your country",
     "Not available in your country"),
    (r"video unavailable|has been removed|no longer available|does not exist",
     "Video unavailable (removed, private or blocked)"),
    (r"unsupported url", "This link is not supported by yt-dlp"),
    (r"requested format is not available", "That format is not offered for this video - choose another"),
    (r"ffmpeg.*(not found|not installed)|ffprobe.*(not found|not installed)", "ffmpeg is missing"),
    (r"premieres in|live event will begin|will begin in", "The live stream or premiere has not started yet"),
    (r"unable to download|getaddrinfo|name resolution|timed out|connection (reset|refused)",
     "Network problem - check your connection"),
]


def friendly_error(text: str) -> str:
    """A short human explanation for a yt-dlp error line (the line itself if nothing matches)."""
    for pattern, message in FRIENDLY_ERRORS:
        if re.search(pattern, text or "", re.I):
            return message
    return text


def fetch_formats(url: str, *, cookies_browser: str = "", timeout: int = 90) -> list[dict] | None:
    """All formats of a video for the manual format picker (None if yt-dlp cannot read the URL)."""
    base = find_ytdlp()
    if base is None:
        return None
    cmd = base + ["--dump-single-json", "--no-warnings", "--skip-download", "--no-playlist"]
    cmd += find_js_runtime() or []
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    try:
        r = subprocess.run(cmd + ["--", url], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **_no_window())
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return parse_formats(json.loads(r.stdout))
    except Exception:
        return None


def parse_formats(data: dict) -> list[dict]:
    """Rows for the format picker, best first: video formats by height, then audio-only by bitrate."""
    rows = []
    for f in data.get("formats") or []:
        if not f.get("format_id") or f.get("vcodec") == f.get("acodec") == "none":
            continue                                   # storyboards etc.
        has_v, has_a = f.get("vcodec") not in (None, "none"), f.get("acodec") not in (None, "none")
        size = f.get("filesize") or f.get("filesize_approx")
        rows.append({
            "id": str(f["format_id"]), "ext": f.get("ext") or "",
            "video": has_v, "audio": has_a,
            "res": (f"{f['width']}x{f['height']}" if f.get("width") and f.get("height")
                    else (f"{f['height']}p" if f.get("height") else ("audio only" if not has_v else ""))),
            "height": f.get("height") or 0, "fps": f.get("fps") or 0,
            "vcodec": (f.get("vcodec") or "").split(".")[0] if has_v else "",
            "acodec": (f.get("acodec") or "").split(".")[0] if has_a else "",
            "size": size or 0, "tbr": f.get("tbr") or f.get("abr") or 0,
            "note": f.get("format_note") or "",
        })
    rows.sort(key=lambda r: (not r["video"], -r["height"], -r["tbr"]))
    return rows


def format_expression(rows: list[dict]) -> str:
    """The yt-dlp -f expression for the rows picked in the format picker."""
    if not rows:
        return ""
    if len(rows) == 1:
        r = rows[0]
        return f"{r['id']}+ba/{r['id']}" if r["video"] and not r["audio"] else r["id"]
    ordered = sorted(rows[:2], key=lambda r: not r["video"])     # video first, then audio
    return "+".join(r["id"] for r in ordered)


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


def prepare_command(args: list[str], log=print) -> tuple[list[str], list[str]] | None:
    """(yt-dlp command prefix, arguments incl. ffmpeg/JS engine), or None if yt-dlp is unavailable."""
    if not ensure_tools(log):
        log("Could not download yt-dlp. Check your internet connection and try again.")
        return None
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
    return base, args


def download_one(base: list[str], args: list[str], url: str, out_dir: Path, log=print,
                 stop_flag=None, clean_intermediates: bool = False) -> int:
    """Download one URL. Returns the yt-dlp exit code (-1 = cancelled). Failed URLs are appended
    to failed.log; clean_intermediates removes the raw stream files that -k leaves behind (only
    files created by this run, so nothing that was already there is touched)."""
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    before = _intermediates(out_dir) if clean_intermediates else set()
    rc = _stream(base + args + ["--", url], log, stop_flag)
    if clean_intermediates:
        for leftover in _intermediates(out_dir) - before:
            try:
                leftover.unlink()
            except OSError:
                pass
    if stop_flag is not None and stop_flag.is_set():
        return -1
    if rc != 0:
        log(f"FAILED ({rc}): {url}")
        with (out_dir / "failed.log").open("a", encoding="utf-8") as fh:
            fh.write(url + "\n")
    return rc


def download(urls: list[str], args: list[str], out_dir: Path, log=print, stop_flag=None,
             clean_intermediates: bool = False) -> tuple[int, int]:
    """Download every URL one after another (CLI). Returns (succeeded, failed)."""
    prepared = prepare_command(args, log)
    if prepared is None:
        return (0, len(urls))
    base, args = prepared
    out_dir = Path(out_dir).expanduser()

    ok = bad = 0
    for i, url in enumerate(urls, 1):
        if stop_flag is not None and stop_flag.is_set():
            log("-- cancelled --")
            break
        log(f"\n[{i}/{len(urls)}] {url}")
        rc = download_one(base, args, url, out_dir, log, stop_flag, clean_intermediates)
        if rc == -1:
            break
        if rc == 0:
            ok += 1
        else:
            bad += 1

    log(f"\nDone. {ok} succeeded, {bad} failed.")
    if bad:
        log(f"List of failures: {out_dir / 'failed.log'}")
    return (ok, bad)


# ---------------------------------------------------------------- Metadata, progress, history

URL_RE = re.compile(r"https?://[^\s<>\"']+")


def extract_urls(text: str) -> list[str]:
    """All http(s) URLs in a text, in order, without duplicates."""
    seen: dict[str, None] = {}
    for u in URL_RE.findall(text):
        seen.setdefault(u.rstrip(".,;)"), None)
    return list(seen)


def fmt_duration(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _entry_url(entry: dict) -> str:
    url = entry.get("webpage_url") or entry.get("url") or ""
    if url.startswith("http"):
        return url
    if entry.get("id") and str(entry.get("ie_key", "")).lower().startswith("youtube"):
        return "https://www.youtube.com/watch?v=" + entry["id"]
    return ""


def parse_info(data: dict) -> dict:
    """Reduce yt-dlp's JSON to what the GUI needs (see fetch_info)."""
    thumb = data.get("thumbnail") or ""
    if not thumb and data.get("thumbnails"):
        thumb = (data["thumbnails"][-1] or {}).get("url", "")
    info = {"title": data.get("title") or "", "uploader": data.get("uploader") or data.get("channel") or "",
            "duration": data.get("duration"), "thumbnail": thumb, "is_playlist": False, "entries": []}
    if data.get("_type") == "playlist" or "entries" in data:
        info["is_playlist"] = True
        for e in data.get("entries") or []:
            if not e:
                continue
            url = _entry_url(e)
            if url:
                thumb = e.get("thumbnail") or ((e.get("thumbnails") or [{}])[-1] or {}).get("url", "")
                if not thumb and e.get("id") and str(e.get("ie_key", "")).lower().startswith("youtube"):
                    thumb = f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
                info["entries"].append({"title": e.get("title") or url, "url": url,
                                        "duration": e.get("duration"), "thumbnail": thumb,
                                        "uploader": e.get("uploader") or e.get("channel") or ""})
    return info


def fetch_info(url: str, *, no_playlist: bool = False, cookies_browser: str = "",
               timeout: int = 90) -> dict | None:
    """Title, channel, duration, thumbnail URL (and the entries of a playlist) for a URL,
    without downloading anything. None if yt-dlp cannot read the URL."""
    base = find_ytdlp()
    if base is None:
        return None
    cmd = base + ["--dump-single-json", "--flat-playlist", "--no-warnings", "--skip-download"]
    cmd += find_js_runtime() or []
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    if no_playlist:
        cmd += ["--no-playlist"]
    try:
        r = subprocess.run(cmd + ["--", url], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **_no_window())
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return parse_info(json.loads(r.stdout))
    except Exception:
        return None


PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%(?:\s+of\s+~?\s*(?P<size>\S+))?(?:\s+in\s+\S+)?"
    r"(?:\s+at\s+(?P<speed>\S+))?(?:\s+ETA\s+(?P<eta>\S+))?")
ITEM_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s")
DEST_RE = re.compile(r'^\[download\] Destination: (?P<path>.+)$')
MERGE_RE = re.compile(r'^\[Merger\] Merging formats into "(?P<path>.+)"$')
EXTRACT_RE = re.compile(r'^\[ExtractAudio\] Destination: (?P<path>.+)$')
EXISTS_RE = re.compile(r'^\[download\] (?P<path>.+) has already been downloaded$')
ARCHIVED_RE = re.compile(r"has already been recorded in the archive")


def parse_progress(line: str) -> dict | None:
    """{"pct", "size", "speed", "eta"} from a yt-dlp progress line (missing parts are ""), or None."""
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    return {"pct": float(m.group("pct")), "size": m.group("size") or "",
            "speed": m.group("speed") or "", "eta": m.group("eta") or ""}


def track_output_file(found: dict, line: str, audio_mode: bool) -> None:
    """Remember which file a run produced (found is updated in place; read it with output_file)."""
    line = line.strip()
    for key, rx in (("merge", MERGE_RE), ("extract", EXTRACT_RE), ("dest", DEST_RE), ("dest", EXISTS_RE)):
        m = rx.match(line)
        if m:
            path = m.group("path")
            if key == "extract" and not audio_mode:
                return                           # the extra audio copy is not "the" result
            if key == "dest" and INTERMEDIATE_RE.search(path):
                return
            found[key] = path
            return


def output_file(found: dict) -> str:
    return found.get("merge") or found.get("extract") or found.get("dest") or ""


QUEUE_FILE = data_dir() / "queue.json"


def load_queue() -> list[dict]:
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict) and d.get("url")] if isinstance(data, list) else []
    except Exception:
        return []


def save_queue(entries: list[dict]) -> None:
    try:
        if entries:
            QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            QUEUE_FILE.write_text(json.dumps(entries, indent=1), encoding="utf-8")
        else:
            QUEUE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def overrides_summary(o: dict) -> str:
    """Short text for the changes a queue item has compared to the main-window settings."""
    parts = []
    if o.get("mode") in MODES:
        parts.append(MODES[o["mode"]].split(" - ", 1)[-1])
    if o.get("format"):
        parts.append("format " + o["format"])
    if o.get("section"):
        parts.append("cut " + o["section"].lstrip("*").replace("-inf", "-end"))
    if o.get("chapters"):
        parts.append("chapters")
    if o.get("split"):
        parts.append("split chapters")
    if o.get("extra"):
        parts.append("custom arguments")
    return "  ·  ".join(parts)


HISTORY_FILE = data_dir() / "history.json"
HISTORY_MAX = 500


def load_history() -> list[dict]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(entries: list[dict]) -> None:
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(entries[-HISTORY_MAX:], indent=1), encoding="utf-8")
    except Exception:
        pass


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


def open_path(path: Path) -> None:
    """Open a file with its default program (a folder is opened in the file manager)."""
    path = Path(path).expanduser()
    if os.name == "nt":
        os.startfile(str(path))               # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_file(path: Path) -> None:
    """Show a file selected in the file manager (falls back to opening its folder)."""
    path = Path(path).expanduser()
    if not path.exists():
        open_folder(path.parent)
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


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


try:
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk, filedialog, messagebox
except ImportError:                            # checked again in run_gui() with a helpful message
    tk = None

try:                                           # thumbnails; without Pillow the cards just show a placeholder
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

try:                                           # modern theme; without sv-ttk the default Tk look stays
    import sv_ttk
except ImportError:
    sv_ttk = None

THUMB_SIZE = (128, 72)

# Status colours that stay readable on both the light and the dark theme.
COLORS = {
    "light": {"ok": "#1e8e4e", "bad": "#c93030", "muted": "#6b6b6b", "thumb": "#dcdcdc",
              "text_bg": "#ffffff", "text_fg": "#1a1a1a", "text_border": "#c8c8c8", "accent": "#0067c0"},
    "dark":  {"ok": "#5fd38d", "bad": "#ff7b7b", "muted": "#a0a0a0", "thumb": "#3a3a3a",
              "text_bg": "#2b2b2b", "text_fg": "#e6e6e6", "text_border": "#4a4a4a", "accent": "#60cdff"},
}

STATUS_ORDER = ("downloading", "queued", "fetching", "failed", "cancelled", "done", "skipped")


def shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def fmt_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num < 1024 or unit == "GiB":
            return f"{num:.0f} B" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return ""


def mode_kind(mode: str) -> str:
    return "audio" if mode in AUDIO_MODES else "video"


def fetch_thumbnail(url: str):
    """Download a thumbnail and crop it to THUMB_SIZE (a PIL image), or None."""
    if Image is None or not url:
        return None
    try:
        import io
        req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            img = Image.open(io.BytesIO(r.read(4_000_000))).convert("RGB")
        w, h = THUMB_SIZE
        scale = max(w / img.width, h / img.height)
        img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))))
        left, top = (img.width - w) // 2, (img.height - h) // 2
        return img.crop((left, top, left + w, top + h))
    except Exception:
        return None


class Item:
    """One download in the queue."""
    _counter = 0

    def __init__(self, url: str):
        Item._counter += 1
        self.id = Item._counter
        self.url = url
        self.title = url
        self.uploader = ""
        self.duration = None
        self.thumb = None                      # PIL image, set by the info thread
        self.status = "fetching"               # fetching queued downloading done skipped failed cancelled
        self.pct = 0.0
        self.speed = self.eta = self.size = ""
        self.path = ""
        self.error = ""
        self.mode = ""
        self.thumb_url = ""
        self.overrides: dict = {}              # per-item changes, see ItemDialog
        self.stop = threading.Event()
        self.removed = False
        self.card: "Card | None" = None

    @property
    def active(self) -> bool:
        return self.status in ("fetching", "queued", "downloading")

    @property
    def finished(self) -> bool:
        return self.status in ("done", "skipped", "failed", "cancelled")


class Card:
    """The row of one Item in the queue: thumbnail, title, progress and action buttons."""

    def __init__(self, app: "App", item: Item):
        self.app, self.item = app, item
        self._buttons_for = None
        self.photo = None
        card_style = "Card.TFrame" if sv_ttk is not None else "TFrame"
        self.frame = ttk.Frame(app.list_inner, style=card_style, padding=10)
        self.frame.columnconfigure(1, weight=1)

        self.thumb_box = tk.Frame(self.frame, width=THUMB_SIZE[0], height=THUMB_SIZE[1])
        self.thumb_box.grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, 12))
        self.thumb_box.grid_propagate(False)
        self.thumb_box.pack_propagate(False)
        self.thumb_lbl = tk.Label(self.thumb_box, text="▶", borderwidth=0)
        self.thumb_lbl.pack(fill="both", expand=True)

        self.title_lbl = ttk.Label(self.frame, style="CardTitle.TLabel", anchor="w")
        self.title_lbl.grid(row=0, column=1, sticky="ew")
        self.meta_lbl = ttk.Label(self.frame, style="Muted.TLabel", anchor="w")
        self.meta_lbl.grid(row=1, column=1, sticky="ew", pady=(1, 6))

        self.bar = ttk.Progressbar(self.frame, maximum=100)
        self.bar.grid(row=2, column=1, sticky="ew", pady=(0, 4))
        self.status_lbl = ttk.Label(self.frame, anchor="w")
        self.status_lbl.grid(row=3, column=1, sticky="ew")

        self.btn_box = ttk.Frame(self.frame)
        self.btn_box.grid(row=0, column=2, rowspan=4, sticky="ne", padx=(12, 0))
        self._bind_menu(self.frame)
        self.refresh()

    def _bind_menu(self, widget) -> None:
        """Right click opens the item menu, double click the item options (or the finished file)."""
        secondary = ("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ("<Button-3>",)
        for seq in secondary:
            widget.bind(seq, lambda e: self.app.card_menu(self.item, e))
        widget.bind("<Double-Button-1>", lambda e: self.app.card_activate(self.item))
        for child in widget.winfo_children():
            if not isinstance(child, ttk.Button):
                self._bind_menu(child)

    def _set_buttons(self, specs) -> None:
        for child in self.btn_box.winfo_children():
            child.destroy()
        for text, command in specs:
            ttk.Button(self.btn_box, text=text, command=command, width=8).pack(pady=(0, 4), anchor="e")

    def refresh(self) -> None:
        it, app = self.item, self.app
        colors = COLORS[app.theme]
        self.title_lbl.configure(text=shorten(it.title, 110))
        meta = "  ·  ".join(x for x in (it.uploader, fmt_duration(it.duration),
                                         ("⚙ " + overrides_summary(it.overrides)) if it.overrides else "") if x)
        self.meta_lbl.configure(text=meta or (it.url if it.title != it.url else ""))
        self.thumb_box.configure(background=colors["thumb"])
        self.thumb_lbl.configure(background=colors["thumb"], foreground=colors["muted"])

        st = it.status
        if st == "fetching":
            text, color = "Loading info …", colors["muted"]
        elif st == "queued":
            text, color = "Waiting", colors["muted"]
        elif st == "downloading":
            parts = [f"{it.pct:.0f}%"]
            if it.size:
                parts.append(it.size)
            if it.speed and it.speed != "Unknown":
                parts.append(it.speed)
            if it.eta and it.eta != "Unknown":
                parts.append(f"ETA {it.eta}")
            text, color = "   ·   ".join(parts), colors["accent"]
            if it.pct >= 100:
                text, color = "Processing …", colors["accent"]
        elif st == "done":
            text, color = "✓ Done" + (f"   ·   {it.size}" if it.size else ""), colors["ok"]
        elif st == "skipped":
            text, color = "✓ Already downloaded", colors["ok"]
        elif st == "failed":
            text, color = ("✗ Failed" + (f"   ·   {shorten(friendly_error(it.error), 90)}" if it.error else ""),
                           colors["bad"])
        else:
            text, color = "Cancelled", colors["muted"]
        self.status_lbl.configure(text=text, foreground=color)

        if st == "fetching":
            if str(self.bar.cget("mode")) != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(15)
            self.bar.grid()
        elif st in ("queued", "downloading"):
            if str(self.bar.cget("mode")) == "indeterminate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
            self.bar["value"] = it.pct
            self.bar.grid()
        else:
            self.bar.stop()
            self.bar.grid_remove()

        if self._buttons_for != st:
            self._buttons_for = st
            remove = ("Remove", lambda: app.remove_item(it))
            if st == "downloading":
                self._set_buttons([("Cancel", lambda: app.cancel_item(it))])
            elif st in ("done", "skipped"):
                self._set_buttons([("Show", lambda: app.show_item(it)), remove])
            elif st in ("failed", "cancelled"):
                self._set_buttons([("Retry", lambda: app.retry_item(it)),
                                   ("Options", lambda: app.open_item_options(it)), remove])
            else:
                self._set_buttons([("Options", lambda: app.open_item_options(it)), remove])

    def set_thumb(self) -> None:
        if ImageTk is None or self.item.thumb is None:
            return
        self.photo = ImageTk.PhotoImage(self.item.thumb)
        self.thumb_lbl.configure(image=self.photo, text="")


class PlaylistDialog(tk.Toplevel if tk else object):
    """Pick the videos of a playlist. After it closes, .result is the chosen entries (or None)."""

    def __init__(self, parent, title: str, entries: list[dict], colors: dict):
        super().__init__(parent)
        self.result = None
        self.entries = entries
        self.checked = [True] * len(entries)
        self.title("Select videos")
        self.geometry("620x520")
        self.minsize(460, 320)
        self.transient(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        head = ttk.Frame(self, padding=(14, 12, 14, 4))
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=shorten(title or "Playlist", 70), style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(head, text=f"{len(entries)} videos - click a row to (de)select it",
                  style="Muted.TLabel").pack(anchor="w")

        self.tree = ttk.Treeview(self, show="tree", selectmode="none")
        self.tree.column("#0", width=520, stretch=True)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=6)
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns", padx=(0, 14), pady=6)
        self.tree.configure(yscrollcommand=sb.set)
        for i in range(len(entries)):
            self.tree.insert("", "end", iid=str(i))
            self._render(i)
        self.tree.bind("<Button-1>", self._toggle)

        foot = ttk.Frame(self, padding=(14, 4, 14, 12))
        foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(foot, text="All", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(foot, text="None", command=lambda: self._set_all(False)).pack(side="left", padx=6)
        self.add_btn = ttk.Button(foot, command=self._accept,
                                  style="Accent.TButton" if sv_ttk is not None else "TButton")
        self.add_btn.pack(side="right")
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        self._update_count()
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()                # centre over the main window
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.focus_set()

    def _render(self, i: int) -> None:
        e = self.entries[i]
        dur = fmt_duration(e.get("duration"))
        box = "☑" if self.checked[i] else "☐"
        self.tree.item(str(i), text=f"  {box}   {i + 1:>3}.  {shorten(e['title'], 58)}" + (f"   ({dur})" if dur else ""))

    def _toggle(self, event) -> str:
        row = self.tree.identify_row(event.y)
        if row:
            i = int(row)
            self.checked[i] = not self.checked[i]
            self._render(i)
            self._update_count()
        return "break"

    def _set_all(self, value: bool) -> None:
        self.checked = [value] * len(self.entries)
        for i in range(len(self.entries)):
            self._render(i)
        self._update_count()

    def _update_count(self) -> None:
        n = sum(self.checked)
        self.add_btn.configure(text=f"Add {n} to queue", state="normal" if n else "disabled")

    def _accept(self) -> None:
        self.result = [e for e, c in zip(self.entries, self.checked) if c]
        self.destroy()


class ItemDialog(tk.Toplevel if tk else object):
    """Options of one queue item: format, time range, chapters and extra yt-dlp arguments.
    They are stored in item.overrides and win over the settings in the main window."""

    DEFAULT_MODE = "Default (as chosen in the main window)"

    def __init__(self, app: "App", item: Item):
        super().__init__(app.root)
        self.app, self.item = app, item
        o = item.overrides
        self.picked_format = o.get("format", "")
        self.rows: list[dict] = []
        self.title("Download options")
        self.transient(app.root)
        self.columnconfigure(0, weight=1)
        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text=shorten(item.title, 70), style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w")
        hint = "  ·  ".join(x for x in (item.uploader, fmt_duration(item.duration)) if x)
        ttk.Label(body, text=hint, style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w",
                                                              pady=(0, 12))

        ttk.Label(body, text="Download as").grid(row=2, column=0, sticky="w", pady=4)
        self.mode_var = tk.StringVar(value=MODES.get(o.get("mode", ""), self.DEFAULT_MODE))
        ttk.Combobox(body, textvariable=self.mode_var, state="readonly", width=28,
                     values=[self.DEFAULT_MODE, *MODES.values()]).grid(row=2, column=1, columnspan=3, sticky="w")

        ttk.Label(body, text="Exact format").grid(row=3, column=0, sticky="w", pady=4)
        self.format_lbl = ttk.Label(body, style="Muted.TLabel")
        self.format_lbl.grid(row=3, column=1, sticky="w")
        self.pick_btn = ttk.Button(body, text="Choose …", command=self.load_formats)
        self.pick_btn.grid(row=3, column=2, padx=(8, 4))
        ttk.Button(body, text="Reset", command=self.reset_format).grid(row=3, column=3)

        self.picker = ttk.Frame(body)               # the format list, shown on demand
        self.picker.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(4, 8))
        self.picker.columnconfigure(0, weight=1)
        cols = (("id", "ID", 60), ("res", "Resolution", 100), ("ext", "Type", 50), ("codec", "Codec", 130),
                ("size", "Size", 80), ("note", "Note", 130))
        self.tree = ttk.Treeview(self.picker, columns=[c[0] for c in cols], show="headings", height=8,
                                 selectmode="extended")
        for key, text, width in cols:
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, anchor="w", stretch=key in ("note", "codec"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self.picker, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)
        self.picker_msg = ttk.Label(self.picker, style="Muted.TLabel",
                                    text="Pick one row, or a video-only row plus an audio-only row.")
        self.picker_msg.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.picker.grid_remove()

        ttk.Label(body, text="Only this part").grid(row=5, column=0, sticky="w", pady=4)
        cut = ttk.Frame(body)
        cut.grid(row=5, column=1, columnspan=3, sticky="w")
        self.start_var = tk.StringVar(value=o.get("start", ""))
        self.end_var = tk.StringVar(value=o.get("end", ""))
        ttk.Entry(cut, textvariable=self.start_var, width=9).pack(side="left")
        ttk.Label(cut, text="  to  ").pack(side="left")
        ttk.Entry(cut, textvariable=self.end_var, width=9).pack(side="left")
        self.exact_var = tk.BooleanVar(value=o.get("exact", False))
        ttk.Checkbutton(cut, text="Exact cut (slower, re-encodes)", variable=self.exact_var).pack(side="left", padx=14)
        ttk.Label(body, text="Times like 90, 1:30 or 01:02:03; leave the end empty for 'until the end'.",
                  style="Muted.TLabel").grid(row=6, column=1, columnspan=3, sticky="w")

        ttk.Label(body, text="Chapters").grid(row=7, column=0, sticky="w", pady=(10, 4))
        chap = ttk.Frame(body)
        chap.grid(row=7, column=1, columnspan=3, sticky="w", pady=(10, 0))
        self.chap_var = tk.BooleanVar(value=o.get("chapters", app.chapters_var.get()))
        self.split_var = tk.BooleanVar(value=o.get("split", False))
        ttk.Checkbutton(chap, text="Embed chapter markers", variable=self.chap_var).pack(side="left")
        ttk.Checkbutton(chap, text="One file per chapter", variable=self.split_var).pack(side="left", padx=14)

        ttk.Label(body, text="Extra arguments").grid(row=8, column=0, sticky="w", pady=4)
        self.extra_var = tk.StringVar(value=o.get("extra", ""))
        ttk.Entry(body, textvariable=self.extra_var).grid(row=8, column=1, columnspan=3, sticky="ew")
        ttk.Label(body, text="Passed to yt-dlp for this item only, e.g. --write-info-json",
                  style="Muted.TLabel").grid(row=9, column=1, columnspan=3, sticky="w")

        foot = ttk.Frame(body)
        foot.grid(row=10, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        ttk.Button(foot, text="Apply to all waiting", command=lambda: self.accept(all_waiting=True)).pack(side="left")
        accent = "Accent.TButton" if sv_ttk is not None else "TButton"
        ttk.Button(foot, text="OK", style=accent, command=self.accept).pack(side="right")
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right", padx=8)

        self._show_format()
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        y = app.root.winfo_rooty() + max((app.root.winfo_height() - self.winfo_height()) // 3, 0)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()

    # -- format picker

    def _show_format(self) -> None:
        self.format_lbl.configure(text=self.picked_format or "Automatic")

    def reset_format(self) -> None:
        self.picked_format = ""
        self.tree.selection_set(())
        self._show_format()

    def load_formats(self) -> None:
        self.picker.grid()
        if self.rows:
            return
        self.pick_btn.configure(state="disabled")
        self.picker_msg.configure(text="Loading formats …")
        cookies = self.app.cookies()

        def work():
            rows = fetch_formats(self.item.url, cookies_browser=cookies)
            self.app.ui(lambda: self._formats_loaded(rows))

        threading.Thread(target=work, daemon=True).start()

    def _formats_loaded(self, rows: list[dict] | None) -> None:
        if not self.winfo_exists():
            return
        self.pick_btn.configure(state="normal")
        if not rows:
            self.picker_msg.configure(text="Could not read the formats of this link.")
            return
        self.rows = rows
        for r in rows:
            codec = "/".join(x for x in (r["vcodec"], r["acodec"]) if x)
            kind = "" if r["video"] and r["audio"] else ("video only" if r["video"] else "")
            note = "  ".join(x for x in (kind, r["note"], f"{r['fps']:g} fps" if r["fps"] > 30 else "") if x)
            self.tree.insert("", "end", iid=r["id"], values=(
                r["id"], r["res"], r["ext"], codec, fmt_size(r["size"]) if r["size"] else "", note))
        self.picker_msg.configure(text="Pick one row, or a video-only row plus an audio-only row.")

    def _on_pick(self, _event=None) -> None:
        chosen = list(self.tree.selection())
        if len(chosen) > 2:                              # at most video + audio
            self.tree.selection_set(chosen[-2:])
            return
        by_id = {r["id"]: r for r in self.rows}
        self.picked_format = format_expression([by_id[i] for i in chosen if i in by_id])
        self._show_format()

    # -- result

    def accept(self, all_waiting: bool = False) -> None:
        section, error = section_arg(self.start_var.get(), self.end_var.get())
        if error:
            messagebox.showwarning("Only this part", error, parent=self)
            return
        extra = self.extra_var.get().strip()
        try:
            shlex.split(extra)
        except ValueError:
            messagebox.showwarning("Extra arguments", "Unbalanced quotes in the extra arguments.", parent=self)
            return
        mode = next((k for k, v in MODES.items() if v == self.mode_var.get()), "")
        result = {"mode": mode, "format": self.picked_format, "section": section,
                  "start": self.start_var.get().strip(), "end": self.end_var.get().strip(),
                  "exact": self.exact_var.get(), "extra": extra, "split": self.split_var.get()}
        result = {k: v for k, v in result.items() if v}
        if self.chap_var.get() != self.app.chapters_var.get():         # only a difference is an override
            result["chapters"] = self.chap_var.get()
        self.item.overrides = result
        if all_waiting:
            for other in self.app.items:
                if other is not self.item and other.status == "queued":
                    other.overrides = dict(result)
                    other.card.refresh()
        self.destroy()


class App:
    """The main window: link input, download queue, history and log."""

    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.items: list[Item] = []
        self.running = False                   # True after "Download all" until the queue is empty
        self.tools_busy = False                # yt-dlp is being installed/updated
        self.batch = {"ok": 0, "bad": 0}
        self.info_slots = threading.Semaphore(3)
        self.history = load_history()
        self.text_widgets: list = []
        self.restored = False
        self.watch_last = ""
        self.last_clip = ""
        self.placeholder = "Paste a video or playlist link and press Enter"
        self.placeholder_on = False

        self.theme_mode = tk.StringVar(value=self.cfg.get("theme") or "system")    # system | light | dark
        if self.theme_mode.get() not in ("system", "light", "dark"):
            self.theme_mode.set("system")
        self.theme = self._resolve_theme() if sv_ttk is not None else "light"

        root.title(APP_NAME + ("" if __version__ == "dev" else f"  v{__version__}"))
        size = str(self.cfg.get("size", ""))
        root.geometry(size if re.fullmatch(r"\d{3,4}x\d{3,4}", size) else "920x780")
        root.minsize(780, 580)

        self._fonts()
        self._build()
        self._build_menu()
        self.apply_theme(self.theme)
        self.sync_mode_options()
        self.refresh_history()
        self.update_state()

        root.bind("<FocusIn>", self._on_focus)
        root.bind("<<Paste>>", self._on_global_paste)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log((APP_NAME + " " + ("(dev)" if __version__ == "dev" else f"v{__version__}"))
                 + f" ready. Output folder: {self.out_var.get()}")
        if find_ffmpeg()[1] == "none":
            self.log("Note: " + ffmpeg_hint())
        if Image is None:
            self.log("Note: Pillow is not installed - no thumbnails (pip install pillow).")

        self._restore_queue()
        self.root.after(1000, self._watch_clipboard)
        self._startup_tools()
        threading.Thread(target=self._check_app_update, daemon=True).start()

    def ui(self, fn) -> None:
        """Run fn on the Tk thread (called from worker threads; silent once the window is gone)."""
        try:
            self.root.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    # ------------------------------------------------------------ styling

    def _fonts(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        size = abs(int(base.cget("size"))) or 10
        self.font_title = tkfont.Font(family=base.cget("family"), size=size + 1, weight="bold")
        self.font_big = tkfont.Font(family=base.cget("family"), size=size + 8, weight="bold")
        self.font_small = tkfont.Font(family=base.cget("family"), size=max(size - 1, 8))

    def apply_theme(self, name: str) -> None:
        self.theme = name
        if sv_ttk is not None:
            sv_ttk.set_theme(name)
        c = COLORS[name]
        style = ttk.Style()                    # style settings belong to one theme -> set them after switching
        style.configure("CardTitle.TLabel", font=self.font_title)
        style.configure("Big.TLabel", font=self.font_big)
        style.configure("Muted.TLabel", font=self.font_small, foreground=c["muted"])
        for w in self.text_widgets:            # ttk themes do not cover tk.Text
            w.configure(background=c["text_bg"], foreground=c["text_fg"], insertbackground=c["text_fg"],
                        highlightbackground=c["text_border"], highlightcolor=c["accent"])
        bg = self.root.tk.eval("ttk::style lookup TFrame -background") or c["text_bg"]
        self.canvas.configure(background=bg)
        self.empty_lbl.configure(background=bg, foreground=c["muted"])
        for it in self.items:
            if it.card:
                it.card.refresh()
        self._history_tags()

    def _resolve_theme(self) -> str:
        mode = self.theme_mode.get()
        return system_theme() if mode == "system" else mode

    def set_theme_mode(self) -> None:
        """Apply the mode chosen in the View menu (system follows the operating system)."""
        self.apply_theme(self._resolve_theme())
        save_settings({"theme": self.theme_mode.get()})

    def toggle_theme(self) -> None:
        self.theme_mode.set("light" if self.theme == "dark" else "dark")
        self.set_theme_mode()

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        root, cfg = self.root, self.cfg
        accent = "Accent.TButton" if sv_ttk is not None else "TButton"
        switch = "Switch.TCheckbutton" if sv_ttk is not None else "TCheckbutton"
        tool = "Toggle.TButton" if sv_ttk is not None else "TButton"

        main = ttk.Frame(root, padding=(18, 14, 18, 12))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        # header
        head = ttk.Frame(main)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text=APP_NAME, style="Big.TLabel").pack(side="left")
        ttk.Label(head, text="  yt-dlp made comfortable", style="Muted.TLabel").pack(side="left", pady=(8, 0))
        if sv_ttk is not None:
            ttk.Button(head, text="☀ / ☾", width=6, command=self.toggle_theme).pack(side="right")
        self.update_btn = ttk.Button(head, text="Update yt-dlp", command=self.on_update_ytdlp)
        self.update_btn.pack(side="right", padx=8)
        self.head_status = ttk.Label(head, text="", style="Muted.TLabel")
        self.head_status.pack(side="right", padx=8)

        # link input
        add = ttk.Frame(main)
        add.grid(row=1, column=0, sticky="ew", pady=(14, 8))
        add.columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(add, textvariable=self.url_var, font=self.font_title)
        self.url_entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self.url_entry.bind("<Return>", lambda e: self.on_add())
        self.url_entry.bind("<<Paste>>", self._on_entry_paste)
        self.url_entry.bind("<FocusIn>", lambda e: self._placeholder(False))
        self.url_entry.bind("<FocusOut>", lambda e: self._placeholder(True))
        ttk.Button(add, text="Add", style=accent, command=self.on_add, width=8).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(add, text="Import list …", command=self.on_import).grid(row=0, column=2, padx=(8, 0))
        self._placeholder(True)

        # mode / folder / options toggle
        bar = ttk.Frame(main)
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(3, weight=1)
        mode = cfg.get("mode", "video")
        if mode not in MODES:
            mode = "video"
        self.kind_var = tk.StringVar(value=mode_kind(mode))
        kinds = ttk.Frame(bar)
        kinds.grid(row=0, column=0, sticky="w")
        for text, value in (("Video", "video"), ("Audio", "audio")):
            ttk.Radiobutton(kinds, text=text, value=value, variable=self.kind_var, style=tool,
                            command=self._on_kind, width=7).pack(side="left", padx=(0, 4))
        self.quality_var = tk.StringVar(value=MODES[mode])
        self.quality_combo = ttk.Combobox(bar, textvariable=self.quality_var, state="readonly", width=19)
        self.quality_combo.grid(row=0, column=1, padx=8)
        self.quality_combo.bind("<<ComboboxSelected>>", lambda e: self.sync_mode_options())
        also = ttk.Frame(bar)                  # video modes: keep the video AND save a separate audio file
        also.grid(row=0, column=2)
        self.also_lbl = ttk.Label(also, text="+ Audio")
        self.also_lbl.pack(side="left", padx=(0, 6))
        self.also_var = tk.StringVar(value=cfg.get("also_audio") or ALSO_AUDIO_NONE)
        self.also_combo = ttk.Combobox(also, textvariable=self.also_var, values=[ALSO_AUDIO_NONE, *AUDIO_MODES],
                                       state="readonly", width=6)
        self.also_combo.pack(side="left")
        self.out_var = tk.StringVar(value=cfg.get("out") or str(DEFAULT_OUT))
        ttk.Label(bar, text="Save to").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(bar, textvariable=self.out_var).grid(row=1, column=1, columnspan=3, sticky="ew",
                                                       padx=(8, 0), pady=(8, 0))
        ttk.Button(bar, text="Browse …", command=self.pick_dir).grid(row=1, column=4, padx=(8, 0), pady=(8, 0))
        self.opts_open = tk.BooleanVar(value=cfg.get("options_open", False))
        self.opts_btn = ttk.Button(bar, command=self._toggle_options, width=11)
        self.opts_btn.grid(row=0, column=4, padx=(8, 0))

        # collapsible options
        self.opts = ttk.Frame(main, padding=(0, 10, 0, 0))
        self.opts.grid(row=3, column=0, sticky="ew")
        self.subs_var = tk.BooleanVar(value=cfg.get("subs", False))
        self.thumb_var = tk.BooleanVar(value=cfg.get("thumb", False))
        self.arch_var = tk.BooleanVar(value=cfg.get("archive", True))
        self.uploader_var = tk.BooleanVar(value=cfg.get("uploader", False))
        self.single_var = tk.BooleanVar(value=cfg.get("single", True))
        self.sponsor_var = tk.BooleanVar(value=cfg.get("sponsorblock", False))
        self.auto_var = tk.BooleanVar(value=cfg.get("autostart", False))
        self.chapters_var = tk.BooleanVar(value=cfg.get("chapters", False))
        self.clip_watch_var = tk.BooleanVar(value=False)                   # never on at startup
        self.clip_watch_var.trace_add("write", lambda *_: self._clip_watch_toggled())
        switches = (("Subtitles", self.subs_var), ("Embed thumbnail", self.thumb_var),
                    ("Skip already downloaded", self.arch_var), ("Folder per channel", self.uploader_var),
                    ("Single video only (no playlist)", self.single_var),
                    ("Remove sponsor segments", self.sponsor_var),
                    ("Embed chapters", self.chapters_var), ("Start right after adding", self.auto_var),
                    ("Watch the clipboard for links", self.clip_watch_var))
        self.subs_check = None
        for i, (text, var) in enumerate(switches):
            check = ttk.Checkbutton(self.opts, text=text, variable=var, style=switch)
            check.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 22), pady=3)
            if var is self.subs_var:
                self.subs_check = check
        extra = ttk.Frame(self.opts)
        extra.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.sub_langs_var = tk.StringVar(value=cfg.get("sub_langs", "en,de"))
        self.name_var = tk.StringVar(value=cfg.get("name", ""))
        self.proxy_var = tk.StringVar(value=cfg.get("proxy", ""))
        self.args_var = tk.StringVar(value=cfg.get("args", ""))
        self.cookie_var = tk.StringVar(value=cfg.get("cookies") or NO_BROWSER)
        self.limit_var = tk.StringVar(value=cfg.get("limit", ""))
        self.parallel_var = tk.StringVar(value=str(cfg.get("parallel", 2)))
        ttk.Label(extra, text="Cookies from").grid(row=0, column=0, sticky="w")
        ttk.Combobox(extra, textvariable=self.cookie_var, values=[NO_BROWSER, *BROWSERS[1:]],
                     state="readonly", width=10).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(extra, text="Speed limit").grid(row=0, column=2, sticky="w")
        ttk.Entry(extra, textvariable=self.limit_var, width=7).grid(row=0, column=3, padx=(6, 2))
        ttk.Label(extra, text="(e.g. 2M)", style="Muted.TLabel").grid(row=0, column=4, padx=(0, 18))
        ttk.Label(extra, text="Parallel").grid(row=0, column=5, sticky="w")
        ttk.Spinbox(extra, textvariable=self.parallel_var, from_=1, to=4, width=3,
                    state="readonly").grid(row=0, column=6, padx=(6, 0))

        more = ttk.Frame(self.opts)
        more.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        more.columnconfigure(5, weight=1)
        ttk.Label(more, text="Subtitle languages").grid(row=0, column=0, sticky="w")
        ttk.Entry(more, textvariable=self.sub_langs_var, width=10).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(more, text="File name").grid(row=0, column=2, sticky="w")
        ttk.Entry(more, textvariable=self.name_var, width=22).grid(row=0, column=3, padx=(6, 2))
        ttk.Label(more, text="(default %(title)s)", style="Muted.TLabel").grid(row=0, column=4, padx=(0, 18))
        ttk.Label(more, text="Proxy").grid(row=0, column=5, sticky="e")
        ttk.Entry(more, textvariable=self.proxy_var, width=22).grid(row=0, column=6, padx=(6, 0))
        ttk.Label(more, text="Extra yt-dlp arguments").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Entry(more, textvariable=self.args_var).grid(row=1, column=2, columnspan=5, sticky="ew",
                                                         padx=(6, 0), pady=(8, 0))

        prof = ttk.Frame(self.opts)
        prof.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(prof, text="Profile").pack(side="left")
        self.profile_var = tk.StringVar(value="")
        self.profile_combo = ttk.Combobox(prof, textvariable=self.profile_var, width=18,
                                          values=sorted(cfg.get("profiles", {})))
        self.profile_combo.pack(side="left", padx=6)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda e: self.load_profile())
        ttk.Button(prof, text="Save", command=self.save_profile).pack(side="left")
        ttk.Button(prof, text="Delete", command=self.delete_profile).pack(side="left", padx=6)
        ttk.Label(prof, text="Type a name and press Save to keep the current settings under it.",
                  style="Muted.TLabel").pack(side="left", padx=8)
        self._apply_options_visibility()

        # tabs
        self.tabs = ttk.Notebook(main)
        self.tabs.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        self._build_queue_tab()
        self._build_history_tab()
        self._build_log_tab()

        # footer
        foot = ttk.Frame(main)
        foot.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.summary = ttk.Label(foot, text="", style="Muted.TLabel")
        self.summary.pack(side="left")
        self.start_btn = ttk.Button(foot, text="Download all", style=accent, command=self.start_all)
        self.start_btn.pack(side="right")
        self.stop_btn = ttk.Button(foot, text="Stop", command=self.stop_all)
        self.stop_btn.pack(side="right", padx=6)
        ttk.Button(foot, text="Clear finished", command=self.clear_finished).pack(side="right")
        ttk.Button(foot, text="Open folder",
                   command=lambda: open_folder(Path(self.out_var.get() or DEFAULT_OUT))).pack(side="right", padx=6)

        # update banner (only shown when a newer release exists)
        card_style = "Card.TFrame" if sv_ttk is not None else "TFrame"
        self.news = ttk.Frame(main, style=card_style, padding=(12, 8))
        self.news.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.news_label = ttk.Label(self.news, text="")
        self.news_label.pack(side="left")
        self.news_page_btn = ttk.Button(self.news, text="Release page")
        self.news_page_btn.pack(side="right")
        self.news_now_btn = ttk.Button(self.news, text="Update now")
        self.news_now_btn.pack(side="right", padx=6)
        self.news.grid_remove()

    def _build_queue_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        self.tabs.add(tab, text="Queue")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(tab, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=self.canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.canvas.configure(yscrollcommand=vsb.set)
        self.list_inner = ttk.Frame(self.canvas)
        self.list_window = self.canvas.create_window((0, 0), window=self.list_inner, anchor="nw")
        self.list_inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.list_window, width=e.width))
        for widget in (self.canvas, self.list_inner):
            widget.bind("<Enter>", self._bind_wheel)
            widget.bind("<Leave>", self._unbind_wheel)
        self.empty_lbl = tk.Label(
            self.canvas, justify="center", borderwidth=0, font=self.font_title,
            text="Nothing here yet.\n\nPaste a link above (or just press Ctrl+V / Cmd+V anywhere in this window).\n"
                 "Playlists let you pick the videos first.")

    def _build_history_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        self.tabs.add(tab, text="History")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.hist = ttk.Treeview(tab, columns=("title", "mode", "when"), show="headings", selectmode="extended")
        for col, text, width, stretch in (("title", "Title", 480, True), ("mode", "Format", 170, False),
                                          ("when", "Downloaded", 140, False)):
            self.hist.heading(col, text=text, anchor="w")
            self.hist.column(col, width=width, stretch=stretch, anchor="w")
        self.hist.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tab, command=self.hist.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.hist.configure(yscrollcommand=sb.set)
        self.hist.bind("<Double-1>", lambda e: self.hist_open())
        row = ttk.Frame(tab)
        row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(row, text="Open file", command=self.hist_open).pack(side="left")
        ttk.Button(row, text="Show in folder", command=self.hist_reveal).pack(side="left", padx=6)
        ttk.Button(row, text="Download again", command=self.hist_again).pack(side="left")
        ttk.Button(row, text="Clear history", command=self.hist_clear).pack(side="right")
        ttk.Button(row, text="Remove", command=self.hist_remove).pack(side="right", padx=6)

    def _build_log_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=(0, 10, 0, 0))
        self.tabs.add(tab, text="Log")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.log_box = tk.Text(tab, wrap="none", state="disabled", relief="flat", borderwidth=0,
                               highlightthickness=1, padx=8, pady=8, font=self.font_small)
        self.text_widgets.append(self.log_box)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tab, command=self.log_box.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_box.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------ small helpers

    def _bind_wheel(self, _e=None) -> None:
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind_all("<Button-4>", self._on_wheel)
        self.root.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _e=None) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.unbind_all(seq)

    def _on_wheel(self, event) -> None:
        if self.list_inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if event.num == 4:
            step = -2
        elif event.num == 5:
            step = 2
        else:                                  # Windows: multiples of 120, macOS: small numbers
            step = -int(event.delta / 120) * 2 if abs(event.delta) >= 120 else -event.delta
        self.canvas.yview_scroll(step, "units")

    def _placeholder(self, show: bool) -> None:
        """Grey hint text inside the empty link field."""
        if show and not self.url_var.get():
            self.placeholder_on = True
            self.url_var.set(self.placeholder)
            self.url_entry.configure(foreground=COLORS[getattr(self, "theme", "light")]["muted"])
        elif not show and self.placeholder_on:
            self.placeholder_on = False
            self.url_var.set("")
            self.url_entry.configure(foreground="")

    def _toggle_options(self) -> None:
        self.opts_open.set(not self.opts_open.get())
        self._options_changed()

    def _options_changed(self) -> None:
        self._apply_options_visibility()
        save_settings({"options_open": self.opts_open.get()})

    def _apply_options_visibility(self) -> None:
        if self.opts_open.get():
            self.opts.grid()
            self.opts_btn.configure(text="Options ▴")
        else:
            self.opts.grid_remove()
            self.opts_btn.configure(text="Options ▾")

    def _on_kind(self) -> None:
        self.quality_var.set(MODES["mp3" if self.kind_var.get() == "audio" else "video"])
        self.sync_mode_options()

    def mode_key(self) -> str:
        for key, label in MODES.items():
            if label == self.quality_var.get():
                return key
        return "video"

    def sync_mode_options(self, *_) -> None:
        """Offer the qualities of the chosen kind and grey out options without effect in audio mode."""
        kind = self.kind_var.get()
        keys = [k for k in MODES if mode_kind(k) == kind]
        self.quality_combo.configure(values=[MODES[k] for k in keys])
        if self.mode_key() not in keys:
            self.quality_var.set(MODES[keys[0]])
        audio_only = self.mode_key() in AUDIO_MODES
        self.kind_var.set(mode_kind(self.mode_key()))
        self.also_combo.configure(state="disabled" if audio_only else "readonly")
        self.also_lbl.configure(foreground=COLORS[self.theme]["muted"] if audio_only else "")
        self.subs_check.configure(state="disabled" if audio_only else "normal")

    def pick_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.out_var.set(d)

    def log(self, msg: str) -> None:
        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", str(msg) + "\n")
            if int(self.log_box.index("end-1c").split(".")[0]) > 3000:
                self.log_box.delete("1.0", "500.0")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.ui(_append)

    def collect_options(self) -> dict:
        """The download settings of the main window (also what a profile stores)."""
        return {"mode": self.mode_key(), "out": self.out_var.get(), "cookies": self.cookies(),
                "subs": self.subs_var.get(), "thumb": self.thumb_var.get(),
                "archive": self.arch_var.get(), "uploader": self.uploader_var.get(),
                "single": self.single_var.get(), "also_audio": self.also_var.get(),
                "sponsorblock": self.sponsor_var.get(), "chapters": self.chapters_var.get(),
                "sub_langs": self.sub_langs_var.get().strip(), "name": self.name_var.get().strip(),
                "proxy": self.proxy_var.get().strip(), "args": self.args_var.get().strip(),
                "limit": self.limit_var.get().strip()}

    def apply_options(self, d: dict) -> None:
        mode = d.get("mode") if d.get("mode") in MODES else self.mode_key()
        self.kind_var.set(mode_kind(mode))
        self.quality_var.set(MODES[mode])
        self.out_var.set(d.get("out") or self.out_var.get())
        self.cookie_var.set(d.get("cookies") or NO_BROWSER)
        self.also_var.set(d.get("also_audio") or ALSO_AUDIO_NONE)
        for var, key in ((self.subs_var, "subs"), (self.thumb_var, "thumb"), (self.arch_var, "archive"),
                         (self.uploader_var, "uploader"), (self.single_var, "single"),
                         (self.sponsor_var, "sponsorblock"), (self.chapters_var, "chapters")):
            if key in d:
                var.set(bool(d[key]))
        for var, key in ((self.sub_langs_var, "sub_langs"), (self.name_var, "name"),
                         (self.proxy_var, "proxy"), (self.args_var, "args"), (self.limit_var, "limit")):
            if key in d:
                var.set(str(d[key]))
        self.sync_mode_options()

    def save_options(self) -> None:
        save_settings({**self.collect_options(), "autostart": self.auto_var.get(),
                       "parallel": self.parallel(), "theme": self.theme_mode.get()})

    def save_profile(self) -> None:
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showinfo("Profile", "Type a name for the profile first.")
            return
        profiles = dict(load_settings().get("profiles", {}))
        profiles[name] = self.collect_options()
        save_settings({"profiles": profiles})
        self.profile_combo.configure(values=sorted(profiles))
        self.log(f"Profile '{name}' saved.")

    def load_profile(self) -> None:
        d = load_settings().get("profiles", {}).get(self.profile_var.get())
        if d:
            self.apply_options(d)

    def delete_profile(self) -> None:
        name = self.profile_var.get().strip()
        profiles = dict(load_settings().get("profiles", {}))
        if name in profiles and messagebox.askyesno("Profile", f"Delete the profile '{name}'?"):
            del profiles[name]
            save_settings({"profiles": profiles})
            self.profile_combo.configure(values=sorted(profiles))
            self.profile_var.set("")

    def cookies(self) -> str:
        value = self.cookie_var.get()
        return "" if value == NO_BROWSER else value

    def parallel(self) -> int:
        try:
            return min(4, max(1, int(self.parallel_var.get())))
        except ValueError:
            return 1

    def limit_rate(self) -> str:
        value = self.limit_var.get().strip()
        return value if re.fullmatch(r"\d+(\.\d+)?[KkMmGg]?", value) else ""

    # ------------------------------------------------------------ clipboard & input

    def _clipboard(self) -> str:
        try:
            return self.root.clipboard_get()
        except tk.TclError:
            return ""

    def _on_focus(self, event) -> None:
        """Back in the window with a new link in the clipboard: offer it in the empty link field."""
        if event.widget is not self.root:
            return
        clip = self._clipboard().strip()
        if not looks_like_url(clip) or clip == self.last_clip:
            return
        if self.url_var.get() and not self.placeholder_on:
            return
        if any(it.url == clip for it in self.items):
            return
        self.last_clip = clip
        self.placeholder_on = False
        self.url_var.set(clip)
        self.url_entry.configure(foreground="")

    def _on_entry_paste(self, _event) -> str | None:
        urls = extract_urls(self._clipboard())
        if not urls:
            return None                        # not a link: normal text paste
        self.placeholder_on = False
        self.url_entry.configure(foreground="")
        self.url_var.set("")
        self.add_urls(urls)
        return "break"

    def _on_global_paste(self, _event) -> None:
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox, ttk.Spinbox)):
            return
        urls = extract_urls(self._clipboard())
        if urls:
            self.add_urls(urls)

    def on_add(self) -> None:
        text = "" if self.placeholder_on else self.url_var.get()
        urls = extract_urls(text)
        if not urls:
            if text.strip():
                messagebox.showinfo("Add", "That does not look like a link (it must start with http:// or https://).")
            return
        self.url_var.set("")
        self.add_urls(urls)

    def on_import(self) -> None:
        path = filedialog.askopenfilename(title="Text file with one link per line",
                                          filetypes=[("Text", "*.txt *.list"), ("All files", "*.*")])
        if path:
            try:
                self.add_urls(read_url_file(path))
            except OSError as e:
                messagebox.showerror("Import", str(e))

    # ------------------------------------------------------------ queue

    def add_urls(self, urls: list[str]) -> None:
        self.tabs.select(0)
        known = {it.url for it in self.items if it.active}
        no_playlist, cookies = self.single_var.get(), self.cookies()   # Tk variables: main thread only
        for url in urls:
            if url in known:
                continue
            known.add(url)
            item = Item(url)
            self._add_card(item)
            threading.Thread(target=self._info_worker, args=(item, no_playlist, cookies), daemon=True).start()
        self.update_state()

    def _add_card(self, item: Item) -> None:
        self.items.append(item)
        item.card = Card(self, item)
        item.card.frame.pack(fill="x", pady=(0, 8), padx=(0, 4))
        item.card.set_thumb()
        self.update_state()

    def _info_worker(self, item: Item, no_playlist: bool, cookies: str) -> None:
        with self.info_slots:
            if item.removed:
                return
            if find_ytdlp() is None:
                info = None
                for _ in range(60):            # first start: the tools are still being downloaded
                    time.sleep(1)
                    if find_ytdlp() is not None or item.removed:
                        break
                if find_ytdlp() is not None and not item.removed:
                    info = fetch_info(item.url, no_playlist=no_playlist, cookies_browser=cookies)
            else:
                info = fetch_info(item.url, no_playlist=no_playlist, cookies_browser=cookies)
            thumb = fetch_thumbnail(info["thumbnail"]) if info and not info["is_playlist"] else None
        self.ui(lambda: self._info_done(item, info, thumb))

    def _info_done(self, item: Item, info: dict | None, thumb) -> None:
        if item.removed:
            return
        if info and info["is_playlist"] and info["entries"]:
            self._remove_card(item)
            chosen = self._pick_playlist(info)
            if chosen:
                for e in chosen:
                    sub = Item(e["url"])
                    sub.title = e["title"]
                    sub.uploader = e.get("uploader") or info["uploader"]
                    sub.duration = e.get("duration")
                    sub.status = "queued"
                    sub.thumb_url = e.get("thumbnail", "")
                    self._add_card(sub)
                    threading.Thread(target=self._thumb_worker, args=(sub, e.get("thumbnail", "")),
                                     daemon=True).start()
                self.log(f"Playlist '{info['title']}': {len(chosen)} videos added.")
            self.update_state()
            self._autostart()
            return
        if info:
            item.title = info["title"] or item.url
            item.uploader = info["uploader"]
            item.duration = info["duration"]
            item.thumb = thumb
            item.thumb_url = info["thumbnail"]
        else:
            item.uploader = "No preview available - will still try to download"
        item.status = "queued"
        item.card.set_thumb()
        item.card.refresh()
        self.update_state()
        self._autostart()

    def _thumb_worker(self, item: Item, url: str) -> None:
        with self.info_slots:
            img = fetch_thumbnail(url)
        if img is not None:
            def apply():
                if not item.removed and item.card:
                    item.thumb = img
                    item.card.set_thumb()
            self.ui(apply)

    def _pick_playlist(self, info: dict) -> list[dict] | None:
        if len(info["entries"]) == 1:
            return info["entries"]
        dialog = PlaylistDialog(self.root, info["title"], info["entries"], COLORS[self.theme])
        self.root.wait_window(dialog)
        return dialog.result

    def _autostart(self) -> None:
        if self.auto_var.get() and any(it.status == "queued" for it in self.items):
            self.start_all()

    def _remove_card(self, item: Item) -> None:
        item.removed = True
        item.stop.set()
        if item in self.items:
            self.items.remove(item)
        if item.card:
            item.card.frame.destroy()
            item.card = None

    def remove_item(self, item: Item) -> None:
        self._remove_card(item)
        self.update_state()
        self.pump()

    def cancel_item(self, item: Item) -> None:
        item.stop.set()

    def retry_item(self, item: Item) -> None:
        item.stop = threading.Event()
        item.status, item.pct, item.error = "queued", 0.0, ""
        item.speed = item.eta = item.size = ""
        item.card.refresh()
        self.update_state()
        self.start_all()

    def show_item(self, item: Item) -> None:
        if item.path and Path(item.path).exists():
            reveal_file(Path(item.path))
        else:
            open_folder(Path(self.out_var.get() or DEFAULT_OUT))

    def open_item_options(self, item: Item) -> None:
        if item.status == "downloading":
            messagebox.showinfo("Options", "Cancel the download first to change its options.")
            return
        dialog = ItemDialog(self, item)
        self.root.wait_window(dialog)
        if item.card:
            item.card.refresh()
        self.update_state()

    def card_activate(self, item: Item) -> None:
        """Double click: open the finished file, otherwise the options of the item."""
        if item.status in ("done", "skipped"):
            if item.path and Path(item.path).exists():
                open_path(Path(item.path))
        elif item.status != "downloading":
            self.open_item_options(item)

    def copy_link(self, item: Item) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(item.url)

    def card_menu(self, item: Item, event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        st = item.status
        if st in ("queued", "failed", "cancelled"):
            menu.add_command(label="Options …", command=lambda: self.open_item_options(item))
        if st == "downloading":
            menu.add_command(label="Cancel", command=lambda: self.cancel_item(item))
        if st in ("failed", "cancelled"):
            menu.add_command(label="Retry", command=lambda: self.retry_item(item))
        if st in ("done", "skipped"):
            if item.path and Path(item.path).exists():
                menu.add_command(label="Open file", command=lambda: open_path(Path(item.path)))
            menu.add_command(label="Show in folder", command=lambda: self.show_item(item))
        menu.add_separator()
        menu.add_command(label="Copy link", command=lambda: self.copy_link(item))
        menu.add_command(label="Open link in browser", command=lambda: webbrowser.open(item.url))
        if item.error and st == "failed":
            menu.add_command(label="Copy error message", command=lambda: (
                self.root.clipboard_clear(), self.root.clipboard_append(item.error)))
        menu.add_separator()
        menu.add_command(label="Remove", command=lambda: self.remove_item(item))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def clear_finished(self) -> None:
        for item in [it for it in self.items if it.finished]:
            self._remove_card(item)
        self.update_state()

    def start_all(self) -> None:
        if not self.running:
            self.batch = {"ok": 0, "bad": 0}
        self.running = True
        self.save_options()
        self.pump()

    def stop_all(self) -> None:
        self.running = False
        for it in self.items:
            if it.status == "downloading":
                it.stop.set()
        self.update_state()

    def pump(self) -> None:
        """Start waiting downloads while there is a free slot; finish the batch when nothing is left."""
        if self.running and not self.tools_busy:
            active = sum(1 for it in self.items if it.status == "downloading")
            for it in self.items:
                if active >= self.parallel():
                    break
                if it.status == "queued":
                    self._launch(it)
                    active += 1
            if not any(it.active for it in self.items):
                self.running = False
                self._batch_done()
        self.update_state()

    def _batch_done(self) -> None:
        ok, bad = self.batch["ok"], self.batch["bad"]
        if ok or bad:
            summary = f"{ok} succeeded" + (f", {bad} failed" if bad else "")
            self.log("Done. " + summary)
            notify(APP_NAME, "Done: " + summary, self.root)

    def snapshot(self) -> dict:
        return {"mode": self.mode_key(), "out": Path(self.out_var.get() or DEFAULT_OUT).expanduser(),
                "subs": self.subs_var.get(), "thumb": self.thumb_var.get(), "archive": self.arch_var.get(),
                "cookies": self.cookies(), "uploader": self.uploader_var.get(),
                "single": self.single_var.get(), "sponsor": self.sponsor_var.get(),
                "also": "" if self.also_var.get() == ALSO_AUDIO_NONE else self.also_var.get(),
                "limit": self.limit_rate(), "sub_langs": self.sub_langs_var.get().strip() or "en,de",
                "name": self.name_var.get().strip(), "proxy": self.proxy_var.get().strip(),
                "args": self.args_var.get().strip(), "chapters": self.chapters_var.get(),
                "format": "", "section": "", "exact": False, "split": False}

    def _launch(self, item: Item) -> None:
        opts = self.snapshot()
        ov = item.overrides                    # what was changed for this item wins
        for key in ("mode", "format", "section", "exact", "split", "chapters"):
            if key in ov:
                opts[key] = ov[key]
        if ov.get("extra"):
            opts["args"] = (opts["args"] + " " + ov["extra"]).strip()
        item.status, item.pct, item.mode = "downloading", 0.0, opts["mode"]
        item.speed = item.eta = item.size = item.error = ""
        item.card.refresh()
        threading.Thread(target=self._download_worker, args=(item, opts), daemon=True).start()

    def _download_worker(self, item: Item, o: dict) -> None:
        audio = o["mode"] in AUDIO_MODES
        try:
            extra = shlex.split(o["args"])
        except ValueError:
            extra = None
        args = build_args(o["mode"], o["out"], subs=o["subs"], thumb=o["thumb"], archive=o["archive"],
                          cookies_browser=o["cookies"], sort_by_uploader=o["uploader"],
                          no_playlist=o["single"], also_audio=o["also"], limit_rate=o["limit"],
                          sponsorblock=o["sponsor"], sub_langs=o["sub_langs"], format_override=o["format"],
                          section=o["section"], exact_cut=o["exact"], embed_chapters=o["chapters"],
                          split_chapters=o["split"], proxy=o["proxy"], name_template=o["name"],
                          extra=extra)
        found: dict = {}
        state = {"skipped": False, "last": 0.0}

        def refresh_card() -> None:
            if not item.removed and item.card:
                item.card.refresh()

        def on_line(line: str) -> None:
            prog = parse_progress(line)
            if prog:
                item.pct, item.size = prog["pct"], prog["size"] or item.size
                item.speed, item.eta = prog["speed"], prog["eta"]
                now = time.monotonic()
                if now - state["last"] > 0.15 or prog["pct"] >= 100:
                    state["last"] = now
                    self.ui(refresh_card)
                return
            track_output_file(found, line, audio)
            if item.title == item.url and DEST_RE.match(line.strip()):
                item.title = INTERMEDIATE_RE.sub("", Path(DEST_RE.match(line.strip()).group("path")).name)
                self.ui(refresh_card)
            if ARCHIVED_RE.search(line):
                state["skipped"] = True
            if line.startswith("ERROR"):
                item.error = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", line)
            self.log(f"[{shorten(item.title, 28)}] {line}" if line.strip() else "")

        rc = 1
        try:
            prepared = prepare_command(args, self.log) if extra is not None else None
            if extra is None:
                item.error = "Unbalanced quotes in the extra yt-dlp arguments"
            elif prepared is None:
                item.error = "yt-dlp is not available (offline?)"
            else:
                base, args = prepared
                rc = download_one(base, args, item.url, o["out"], on_line, item.stop,
                                  clean_intermediates=bool(o["also"]) and not audio)
        except Exception as e:                 # never leave a card stuck on "downloading"
            item.error = str(e)
            self.log(f"ERROR: {e}")
        path = output_file(found)
        self.ui(lambda: self._download_done(item, rc, path, state["skipped"]))

    def _download_done(self, item: Item, rc: int, path: str, skipped: bool) -> None:
        if item.removed:
            self.pump()
            return
        item.path = path
        if rc == 0:
            item.pct = 100.0
            if skipped and not path:
                item.status = "skipped"
            else:
                item.status = "done"
                try:
                    item.size = fmt_size(Path(path).stat().st_size)
                except OSError:
                    item.size = item.size.lstrip("~")
                self._add_history(item)
            self.batch["ok"] += 1
        elif rc == -1:
            item.status = "cancelled"
        else:
            item.status = "failed"
            self.batch["bad"] += 1
        item.card.refresh()
        self.pump()

    # ------------------------------------------------------------ state display

    def update_state(self) -> None:
        counts = {s: 0 for s in STATUS_ORDER}
        for it in self.items:
            counts[it.status] += 1
        waiting = counts["queued"] + counts["fetching"]
        parts = []
        if counts["downloading"]:
            parts.append(f"{counts['downloading']} downloading")
        if waiting:
            parts.append(f"{waiting} waiting")
        if counts["done"] + counts["skipped"]:
            parts.append(f"{counts['done'] + counts['skipped']} done")
        if counts["failed"]:
            parts.append(f"{counts['failed']} failed")
        self.summary.configure(text="  ·  ".join(parts))
        self.tabs.tab(0, text=f"Queue ({len(self.items)})" if self.items else "Queue")

        can_start = counts["queued"] > 0 and not self.tools_busy
        self.start_btn.configure(state="normal" if can_start and not (self.running and not counts["queued"])
                                 else "disabled")
        self.start_btn.configure(text=f"Download all ({counts['queued']})" if counts["queued"] else "Download all")
        self.stop_btn.configure(state="normal" if counts["downloading"] or self.running else "disabled")
        self.update_btn.configure(state="disabled" if counts["downloading"] or self.tools_busy else "normal")

        if self.items:
            self.empty_lbl.place_forget()
        else:
            self.empty_lbl.place(relx=0.5, rely=0.42, anchor="center")
        if self.restored:                      # not before the old queue is back, or it would be overwritten
            self._save_queue()

    # ------------------------------------------------------------ queue on disk, clipboard watcher

    def _save_queue(self) -> None:
        """Keep everything that is not finished successfully, so a restart does not lose the queue."""
        keep = []
        for it in self.items:
            if it.status in ("done", "skipped"):
                continue
            keep.append({"url": it.url, "title": it.title, "uploader": it.uploader, "duration": it.duration,
                         "thumb_url": it.thumb_url, "overrides": it.overrides,
                         "status": it.status if it.status in ("failed", "cancelled") else "queued"})
        save_queue(keep)

    def _restore_queue(self) -> None:
        for d in load_queue():
            item = Item(d["url"])
            item.title = d.get("title") or item.url
            item.uploader = d.get("uploader") or ""
            item.duration = d.get("duration")
            item.thumb_url = d.get("thumb_url") or ""
            item.overrides = d.get("overrides") if isinstance(d.get("overrides"), dict) else {}
            item.status = d.get("status") if d.get("status") in ("failed", "cancelled") else "queued"
            if item.title == item.url and item.status == "queued":       # never got its preview
                item.status = "fetching"
                threading.Thread(target=self._info_worker, args=(item, self.single_var.get(), self.cookies()),
                                 daemon=True).start()
            self._add_card(item)
            if item.thumb_url:
                threading.Thread(target=self._thumb_worker, args=(item, item.thumb_url), daemon=True).start()
        self.restored = True
        if self.items:
            self.log(f"{len(self.items)} item(s) restored from the last session.")
        self.update_state()

    def _clip_watch_toggled(self) -> None:
        if self.clip_watch_var.get():          # what is in the clipboard right now is not "new"
            self.watch_last = self._clipboard().strip()

    def _watch_clipboard(self) -> None:
        """While enabled in the options, every new link copied anywhere is added to the queue."""
        try:
            if self.clip_watch_var.get():
                clip = self._clipboard().strip()
                if clip != self.watch_last:
                    self.watch_last = clip
                    if looks_like_url(clip) and not any(it.url == clip for it in self.items):
                        self.add_urls([clip])
            else:
                self.watch_last = self._clipboard().strip()      # so enabling it does not add the old content
            self.root.after(1000, self._watch_clipboard)
        except tk.TclError:
            pass                                                 # window closed

    # ------------------------------------------------------------ history

    def _history_tags(self) -> None:
        self.hist.tag_configure("missing", foreground=COLORS[self.theme]["muted"])

    def _add_history(self, item: Item) -> None:
        self.history.append({"title": item.title, "url": item.url, "path": item.path, "mode": item.mode,
                             "time": time.time(), "uploader": item.uploader})
        save_history(self.history)
        self.refresh_history()

    def refresh_history(self) -> None:
        self.hist.delete(*self.hist.get_children())
        for i in range(len(self.history) - 1, -1, -1):
            h = self.history[i]
            missing = bool(h.get("path")) and not Path(h["path"]).exists()
            when = time.strftime("%Y-%m-%d  %H:%M", time.localtime(h.get("time", 0)))
            title = h.get("title", "") + ("   (file missing)" if missing else "")
            self.hist.insert("", "end", iid=str(i), values=(title, MODES.get(h.get("mode", ""), ""), when),
                             tags=("missing",) if missing else ())
        self._history_tags()

    def _selected_history(self) -> list[dict]:
        return [self.history[int(i)] for i in self.hist.selection()]

    def hist_open(self) -> None:
        for h in self._selected_history()[:1]:
            if h.get("path") and Path(h["path"]).exists():
                open_path(Path(h["path"]))
            else:
                messagebox.showinfo("History", "The file no longer exists at its old location.")

    def hist_reveal(self) -> None:
        for h in self._selected_history()[:1]:
            reveal_file(Path(h["path"])) if h.get("path") else None

    def hist_again(self) -> None:
        self.add_urls([h["url"] for h in self._selected_history() if h.get("url")])

    def hist_remove(self) -> None:
        drop = {int(i) for i in self.hist.selection()}
        self.history = [h for i, h in enumerate(self.history) if i not in drop]
        save_history(self.history)
        self.refresh_history()

    def hist_clear(self) -> None:
        if self.history and messagebox.askyesno("History", "Remove all entries from the history?\n"
                                                          "(Downloaded files are not touched.)"):
            self.history = []
            save_history(self.history)
            self.refresh_history()

    # ------------------------------------------------------------ menu bar, quitting

    def _build_menu(self) -> None:
        """Native menu bar: at the top of the screen on macOS, inside the window on Windows/Linux."""
        root, mac = self.root, sys.platform == "darwin"
        mod = "Command" if mac else "Control"
        acc = (lambda key: f"⌘{key}") if mac else (lambda key: f"Ctrl+{key}")
        bar = tk.Menu(root, tearoff=0)
        self.menubar = bar

        if mac:                                # the application menu ("simple-ytdlp") must come first
            app_menu = tk.Menu(bar, name="apple", tearoff=0)
            app_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)
            bar.add_cascade(menu=app_menu)
            root.createcommand("tk::mac::ShowPreferences", self._toggle_options)
            root.createcommand("tk::mac::Quit", self.on_close)

        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="Paste links", accelerator=acc("V"), command=self._on_global_paste_menu)
        file_menu.add_command(label="Import list …", accelerator=acc("O"), command=self.on_import)
        file_menu.add_separator()
        file_menu.add_command(label="Open download folder", accelerator=acc("Shift+O") if not mac else "⇧⌘O",
                              command=self.open_out_folder)
        if not mac:
            file_menu.add_separator()
            file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_close)
        bar.add_cascade(label="File", menu=file_menu)

        queue_menu = tk.Menu(bar, tearoff=0)
        queue_menu.add_command(label="Download all", accelerator=acc("Return") if not mac else "⌘↩",
                               command=self.start_all)
        queue_menu.add_command(label="Stop", accelerator=acc("."), command=self.stop_all)
        queue_menu.add_command(label="Retry failed", command=self.retry_failed)
        queue_menu.add_separator()
        queue_menu.add_command(label="Clear finished", command=self.clear_finished)
        bar.add_cascade(label="Queue", menu=queue_menu)

        view_menu = tk.Menu(bar, tearoff=0)
        for label, value in (("Follow system theme", "system"), ("Light theme", "light"), ("Dark theme", "dark")):
            view_menu.add_radiobutton(label=label, value=value, variable=self.theme_mode,
                                      command=self.set_theme_mode, state="normal" if sv_ttk else "disabled")
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Show options", variable=self.opts_open, accelerator=acc(","),
                                  command=self._options_changed)
        for i, name in enumerate(("Queue", "History", "Log")):
            view_menu.add_command(label=f"Show {name}", accelerator=acc(str(i + 1)),
                                  command=lambda i=i: self.tabs.select(i))
        bar.add_cascade(label="View", menu=view_menu)

        tools_menu = tk.Menu(bar, tearoff=0)
        tools_menu.add_command(label="Update yt-dlp", command=self.on_update_ytdlp)
        tools_menu.add_command(label=f"Check for {APP_NAME} updates …", command=self.check_app_update_manually)
        tools_menu.add_separator()
        tools_menu.add_command(label="Open settings folder", command=lambda: open_folder(data_dir()))
        bar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(bar, name="help", tearoff=0)
        help_menu.add_command(label="Project page", command=lambda: webbrowser.open(f"https://github.com/{REPO}"))
        help_menu.add_command(label="Report a problem",
                              command=lambda: webbrowser.open(f"https://github.com/{REPO}/issues"))
        if not mac:
            help_menu.add_separator()
            help_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)
        bar.add_cascade(label="Help", menu=help_menu)
        root.configure(menu=bar)

        def bind(seq: str, fn) -> None:
            root.bind_all(f"<{mod}-{seq}>", lambda e: (fn(), "break")[1])

        bind("o", self.on_import)
        bind("O", self.open_out_folder)
        bind("Return", self.start_all)
        bind("period", self.stop_all)
        for i in range(3):
            bind(f"Key-{i + 1}", lambda i=i: self.tabs.select(i))
        if not mac:                            # on macOS the application menu already provides these
            bind("comma", self._toggle_options)
            bind("q", self.on_close)

    def _on_global_paste_menu(self) -> None:
        urls = extract_urls(self._clipboard())
        if urls:
            self.add_urls(urls)
        else:
            messagebox.showinfo("Paste links", "There is no link in the clipboard.")

    def open_out_folder(self) -> None:
        open_folder(Path(self.out_var.get() or DEFAULT_OUT))

    def retry_failed(self) -> None:
        failed = [it for it in self.items if it.status == "failed"]
        for it in failed:
            it.stop = threading.Event()
            it.status, it.pct, it.error = "queued", 0.0, ""
            it.speed = it.eta = it.size = ""
            it.card.refresh()
        if failed:
            self.start_all()

    def show_about(self) -> None:
        try:
            base = find_ytdlp()
            ytdlp = subprocess.run(base + ["--version"], capture_output=True, text=True, timeout=10,
                                   **_no_window()).stdout.strip() if base else "not installed yet"
        except Exception:
            ytdlp = "unknown"
        ffmpeg, source = find_ffmpeg()
        js = find_js_runtime()
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {'(dev)' if __version__ == 'dev' else 'v' + __version__}\n"
            "A friendly graphical front end for yt-dlp.\n\n"
            f"yt-dlp: {ytdlp}\n"
            f"ffmpeg: {source}\n"
            f"JS engine: {js[1].split(':')[0] if js else 'none'}\n"
            f"Settings: {data_dir()}\n\n"
            f"https://github.com/{REPO}")

    def on_close(self) -> None:
        """Quit: ask first while downloads run, and stop their yt-dlp/ffmpeg processes."""
        running = [it for it in self.items if it.status == "downloading"]
        if running and not messagebox.askyesno(
                "Quit", f"{len(running)} download(s) are still running. Quit and cancel them?\n"
                        "(Partial files are kept - downloading the link again resumes them.)"):
            return
        self.running = False
        self._save_queue()                     # running items come back as waiting after a restart
        for it in running:
            it.stop.set()                      # the watcher threads kill the process trees
        try:
            save_settings({"size": f"{self.root.winfo_width()}x{self.root.winfo_height()}"})
        except tk.TclError:
            pass
        self.root.withdraw()
        self.root.after(800 if running else 0, self.root.destroy)

    # ------------------------------------------------------------ tools & updates

    def set_tools_busy(self, busy: bool, text: str = "") -> None:
        self.tools_busy = busy
        self.head_status.configure(text=text if busy else "")
        self.update_state()
        if not busy:
            self.pump()

    def _startup_tools(self) -> None:
        """Fetch missing tools, otherwise check for a yt-dlp update once a day (YouTube changes often;
        an outdated yt-dlp is the most common reason downloads fail)."""
        missing = find_ytdlp() is None or find_js_runtime() is None
        update_due = (managed_ytdlp().exists()
                      and time.time() - float(self.cfg.get("last_update", 0)) > UPDATE_INTERVAL)
        if not (missing or update_due):
            return
        self.set_tools_busy(True, "First start: downloading tools …" if missing else "Checking for updates …")

        def work():
            try:
                if missing:
                    ensure_tools(self.log)
                    ok = find_ytdlp() is not None
                else:
                    ok = update_ytdlp(self.log)
                if ok:
                    save_settings({"last_update": time.time()})
            finally:
                self.ui(lambda: self.set_tools_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def on_update_ytdlp(self) -> None:
        if self.tools_busy or any(it.status == "downloading" for it in self.items):
            messagebox.showinfo("Update yt-dlp", "Please wait until the current downloads have finished.")
            return
        self.set_tools_busy(True, "Updating yt-dlp …")

        def work():
            try:
                if update_ytdlp(self.log):
                    save_settings({"last_update": time.time()})
                if find_js_runtime() is None:
                    install_deno(self.log)
            finally:
                self.ui(lambda: self.set_tools_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def _check_app_update(self) -> None:       # newer release on GitHub? (silent if not)
        found = check_app_update()
        if found:
            self.ui(lambda: self.show_app_update(found))

    def check_app_update_manually(self) -> None:
        def work():
            found = check_app_update()
            if found:
                self.ui(lambda: (self.show_app_update(found), self.tabs.select(0)))
            else:
                self.ui(lambda: messagebox.showinfo(
                    "Updates", f"{APP_NAME} is up to date." if __version__ != "dev"
                    else "This is a development version - the update check is disabled."))
        threading.Thread(target=work, daemon=True).start()

    def show_app_update(self, update: dict) -> None:
        self.news_label.configure(text=f"A new version of simple-ytdlp is available: v{update['version']}")
        self.news_page_btn.configure(command=lambda: webbrowser.open(update["page"]))
        if can_self_update() and update["asset_url"]:
            self.news_now_btn.configure(command=lambda: self.run_self_update(update))
        else:                                  # running as a script, or no file for this platform
            self.news_now_btn.pack_forget()
        self.news.grid()
        self.log(f"New version available: v{update['version']}  ->  {update['page']}")

    def run_self_update(self, update: dict) -> None:
        if any(it.status == "downloading" for it in self.items) or self.tools_busy:
            messagebox.showinfo("Update", "Please wait until the current downloads have finished "
                                          "(or cancel them), then click 'Update now' again.")
            return
        self.news_now_btn.configure(state="disabled")
        self.set_tools_busy(True, f"Updating {APP_NAME} …")

        def work():
            done = install_app_update(update, self.log)

            def finish():
                if done:
                    self.root.destroy()        # the new version has already been started
                    return
                self.set_tools_busy(False)
                self.news_now_btn.configure(state="normal")
                messagebox.showwarning(
                    "Update failed",
                    "The automatic update did not work (see the log).\n"
                    "Use 'Release page' to download the new version manually.")
            self.ui(finish)

        threading.Thread(target=work, daemon=True).start()


def system_theme() -> str:
    try:
        import darkdetect
        return "dark" if darkdetect.isDark() else "light"
    except Exception:
        return "light"


def run_gui() -> int:
    if tk is None:
        print("Tkinter is not installed.")
        print("Linux:  sudo apt install python3-tk   (or python3-tkinter)")
        return 1
    cleanup_old_versions()                     # leftovers of a previous self-update
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    migrate_legacy_data()
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Cross-platform yt-dlp wrapper (CLI + GUI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 simple-ytdlp.py URL1 URL2\n"
               "  python3 simple-ytdlp.py -a urls.txt -m mp3 -o ~/Music\n"
               "  python3 simple-ytdlp.py --gui\n",
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
