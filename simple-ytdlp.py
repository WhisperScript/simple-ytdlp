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
import collections
import datetime
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


def managed_ffmpeg() -> Path:
    return BIN_DIR / _exe("ffmpeg")


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


def ffmpeg_build_name() -> str | None:
    """File name of the matching full ffmpeg build (with ffprobe) of yt-dlp/FFmpeg-Builds, None if there is none."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if os.name == "nt":
        return f"ffmpeg-master-latest-win{'arm64' if arm else '64'}-gpl.zip"
    if sys.platform.startswith("linux"):
        return f"ffmpeg-master-latest-linux{'arm64' if arm else '64'}-gpl.tar.xz"
    return None                                # macOS: brew install ffmpeg


def install_ffmpeg(log=print) -> bool:
    """Download a full ffmpeg (ffmpeg + ffprobe, about 150 MB) into the tools folder. The bundled ffmpeg has no
    ffprobe, which some downloads (merging HLS streams, ...) need."""
    name = ffmpeg_build_name()
    if name is None:
        log(ffmpeg_hint())
        return False
    with _tools_lock:
        archive = BIN_DIR / name
        log("Downloading ffmpeg with ffprobe (about 150 MB, one time) ...")
        try:
            _fetch("https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/" + name, archive, log)
            wanted = {_exe("ffmpeg"), _exe("ffprobe")}
            if name.endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    for member in zf.namelist():
                        if Path(member).name in wanted and "/bin/" in member:
                            (BIN_DIR / Path(member).name).write_bytes(zf.read(member))
            else:
                import tarfile
                with tarfile.open(archive) as tf:
                    for member in tf:
                        if Path(member.name).name in wanted and "/bin/" in member.name and member.isfile():
                            with tf.extractfile(member) as src, (BIN_DIR / Path(member.name).name).open("wb") as dst:
                                shutil.copyfileobj(src, dst)
            archive.unlink(missing_ok=True)
            for exe in wanted:
                _make_executable(BIN_DIR / exe)
            if not (BIN_DIR / _exe("ffprobe")).exists():
                raise RuntimeError("the download does not contain ffprobe")
        except Exception as e:
            archive.unlink(missing_ok=True)
            log(f"ERROR downloading ffmpeg: {e}")
            return False
        log("ffmpeg ready.")
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
    """(path, source) of the ffmpeg to use. source: system | managed (downloaded, with ffprobe) | bundled | none."""
    exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if exe:
        return exe, "system"
    if managed_ffmpeg().exists():
        return str(managed_ffmpeg()), "managed"
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


UPDATE_HINT = "yt-dlp is probably out of date - update it (Tools > Update yt-dlp) and retry"
UPDATE_RE = re.compile(r"unable to extract|nsig extraction failed|player response|precondition check failed|"
                       r"http error 403|no video formats found", re.I)

FRIENDLY_ERRORS = [
    (UPDATE_RE.pattern, UPDATE_HINT),
    (r"confirm your age|age-restricted|age restricted",
     "Age-restricted - pick your browser under Settings > Cookies from browser"),
    (r"private video|this video is private",
     "Private video - sign in in your browser and pick it under Settings > Cookies from browser"),
    (r"sign in to confirm",
     "YouTube wants a login - use Settings > Cookies from browser, or update yt-dlp"),
    (r"http error 429|too many requests",
     "Rate limited (HTTP 429) - wait a few minutes, lower Parallel, or use cookies"),
    (r"not available in your country|geo.?restrict|blocked it in your country",
     "Not available in your country"),
    (r"video unavailable|has been removed|no longer available|does not exist",
     "Video unavailable (removed, private or blocked)"),
    (r"unsupported url", "This link is not supported by yt-dlp"),
    (r"requested format is not available", "That format is not offered for this video - choose another"),
    (r"ffprobe.*(not found|not installed)", "ffprobe is missing - Tools > Get ffmpeg tools, then Retry"),
    (r"ffmpeg.*(not found|not installed)", "ffmpeg is missing"),
    (r"premieres in|live event will begin|will begin in", "The live stream or premiere has not started yet"),
    (r"giving up after|got error: downloaded \d+ bytes",
     "The connection keeps dropping - what was downloaded is kept, Retry continues from there"),
    (r"unable to download|getaddrinfo|name resolution|timed out|connection (reset|refused)",
     "Network problem - check your connection"),
]


def needs_ffprobe(text: str) -> bool:
    return bool(re.search(r"ffprobe.*(not found|not installed)", text or "", re.I))


def suggests_update(text: str) -> bool:
    """True if the error looks like "YouTube changed something", which a yt-dlp update usually fixes."""
    return bool(UPDATE_RE.search(text or ""))


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
                    log("-- stopped --")
                    return
        threading.Thread(target=watch, daemon=True).start()

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            log(line.rstrip())
    except KeyboardInterrupt:
        _kill_tree(proc)
        raise
    finally:
        proc.stdout.close()
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
    if source in ("bundled", "managed"):
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


_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|igshid|si|feature|pp|ref|ref_src|source)$", re.I)
_YT_HOSTS = {"youtube.com", "youtube-nocookie.com", "music.youtube.com", "youtu.be"}


def url_key(url: str) -> str:
    """What makes two links the same download: youtu.be/ID, youtube.com/watch?v=ID&t=30s and /shorts/ID are one
    video; for other sites www., the fragment, a trailing slash and tracking parameters do not count."""
    import urllib.parse as up
    try:
        u = up.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (u.hostname or "").lower()
    host = re.sub(r"^(www|m)\.", "", host)
    query = up.parse_qs(u.query)
    if host in _YT_HOSTS:
        parts = [p for p in u.path.split("/") if p]
        vid = (query.get("v") or [""])[0]
        if not vid and host == "youtu.be" and parts:
            vid = parts[0]
        if not vid and len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            vid = parts[1]
        if vid:
            return "youtube:" + vid
        if query.get("list"):
            return "youtube-list:" + query["list"][0]
    kept = sorted((k, v) for k, vals in query.items() if not _TRACKING.match(k) for v in vals)
    return f"{host}{u.path.rstrip('/')}" + (("?" + up.urlencode(kept)) if kept else "")


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


RESUME_RE = re.compile(r"Resuming download at byte (?P<n>\d+)")

# --- Resuming interrupted downloads -------------------------------------------------------------
# yt-dlp keeps unfinished data in "<file>.part" and continues from its end (an HTTP range request), so a
# stopped download only fetches what is missing - the same idea as rsync --partial. The helpers below make
# that dependable: they find the partial data, repair its end after a crash, and recognise network errors
# that are worth an automatic retry.

TAIL_MARGIN = 1 << 20                       # after an unclean stop the last MiB is fetched again
AUTO_RETRY_DELAYS = (3, 10, 30, 60, 120)    # seconds to wait before each automatic retry
TRANSIENT_RE = re.compile(
    r"getaddrinfo|name resolution|timed out|timeout|connection (?:reset|refused|aborted)|"
    r"http error 5\d\d|incompleteread|remote end closed|eof occurred|network is unreachable|"
    r"temporary failure|giving up after|broken pipe|urlopen error", re.I)


def is_transient_error(text: str) -> bool:
    """True for errors a retry can fix (network trouble, server hiccups) - not for 404, private videos etc."""
    return bool(TRANSIENT_RE.search(text or "")) and not re.search(r"http error 4\d\d", text or "", re.I)


def partial_files(dests) -> list[Path]:
    """The unfinished '<file>.part' files of a download (nothing for files that are complete)."""
    return [p for p in (Path(str(d) + ".part") for d in dests or []) if p.is_file()]


def partial_size(dests) -> int:
    total = 0
    for p in partial_files(dests):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def trim_partial_tail(dests, margin: int = TAIL_MARGIN) -> int:
    """Cut the last `margin` bytes off the partial files so they are downloaded again.

    After a crash or power loss the end of a partial file can be incomplete or zero-filled, and appending to
    it would corrupt the finished video. Re-fetching one MiB costs next to nothing (rsync --append-verify
    checks the existing part with checksums instead). Fragment downloads (HLS/DASH) track their position
    per fragment in a '.ytdl' file and are left alone. Returns the number of bytes removed."""
    removed = 0
    for p in partial_files(dests):
        if Path(str(p)[:-len(".part")] + ".ytdl").exists():
            continue
        try:
            size = p.stat().st_size
            keep = max(size - margin, 0)
            with open(p, "r+b") as fh:
                fh.truncate(keep)
            removed += size - keep
        except OSError:
            pass
    return removed


def discard_partial(dests) -> int:
    """Delete the partial data of a download ('start over'). Returns the bytes freed."""
    freed = 0
    for d in dests or []:
        for p in (Path(str(d) + ".part"), Path(str(d) + ".ytdl")):
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
    return freed


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


# ---------------------------------------------------------------- Display helpers (no GUI needed)

# Short quality names for the interface (the long MODES texts stay for the command line).
UI_QUALITY = {
    "video": "Best quality", "video2160": "Up to 4K", "video1440": "Up to 1440p", "video1080": "Up to 1080p",
    "video720": "Up to 720p", "video480": "Up to 480p", "mp3": "MP3", "m4a": "M4A (AAC)", "opus": "Opus",
}


def quality_label(mode: str) -> str:
    return UI_QUALITY.get(mode, mode)


def mode_label(mode: str) -> str:
    """'Video · Up to 1080p' - for places that do not show the Video/Audio switch next to it."""
    if mode not in UI_QUALITY:
        return ""
    return ("Audio" if mode in AUDIO_MODES else "Video") + " · " + UI_QUALITY[mode]


def fit_text(measure, text: str, max_px: int, ellipsis: str = "…") -> str:
    """text cut with an ellipsis so that measure(text) <= max_px ('measure' returns the pixel width)."""
    text = " ".join(str(text).split())
    if max_px <= 0:
        return ""
    if measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:                                 # longest prefix that still fits together with the ellipsis
        mid = (lo + hi + 1) // 2
        if measure(text[:mid].rstrip() + ellipsis) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    if lo == 0:
        return ellipsis if measure(ellipsis) <= max_px else ""
    return text[:lo].rstrip() + ellipsis


def tilde_path(path, limit: int = 44) -> str:
    """A path for display: home folder as ~, the middle cut out when it is long."""
    text, home = str(path), str(Path.home())
    if text == home or text.startswith(home + os.sep):
        text = "~" + text[len(home):]
    if len(text) > limit:
        tail = max(limit * 2 // 3, 8)
        text = text[:limit - tail - 1] + "…" + text[-tail:]
    return text


def humanize_when(ts: float, now: float | None = None) -> str:
    """'Today 14:05', 'Yesterday 09:12', 'Mon 18:30' (this week) or '2026-09-28'."""
    now = time.time() if now is None else now
    t, n = time.localtime(ts), time.localtime(now)
    days = (datetime.date(n.tm_year, n.tm_mon, n.tm_mday) - datetime.date(t.tm_year, t.tm_mon, t.tm_mday)).days
    clock = time.strftime("%H:%M", t)
    if days == 0:
        return f"Today {clock}"
    if days == 1:
        return f"Yesterday {clock}"
    if 1 < days < 7:
        return time.strftime("%a", t) + f" {clock}"
    return time.strftime("%Y-%m-%d", t)


_RATE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3}


def parse_speed(text: str) -> float:
    """Bytes per second from yt-dlp's '3.20MiB/s' (0 for 'Unknown' and everything else)."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMG]?i?B)/s\s*", text or "")
    return float(m.group(1)) * _RATE_UNITS.get(m.group(2), 0) if m else 0.0


def fmt_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num < 1024 or unit == "GiB":
            return f"{num:.0f} B" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return ""


def fmt_rate(bytes_per_second: float) -> str:
    return fmt_size(bytes_per_second) + "/s"


def summary_options(counts: dict, speed: float = 0.0) -> list[str]:
    """Texts for the queue summary above the list, most detailed first - the tab bar shows the first one that
    fits. Finished and failed counts are the last things to go."""
    waiting = counts.get("queued", 0) + counts.get("fetching", 0)
    finished = counts.get("done", 0) + counts.get("skipped", 0)

    def build(short: bool, with_speed: bool, with_done: bool) -> str:
        parts = []
        if counts.get("downloading"):
            parts.append(f"{counts['downloading']} " + ("active" if short else "downloading")
                         + (f" · {fmt_rate(speed)}" if with_speed and speed else ""))
        if counts.get("paused"):
            parts.append(f"{counts['paused']} paused")
        if waiting:
            parts.append(f"{waiting} " + ("queued" if short else "waiting"))
        if finished and with_done:
            parts.append(f"{finished} done")
        if counts.get("failed"):
            parts.append(f"{counts['failed']} failed")
        return (" · " if short else "  ·  ").join(parts)

    options = [build(False, True, True), build(False, False, True), build(True, False, True),
               build(True, False, False)]
    return list(dict.fromkeys(options))            # without repeats, order kept


def shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


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

try:                                           # thumbnails and the icon; without Pillow the cards show a placeholder
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None

try:                                           # modern theme; without sv-ttk the default Tk look stays
    import sv_ttk
except ImportError:
    sv_ttk = None

THUMB_SIZE = (128, 72)
CARD_H, CARD_GAP = 92, 8                       # every queue card has the same height -> the list can be virtual
ROW_H = CARD_H + CARD_GAP

# Colours of both themes. "bg"/"border" are sv-ttk's own window and card-outline colours.
COLORS = {
    "light": {"bg": "#fafafa", "border": "#e7e7e7", "ok": "#167a3f", "bad": "#c93030", "muted": "#6b6b6b",
              "thumb": "#e3e3e3", "text_bg": "#ffffff", "text_fg": "#1a1a1a", "text_border": "#c8c8c8",
              "accent": "#0067c0", "warn": "#996400", "toast_bg": "#323232", "toast_fg": "#ffffff",
              "tip_bg": "#fffbe6"},
    "dark":  {"bg": "#1c1c1c", "border": "#2f2f2f", "ok": "#5fd38d", "bad": "#ff7b7b", "muted": "#a0a0a0",
              "thumb": "#2f2f2f", "text_bg": "#2b2b2b", "text_fg": "#e6e6e6", "text_border": "#4a4a4a",
              "accent": "#60cdff", "warn": "#f0b45a", "toast_bg": "#e6e6e6", "toast_fg": "#1c1c1c",
              "tip_bg": "#2f2f2f"},
}
STATUS_COLOR = {"fetching": "muted", "queued": "muted", "downloading": "accent", "paused": "warn", "done": "ok",
                "skipped": "ok", "failed": "bad", "cancelled": "muted"}
STATUS_ORDER = ("downloading", "paused", "queued", "fetching", "failed", "cancelled", "done", "skipped")
IS_MAC = sys.platform == "darwin"


def mode_kind(mode: str) -> str:
    return "audio" if mode in AUDIO_MODES else "video"


def round_corners(img, radius: int):
    """img as RGBA with transparent, anti-aliased rounded corners."""
    img = img.convert("RGBA")
    scale = 4                                      # draw the mask large, shrink it -> smooth edge
    mask = Image.new("L", (img.width * scale, img.height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius * scale, fill=255)
    img.putalpha(mask.resize(img.size, Image.LANCZOS))
    return img


def fetch_thumbnail(url: str):
    """Download a thumbnail, crop it to THUMB_SIZE and round its corners (a PIL image), or None."""
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
        return round_corners(img.crop((left, top, left + w, top + h)), 6)
    except Exception:
        return None


def thumb_placeholder(theme: str):
    """Rounded grey box with a play triangle, shown until the real thumbnail is there."""
    if Image is None:
        return None
    c = COLORS[theme]
    img = Image.new("RGB", THUMB_SIZE, c["thumb"])
    d = ImageDraw.Draw(img)
    cx, cy = THUMB_SIZE[0] // 2, THUMB_SIZE[1] // 2
    d.polygon([(cx - 8, cy - 11), (cx - 8, cy + 11), (cx + 12, cy)], fill=c["muted"])
    return round_corners(img, 6)


def make_icon(size: int = 256):
    """The app icon as a PIL image (None without Pillow): rounded square, download arrow and tray."""
    if Image is None:
        return None
    S = 1024
    top, bottom = (92, 156, 255), (104, 72, 238)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((44, 44, S - 44, S - 44), radius=224, fill=255)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(icon)
    white, cx = (255, 255, 255, 255), S // 2
    d.rounded_rectangle((cx - 58, 214, cx + 58, 560), radius=44, fill=white)               # arrow shaft
    d.polygon([(cx - 214, 504), (cx + 214, 504), (cx, 722)], fill=white)                   # arrow head
    d.line([(262, 640), (262, 790), (S - 262, 790), (S - 262, 640)], fill=white, width=68, joint="curve")
    for x, y in ((262, 640), (S - 262, 640)):                                              # round tray ends
        d.ellipse((x - 34, y - 34, x + 34, y + 34), fill=white)
    return icon.resize((size, size), Image.LANCZOS)


def write_icons(folder: str) -> list[str]:
    """Write icon.png/.ico/.icns into folder (used by the release build). Returns the file names."""
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    big = make_icon(1024)
    if big is None:
        raise SystemExit("Pillow is needed to draw the icon (pip install pillow).")
    big.save(out / "icon.png")
    big.save(out / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    big.save(out / "icon.icns")
    return ["icon.png", "icon.ico", "icon.icns"]


class Tooltip:
    """A small hint that appears when the pointer rests on a widget. text may be a function (evaluated
    when the hint is about to show), which is how the recycled queue rows get the text of their item."""

    current_theme = "light"

    def __init__(self, widget, text, delay: int = 550):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _enter(self, _event=None) -> None:
        self._hide()
        self.job = self.widget.after(self.delay, self._show)

    def _show(self) -> None:
        self.job = None
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        c = COLORS[Tooltip.current_theme]
        x, y = self.widget.winfo_pointerx() + 14, self.widget.winfo_pointery() + 18
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=text, justify="left", wraplength=460, padx=8, pady=5, background=c["tip_bg"],
                 foreground=c["text_fg"], relief="solid", borderwidth=1).pack()

    def _hide(self, _event=None) -> None:
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None




class Item:
    """One download in the queue."""
    _counter = 0

    def __init__(self, url: str):
        Item._counter += 1
        self.id = Item._counter
        self.url = url
        self.key = url_key(url)                # the same video behind different links has the same key
        self.title = url
        self.uploader = ""
        self.duration = None
        self.thumb = None                      # PIL image (rounded RGBA), loaded when its row gets visible
        self.thumb_state = None                # None (not asked yet) | "loading" | "done" | "failed"
        self.thumb_url = ""
        self.status = "fetching"               # fetching queued downloading done skipped failed cancelled
        self.pct = 0.0
        self.speed = self.eta = self.size = ""
        self.path = ""
        self.error = ""
        self.mode = ""
        self.overrides: dict = {}              # per-item changes, see ItemDialog
        self.stop = threading.Event()
        self.removed = False
        self.dests: list[str] = []             # files yt-dlp writes for this item (their '.part' files hold the partial data)
        self.resumed = False                   # yt-dlp reported that it continues from partial data
        self.interrupted = False               # stopped uncleanly (crash, power loss): repair the partial end first
        self.pause_requested = False
        self.retries = 0                       # automatic retries after network problems
        self.retry_at = 0.0                    # when the next automatic retry starts

    @property
    def active(self) -> bool:
        return self.status in ("fetching", "queued", "downloading")

    @property
    def finished(self) -> bool:
        return self.status in ("done", "skipped", "failed", "cancelled")


class Jobs:
    """A few worker threads for the slow network jobs (video info first, then thumbnails, newest first).
    A queue of hundreds of links therefore never means hundreds of threads."""

    def __init__(self, workers: int = 3):
        import itertools
        import queue
        self.q: "queue.PriorityQueue" = queue.PriorityQueue()
        self.seq = itertools.count()
        for _ in range(workers):
            threading.Thread(target=self._run, daemon=True).start()

    def submit(self, priority: int, fn, *args) -> None:
        n = next(self.seq)
        self.q.put((priority, -n if priority else n, fn, args))   # info: first in first out; thumbnails: newest first

    def _run(self) -> None:
        while True:
            _, _, fn, args = self.q.get()
            try:
                fn(*args)
            except Exception:
                pass


class Row:
    """The widgets of one visible queue row: status strip, thumbnail, text, progress and buttons.
    Rows are recycled while scrolling - bind() points a row at another Item."""

    PRIMARY = {"fetching": "Remove", "queued": "Download", "downloading": "Pause", "paused": "Resume",
               "done": "Show", "skipped": "Show", "failed": "Retry", "cancelled": "Retry"}

    def __init__(self, app: "App", canvas):
        self.app, self.canvas = app, canvas
        self.item: Item | None = None
        self.photo = None
        self.thumb_key = None
        self.width = 10
        self.bar_mode = "determinate"
        self._text: dict = {}                  # label -> (font, full text), re-fitted when the label gets wider/narrower
        self._label_w: dict = {}
        c = COLORS[app.theme]

        self.outer = tk.Frame(canvas, background=c["bg"], borderwidth=0, highlightthickness=0)   # selection ring
        self.win = canvas.create_window(0, 0, anchor="nw", window=self.outer, width=self.width, height=CARD_H,
                                        state="hidden")
        self.card = ttk.Frame(self.outer, style="Card.TFrame" if sv_ttk is not None else "TFrame",
                              padding=(10, 8, 10, 8))
        self.card.pack(fill="both", expand=True, padx=2, pady=2)
        self.card.columnconfigure(2, weight=1)
        self.card.rowconfigure(0, weight=1)

        self.strip = tk.Frame(self.card, width=4, borderwidth=0, highlightthickness=0)
        self.strip.grid(row=0, column=0, sticky="ns", padx=(0, 10), pady=3)

        self.thumb_box = tk.Frame(self.card, width=THUMB_SIZE[0], height=THUMB_SIZE[1], background=c["bg"],
                                  borderwidth=0, highlightthickness=0)
        self.thumb_box.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.thumb_box.grid_propagate(False)
        self.thumb_lbl = tk.Label(self.thumb_box, borderwidth=0, background=c["bg"], foreground=c["muted"],
                                  text="▶")
        self.thumb_lbl.place(x=0, y=0, relwidth=1, relheight=1)
        self.badge = tk.Label(self.thumb_box, font=app.font_badge, padx=4, pady=0, borderwidth=0,
                              background="#000000", foreground="#ffffff")

        body = ttk.Frame(self.card)
        body.grid(row=0, column=2, sticky="ew")            # centred vertically: the card row is the tall one
        body.columnconfigure(0, weight=1)
        self.title = ttk.Label(body, style="CardTitle.TLabel", anchor="w", width=1)
        self.title.grid(row=0, column=0, sticky="ew")
        self.meta = ttk.Label(body, style="Muted.TLabel", anchor="w", width=1)
        self.meta.grid(row=1, column=0, sticky="ew")
        self.status = ttk.Label(body, style="CardStatus.TLabel", anchor="w", width=1)
        self.status.grid(row=2, column=0, sticky="ew")
        self.bar = ttk.Progressbar(body, maximum=100, length=10)
        self.bar.grid(row=3, column=0, sticky="ew", pady=(4, 0))

        btns = ttk.Frame(self.card)
        btns.grid(row=0, column=3, padx=(12, 0))
        self.primary = ttk.Button(btns, width=10, command=self._primary_click)
        self.primary.pack(side="left")
        self.more = ttk.Button(btns, text="…", width=3, command=self._more_click)
        self.more.pack(side="left", padx=(6, 0))

        for label in (self.title, self.meta, self.status):
            label.bind("<Configure>", lambda e, w=label: self._refit(w))
        Tooltip(self.title, self._tip_title)
        Tooltip(self.status, lambda: self.item.error if self.item and self.item.status == "failed" else "")
        Tooltip(self.more, "More actions (or right-click the row)")
        self._bind_click(self.outer)

    # -- events

    def _bind_click(self, widget) -> None:
        """Click selects, double click opens, right click shows the menu - on everything except the buttons."""
        secondary = ("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)
        widget.bind("<Button-1>", lambda e: self.app.row_click(self.item, e))
        widget.bind("<Double-Button-1>", lambda e: self.app.card_activate(self.item))
        for seq in secondary:
            widget.bind(seq, lambda e: self.app.card_menu(self.item, e))
        for child in widget.winfo_children():
            if not isinstance(child, ttk.Button):
                self._bind_click(child)

    def _primary_click(self) -> None:
        it, app = self.item, self.app
        if it is None:
            return
        {"fetching": app.remove_item, "queued": app.start_item, "downloading": app.pause_item,
         "paused": app.resume_item, "done": app.show_item, "skipped": app.show_item, "failed": app.retry_item,
         "cancelled": app.retry_item}[it.status](it)

    def _more_click(self) -> None:
        if self.item is not None:
            self.app.card_menu(self.item, None, anchor=self.more)

    def _tip_title(self) -> str:
        it = self.item
        return f"{it.title}\n{it.url}" if it and it.title != it.url else (it.url if it else "")

    # -- text that must not be cut by the widget edge

    def _set_text(self, label, font, text: str) -> None:
        self._text[label] = (font, text)
        self._fit(label)

    def _fit(self, label) -> None:
        font, text = self._text[label]
        width = label.winfo_width()
        if width <= 10:                                    # not laid out yet: estimate from the row width
            width = max(self.width - 340, 120)
        label.configure(text=fit_text(font.measure, text, width - 4))

    def _refit(self, label) -> None:
        width = label.winfo_width()
        if label in self._text and abs(width - self._label_w.get(label, 0)) >= 2:
            self._label_w[label] = width
            self._fit(label)

    # -- binding to an item

    def bind(self, item: Item) -> None:
        self.item = item
        self.thumb_key = None
        self.canvas.itemconfigure(self.win, state="normal")
        self.refresh()
        self.app.request_thumb(item)

    def unbind(self) -> None:
        self.item = None
        self.bar.stop()
        self.bar_mode = "determinate"
        self.canvas.itemconfigure(self.win, state="hidden")

    def status_text(self, it: Item) -> str:
        st = it.status
        if st == "fetching":
            return "Loading info …"
        if st == "queued":
            if it.retry_at > time.time():
                return (f"Connection problem - retrying in {max(int(it.retry_at - time.time()) + 1, 1)} s "
                        f"({it.retries}/{len(AUTO_RETRY_DELAYS)})")
            return "Waiting"
        if st == "paused":
            kept = partial_size(it.dests)
            return f"Paused at {it.pct:.0f}%" + (f"  ·  {fmt_size(kept)} kept" if kept else "")
        if st == "downloading":
            if it.pause_requested:
                return "Pausing …"
            if it.pct >= 100:
                return "Processing …"
            parts = [("↻ " if it.resumed else "") + f"{it.pct:.0f}%"]
            if it.size:
                parts.append(it.size)
            if it.speed and it.speed != "Unknown":
                parts.append(it.speed)
            if it.eta and it.eta != "Unknown":
                parts.append(f"ETA {it.eta}")
            return "  ·  ".join(parts)
        if st == "done":
            return "✓ Done" + (f"  ·  {it.size}" if it.size else "")
        if st == "skipped":
            return "✓ Already downloaded"
        if st == "failed":
            return "✗ " + (friendly_error(it.error) if it.error else "Failed")
        kept = partial_size(it.dests)
        return "Cancelled" + (f"  ·  {fmt_size(kept)} kept" if kept else "")

    def refresh(self) -> None:
        it, app = self.item, self.app
        if it is None:
            return
        c = COLORS[app.theme]
        color = c[STATUS_COLOR[it.status]]
        self.strip.configure(background=color)
        self.outer.configure(background=c["accent"] if it.id in app.selected else c["bg"])
        self._set_text(self.title, app.font_title, it.title)
        meta = "  ·  ".join(x for x in (it.uploader, ("⚙ " + overrides_summary(it.overrides)) if it.overrides else "")
                            if x)
        self._set_text(self.meta, app.font_small, meta or (it.url if it.title != it.url else ""))
        self._set_text(self.status, app.font_status, self.status_text(it))
        self.status.configure(foreground=color)
        self._refresh_bar(it)
        self.primary.configure(text=self.PRIMARY[it.status])
        self._refresh_thumb(it)

    def _refresh_bar(self, it: Item) -> None:
        if it.status == "fetching":
            if self.bar_mode != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(40)
                self.bar_mode = "indeterminate"
            self.bar.grid()
        elif it.status in ("downloading", "paused"):
            if self.bar_mode == "indeterminate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self.bar_mode = "determinate"
            self.bar["value"] = it.pct
            self.bar.grid()
        else:
            if self.bar_mode == "indeterminate":
                self.bar.stop()
                self.bar.configure(mode="determinate")
                self.bar_mode = "determinate"
            self.bar.grid_remove()

    def _refresh_thumb(self, it: Item) -> None:
        c = COLORS[self.app.theme]
        self.thumb_box.configure(background=c["bg"])
        self.thumb_lbl.configure(background=c["bg"], foreground=c["muted"])
        image = it.thumb if it.thumb is not None else self.app.placeholder()
        key = (id(image), self.app.theme)
        if image is not None and ImageTk is not None:
            if key != self.thumb_key:
                self.photo = ImageTk.PhotoImage(image)
                self.thumb_lbl.configure(image=self.photo, text="")
                self.thumb_key = key
        else:
            self.thumb_lbl.configure(image="", text="▶")
        if it.duration:
            self.badge.configure(text=fmt_duration(it.duration))
            self.badge.place(relx=1, rely=1, x=-5, y=-5, anchor="se")
        else:
            self.badge.place_forget()


class QueueList:
    """The scrollable list of queue cards. It is virtual: only the rows that are (nearly) in view exist
    as widgets, so a playlist with thousands of videos costs the same as one with ten."""

    BUFFER = 2

    def __init__(self, app: "App", parent):
        self.app = app
        self.canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0, yscrollincrement=12)
        self.vsb = ttk.Scrollbar(parent, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.free: list[Row] = []
        self.bound: dict[int, Row] = {}
        self.width = 400
        self._job = None
        self._scrollbar_shown = True
        self._region = None                    # last scroll region / first visible fraction: only react to changes,
        self._top = None                       # or setting the region would trigger the scrollbar callback forever
        self.canvas.bind("<Configure>", lambda e: self.schedule())

    def _on_yscroll(self, lo, hi) -> None:
        self.vsb.set(lo, hi)
        if lo != self._top:
            self._top = lo
            self.schedule()

    def schedule(self) -> None:
        if self._job is None:
            self._job = self.canvas.after_idle(self.layout)

    def layout(self) -> None:
        """Bind rows to the items in view, position them, and size the scroll area to the whole list."""
        self._job = None
        items = self.app.items
        n = len(items)
        w, h = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 1)
        total = n * ROW_H - CARD_GAP if n else 0
        need_bar = total > h
        if need_bar != self._scrollbar_shown:
            self._scrollbar_shown = need_bar
            if need_bar:
                self.vsb.grid()
            else:
                self.vsb.grid_remove()
        region = (0, 0, w, max(total, h))
        if region != self._region:
            self._region = region
            self.canvas.configure(scrollregion=region)
        top = self.canvas.canvasy(0)
        first = max(0, int(top // ROW_H) - self.BUFFER)
        last = min(n - 1, int((top + h) // ROW_H) + self.BUFFER)
        wanted = {items[i].id: i for i in range(first, last + 1)}
        for iid in [k for k in self.bound if k not in wanted]:
            row = self.bound.pop(iid)
            row.unbind()
            self.free.append(row)
        self.width = w
        for iid, i in wanted.items():
            row = self.bound.get(iid)
            if row is None:
                row = self.free.pop() if self.free else Row(self.app, self.canvas)
                self.bound[iid] = row
                row.width = w
                self.canvas.itemconfigure(row.win, width=w)
                row.bind(items[i])
            elif row.width != w:
                row.width = w
                self.canvas.itemconfigure(row.win, width=w)
            self.canvas.coords(row.win, 0, i * ROW_H)

    def row_for(self, item: Item) -> "Row | None":
        return self.bound.get(item.id)

    def touch(self, item: Item) -> None:
        row = self.bound.get(item.id)
        if row is not None:
            row.refresh()

    def refresh_rows(self) -> None:
        for row in self.bound.values():
            row.refresh()

    def scroll_to(self, item: Item) -> None:
        """Scroll just far enough that the item is fully visible."""
        if item not in self.app.items:
            return
        i = self.app.items.index(item)
        top, h = self.canvas.canvasy(0), max(self.canvas.winfo_height(), 1)
        y0, y1 = i * ROW_H, i * ROW_H + CARD_H
        total = max(len(self.app.items) * ROW_H - CARD_GAP, h)
        if y0 < top:
            self.canvas.yview_moveto(y0 / total)
        elif y1 > top + h:
            self.canvas.yview_moveto((y1 - h) / total)
        self.schedule()


class TabBar(tk.Frame if tk else object):
    """Text tabs with an underline for the current one (Queue / History / Log) and a status text at the right."""

    def __init__(self, parent, names, command, font):
        super().__init__(parent, borderwidth=0, highlightthickness=0)
        self.command, self.current, self.font = command, 0, font
        self.tabs: list[tuple] = []
        for i, name in enumerate(names):
            box = tk.Frame(self, borderwidth=0, highlightthickness=0)
            label = tk.Label(box, text=name, font=font, padx=12, pady=7, borderwidth=0, cursor="hand2",
                             takefocus=True, highlightthickness=1)
            line = tk.Frame(box, height=3, borderwidth=0, highlightthickness=0)
            label.pack()
            line.pack(fill="x")
            box.pack(side="left", padx=(0, 4))
            for widget in (label, line):
                widget.bind("<Button-1>", lambda e, i=i: self.select(i))
            label.bind("<Return>", lambda e, i=i: self.select(i))
            label.bind("<space>", lambda e, i=i: self.select(i))
            label.bind("<Right>", lambda e, i=i: self._step(i, 1))
            label.bind("<Left>", lambda e, i=i: self._step(i, -1))
            self.tabs.append((box, label, line))
        self.summary = tk.Label(self, text="", font=font, borderwidth=0)
        self.summary.pack(side="right", padx=(8, 2))
        self._summary_options = [""]
        self.bind("<Configure>", self._fit_summary)

    def set_summary(self, options: list[str]) -> None:
        """Show the most detailed of the texts (longest first) that fits next to the tabs - a plain label
        would cut a too long text in the middle."""
        self._summary_options = options or [""]
        self._fit_summary()

    def _fit_summary(self, _event=None) -> None:
        width = self.winfo_width()
        free = width - sum(box.winfo_reqwidth() + 4 for box, _, _ in self.tabs) - 24 if width > 1 else 10 ** 6
        text = next((t for t in self._summary_options if self.font.measure(t) <= free), None)
        if text is None:
            text = fit_text(self.font.measure, self._summary_options[-1], max(free, 40))
        if text != self.summary.cget("text"):
            self.summary.configure(text=text)

    def _step(self, i: int, delta: int) -> None:
        j = (i + delta) % len(self.tabs)
        self.select(j)
        self.tabs[j][1].focus_set()

    def select(self, i: int, notify: bool = True) -> None:
        self.current = i
        self.colorize(self.theme)
        if notify:
            self.command(i)

    def set_text(self, i: int, text: str) -> None:
        self.tabs[i][1].configure(text=text)
        self.after_idle(self._fit_summary)             # the tab got wider or narrower

    theme = "light"

    def colorize(self, theme: str) -> None:
        self.theme = theme
        c = COLORS[theme]
        self.configure(background=c["bg"])
        for i, (box, label, line) in enumerate(self.tabs):
            on = i == self.current
            box.configure(background=c["bg"])
            label.configure(background=c["bg"], foreground=c["text_fg"] if on else c["muted"],
                            highlightbackground=c["bg"], highlightcolor=c["accent"])
            line.configure(background=c["accent"] if on else c["bg"])
        self.summary.configure(background=c["bg"], foreground=c["muted"])




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
        self.mode_var = tk.StringVar(value=mode_label(o.get("mode", "")) or self.DEFAULT_MODE)
        ttk.Combobox(body, textvariable=self.mode_var, state="readonly", width=34,
                     values=[self.DEFAULT_MODE, *[mode_label(k) for k in MODES]]).grid(row=2, column=1, columnspan=3, sticky="w")

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
        mode = next((k for k in MODES if mode_label(k) == self.mode_var.get()), "")
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


class SettingsDialog(tk.Toplevel if tk else object):
    """All settings of the download queue, grouped by topic. The widgets edit the variables of the
    App directly, so changes apply immediately; they are stored when the window is closed."""

    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.title("Settings")
        self.transient(app.root)
        self.resizable(False, False)
        switch = "Switch.TCheckbutton" if sv_ttk is not None else "TCheckbutton"
        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0)

        def section(title: str, row: int) -> ttk.Frame:
            box = ttk.LabelFrame(body, text=title, padding=(12, 8, 12, 10))
            box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            box.columnconfigure(1, weight=1)
            return box

        def field(box, row: int, label: str, widget, hint: str = "") -> None:
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 12))
            widget.grid(row=row, column=1, sticky="w", pady=3)
            if hint:
                ttk.Label(box, text=hint, style="Muted.TLabel").grid(row=row, column=2, sticky="w", padx=(8, 0))

        def toggles(box, items) -> None:
            for i, (text, var) in enumerate(items):
                check = ttk.Checkbutton(box, text=text, variable=var, style=switch)
                check.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 28), pady=3)
                if var is app.subs_var:
                    app.subs_check = check

        look = section("Appearance", 0)
        for i, (label, value) in enumerate((("Follow the system", "system"), ("Light", "light"), ("Dark", "dark"))):
            ttk.Radiobutton(look, text=label, value=value, variable=app.theme_mode,
                            command=app.set_theme_mode).grid(row=0, column=i, sticky="w", padx=(0, 24), pady=3)
            if sv_ttk is None:
                look.winfo_children()[-1].configure(state="disabled")

        general = section("Downloads", 1)
        toggles(general, (("Skip already downloaded", app.arch_var), ("Single video only (no playlist)", app.single_var),
                          ("Folder per channel", app.uploader_var), ("Start as soon as a link is added", app.auto_var),
                          ("Watch the clipboard for links", app.clip_watch_var)))
        speed = ttk.Frame(general)
        speed.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(speed, text="Parallel downloads").pack(side="left")
        ttk.Spinbox(speed, textvariable=app.parallel_var, from_=1, to=4, width=3,
                    state="readonly").pack(side="left", padx=(8, 24))
        ttk.Label(speed, text="Speed limit").pack(side="left")
        ttk.Entry(speed, textvariable=app.limit_var, width=7).pack(side="left", padx=(8, 4))
        ttk.Label(speed, text="e.g. 2M", style="Muted.TLabel").pack(side="left")

        extras = section("Video and audio extras", 2)
        toggles(extras, (("Subtitles", app.subs_var), ("Embed thumbnail", app.thumb_var),
                         ("Embed chapters", app.chapters_var), ("Remove sponsor segments", app.sponsor_var)))
        field(extras, 2, "Subtitle languages", ttk.Entry(extras, textvariable=app.sub_langs_var, width=14),
              "e.g. en,de or all")

        net = section("Login and network", 3)
        field(net, 0, "Cookies from browser",
              ttk.Combobox(net, textvariable=app.cookie_var, values=[NO_BROWSER, *BROWSERS[1:]],
                           state="readonly", width=12), "for private or age-restricted videos")
        field(net, 1, "Proxy", ttk.Entry(net, textvariable=app.proxy_var, width=30), "e.g. http://host:8080")

        adv = section("Advanced", 4)
        field(adv, 0, "File name", ttk.Entry(adv, textvariable=app.name_var, width=30), "default: %(title)s")
        field(adv, 1, "Extra yt-dlp arguments", ttk.Entry(adv, textvariable=app.args_var, width=30))

        prof = section("Profiles", 5)
        row = ttk.Frame(prof)
        row.grid(row=0, column=0, columnspan=3, sticky="w")
        app.profile_combo = ttk.Combobox(row, textvariable=app.profile_var, width=20,
                                         values=sorted(load_settings().get("profiles", {})))
        app.profile_combo.pack(side="left")
        app.profile_combo.bind("<<ComboboxSelected>>", lambda e: app.load_profile())
        ttk.Button(row, text="Save", command=app.save_profile).pack(side="left", padx=6)
        ttk.Button(row, text="Delete", command=app.delete_profile).pack(side="left")
        ttk.Label(prof, text="Pick a profile to load it. Type a new name and press Save to store the current "
                             "settings\n(quality, folder and everything above) under it.",
                  style="Muted.TLabel", justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Button(body, text="Close", command=self.close, width=10).grid(row=6, column=0, sticky="e")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        app.sync_mode_options()                # e.g. subtitles are greyed out in audio mode
        self.update_idletasks()
        x = app.root.winfo_rootx() + max((app.root.winfo_width() - self.winfo_width()) // 2, 0)
        y = app.root.winfo_rooty() + 40
        self.geometry(f"+{x}+{y}")

    def close(self) -> None:
        self.app.subs_check = None
        self.app.save_options()
        self.destroy()


class AboutDialog(tk.Toplevel if tk else object):
    """Name, version and the tool versions in use - with a button that copies them for a bug report."""

    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.title(f"About {APP_NAME}")
        self.transient(app.root)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=20)
        body.grid(row=0, column=0)
        if ImageTk is not None:
            self.photo = ImageTk.PhotoImage(make_icon(96))
            ttk.Label(body, image=self.photo).grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, 18))
        ttk.Label(body, text=APP_NAME, style="Big.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(body, text=("development version" if __version__ == "dev" else f"Version {__version__}"),
                  style="Muted.TLabel").grid(row=1, column=1, sticky="w")
        ttk.Label(body, text="A friendly graphical front end for yt-dlp.").grid(row=2, column=1, sticky="w", pady=(6, 0))
        self.info_var = tk.StringVar(value="Checking tools …")
        ttk.Label(body, textvariable=self.info_var, style="Muted.TLabel", justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(16, 0))
        row = ttk.Frame(body)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Button(row, text="Copy info", command=self.copy_info).pack(side="left")
        ttk.Button(row, text="Project page",
                   command=lambda: webbrowser.open(f"https://github.com/{REPO}")).pack(side="left", padx=8)
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = app.root.winfo_rootx() + max((app.root.winfo_width() - self.winfo_width()) // 2, 0)
        self.geometry(f"+{x}+{app.root.winfo_rooty() + 70}")
        threading.Thread(target=self._collect, daemon=True).start()

    def _collect(self) -> None:
        try:
            base = find_ytdlp()
            ytdlp = subprocess.run(base + ["--version"], capture_output=True, text=True, timeout=10,
                                   **_no_window()).stdout.strip() if base else "not installed yet"
        except Exception:
            ytdlp = "unknown"
        ffmpeg, source = find_ffmpeg()
        js = find_js_runtime()
        lines = [f"{APP_NAME} {'(dev)' if __version__ == 'dev' else __version__}",
                 f"yt-dlp: {ytdlp}", f"ffmpeg: {source}", f"JS engine: {js[1].split(':')[0] if js else 'none'}",
                 f"Python {platform.python_version()}, Tk {tk.TkVersion}, Pillow: {'yes' if Image else 'no'}",
                 f"System: {platform.platform()}", f"Settings folder: {data_dir()}"]
        text = "\n".join(lines)
        self.app.ui(lambda: self.winfo_exists() and self.info_var.set(text))

    def copy_info(self) -> None:
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(self.info_var.get())
        self.app.toast("Copied to the clipboard")


class App:
    """The main window: link input, download queue, history and log."""

    NOTICE_ORDER = ("setup-error", "setup", "restore", "update")

    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.items: list[Item] = []
        self.selected: set[int] = set()        # ids of the selected queue items
        self.anchor: int | None = None         # where a shift-click range starts
        self.cursor: int | None = None         # the item the arrow keys move from
        self.running = False                   # True after "Download all" until the queue is empty
        self.tools_busy = False                # yt-dlp is being installed/updated
        self.tools_ready = threading.Event()   # set once yt-dlp is usable (or the setup failed)
        self.batch = {"ok": 0, "bad": 0}
        self.batch_ids: set[int] = set()       # items of the current run (for the progress in the title)
        self.jobs = Jobs(3)
        self.history = load_history()
        self.log_lines: "collections.deque[str]" = collections.deque(maxlen=3000)
        self.text_widgets: list = []
        self.notices: dict[str, dict] = {}
        self.restored = False
        self.watch_last = ""
        self.last_clip = ""
        self.placeholder_text = "Paste a video or playlist link here"
        self.placeholder_on = False
        self.toast_job = None
        self.save_job = None
        self._retry_job = None
        self._setup_panel_shown = False
        self.setup_error = ""
        self._placeholders: dict = {}

        self.theme_mode = tk.StringVar(value=self.cfg.get("theme") or "system")    # system | light | dark
        if self.theme_mode.get() not in ("system", "light", "dark"):
            self.theme_mode.set("system")
        self.theme = self._resolve_theme() if sv_ttk is not None else "light"
        Tooltip.current_theme = self.theme

        self.base_title = APP_NAME + ("" if __version__ == "dev" else f"  v{__version__}")
        root.title(self.base_title)
        size = str(self.cfg.get("size", ""))
        root.geometry(size if re.fullmatch(r"\d{3,4}x\d{3,4}", size) else "920x760")
        root.minsize(780, 540)

        self._set_icon()
        if sv_ttk is not None:
            sv_ttk.set_theme(self.theme)       # creates the theme's fonts, which _fonts() takes its sizes from
        self._fonts()
        self._build()
        self._build_menu()
        self.apply_theme(self.theme)
        self.select_tab(0)
        self.sync_mode_options()
        self.refresh_history()
        self.update_state()

        root.bind("<FocusIn>", self._on_focus)
        root.bind("<<Paste>>", self._on_global_paste)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._bind_wheel()
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

    def _set_icon(self) -> None:
        if ImageTk is None:
            return
        try:
            self.icon_photo = ImageTk.PhotoImage(make_icon(128))
            self.root.iconphoto(True, self.icon_photo)
        except Exception:
            pass

    def _fonts(self) -> None:
        """All fonts in pixels and derived from the one the theme uses for its widgets, so text of the
        queue, the dialogs and the ttk widgets always has matching sizes (on every platform)."""
        try:
            body = tkfont.nametofont("SunValleyBodyFont" if sv_ttk is not None else "TkDefaultFont")
        except tk.TclError:
            body = tkfont.nametofont("TkDefaultFont")
        family, size = body.actual("family"), int(body.cget("size"))
        px = -size if size < 0 else max(round(size * float(self.root.tk.call("tk", "scaling"))), 11)

        def make(delta: int, weight: str = "normal"):
            return tkfont.Font(family=family, size=-(px + delta), weight=weight)

        self.font_title, self.font_status, self.font_small = make(1, "bold"), make(0), make(-2)
        self.font_badge, self.font_big, self.font_entry = make(-3, "bold"), make(8, "bold"), make(1)
        self.font_tab = make(0, "bold")

    def placeholder(self):
        """The grey thumbnail stand-in for the current theme."""
        if self.theme not in self._placeholders:
            self._placeholders[self.theme] = thumb_placeholder(self.theme)
        return self._placeholders[self.theme]

    def apply_theme(self, name: str) -> None:
        self.theme = name
        Tooltip.current_theme = name
        if sv_ttk is not None:
            sv_ttk.set_theme(name)
        c = COLORS[name]
        style = ttk.Style()                    # style settings belong to one theme -> set them after switching
        style.configure("CardTitle.TLabel", font=self.font_title)
        style.configure("CardStatus.TLabel", font=self.font_status)
        style.configure("Big.TLabel", font=self.font_big)
        style.configure("Muted.TLabel", font=self.font_small, foreground=c["muted"])
        style.configure("Hint.TLabel", font=self.font_status, foreground=c["muted"])
        for w in self.text_widgets:            # ttk themes do not cover tk.Text
            w.configure(background=c["text_bg"], foreground=c["text_fg"], insertbackground=c["text_fg"],
                        highlightbackground=c["text_border"], highlightcolor=c["accent"])
        self.view.canvas.configure(background=c["bg"])
        self.rule.configure(background=c["border"])
        self.tabbar.colorize(name)
        self.view.refresh_rows()
        self._history_tags()

    def _resolve_theme(self) -> str:
        mode = self.theme_mode.get()
        return system_theme() if mode == "system" else mode

    def set_theme_mode(self) -> None:
        """Apply the mode chosen in the View menu or the settings (system follows the operating system)."""
        self.apply_theme(self._resolve_theme())
        save_settings({"theme": self.theme_mode.get()})

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        root, cfg = self.root, self.cfg
        accent = "Accent.TButton" if sv_ttk is not None else "TButton"
        tool = "Toggle.TButton" if sv_ttk is not None else "TButton"
        card_style = "Card.TFrame" if sv_ttk is not None else "TFrame"

        main = ttk.Frame(root, padding=(18, 16, 18, 12))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        # row 0: the link
        add = ttk.Frame(main)
        add.grid(row=0, column=0, sticky="ew")
        add.columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(add, textvariable=self.url_var, font=self.font_entry)
        self.url_entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self.url_entry.bind("<Return>", lambda e: self.on_add())
        self.url_entry.bind("<<Paste>>", self._on_entry_paste)
        self.url_entry.bind("<FocusIn>", lambda e: self._placeholder(False))
        self.url_entry.bind("<FocusOut>", lambda e: self._placeholder(True))
        ttk.Button(add, text="Paste", command=self._on_global_paste_menu, width=7).grid(
            row=0, column=1, padx=(8, 0), sticky="ns")
        ttk.Button(add, text="Add", style=accent, command=self.on_add, width=8).grid(
            row=0, column=2, padx=(8, 0), sticky="ns")
        self._placeholder(True)

        # row 1: what to download
        bar = ttk.Frame(main)
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
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
        self.quality_var = tk.StringVar(value=quality_label(mode))
        self.quality_combo = ttk.Combobox(bar, textvariable=self.quality_var, state="readonly", width=14)
        self.quality_combo.grid(row=0, column=1, padx=(8, 16))
        self.quality_combo.bind("<<ComboboxSelected>>", lambda e: self.sync_mode_options())
        also = ttk.Frame(bar)                  # video modes: keep the video AND save a separate audio file
        also.grid(row=0, column=2)
        self.also_lbl = ttk.Label(also, text="Also save audio")
        self.also_lbl.pack(side="left", padx=(0, 8))
        self.also_var = tk.StringVar(value=cfg.get("also_audio") or ALSO_AUDIO_NONE)
        self.also_combo = ttk.Combobox(also, textvariable=self.also_var, values=[ALSO_AUDIO_NONE, *AUDIO_MODES],
                                       state="readonly", width=6)
        self.also_combo.pack(side="left")
        Tooltip(self.also_combo, "Keeps the video and saves the sound as a separate file next to it")
        ttk.Button(bar, text="Settings …", command=self.open_settings, width=11).grid(row=0, column=4)

        self.out_var = tk.StringVar(value=cfg.get("out") or str(DEFAULT_OUT))
        self._init_settings_vars(cfg)

        # row 2: queue / history / log
        content = ttk.Frame(main)
        content.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)
        self.tabbar = TabBar(content, ["Queue", "History", "Log"], self._show_page, self.font_tab)
        self.tabbar.grid(row=0, column=0, sticky="ew")
        self.rule = tk.Frame(content, height=1, borderwidth=0, highlightthickness=0)
        self.rule.grid(row=1, column=0, sticky="ew")
        self.notice = ttk.Frame(content, style=card_style, padding=(12, 8))
        self.notice.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.notice.grid_remove()
        self.stack = ttk.Frame(content)
        self.stack.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.stack.columnconfigure(0, weight=1)
        self.stack.rowconfigure(0, weight=1)
        self.pages = [ttk.Frame(self.stack) for _ in range(3)]
        for page in self.pages:
            page.grid(row=0, column=0, sticky="nsew")
        self._build_queue_page(self.pages[0])
        self._build_history_page(self.pages[1])
        self._build_log_page(self.pages[2])
        self.toast_lbl = tk.Label(content, padx=14, pady=8, borderwidth=0, font=self.font_status)

        # row 3: footer
        foot = ttk.Frame(main)
        foot.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        foot.columnconfigure(1, weight=1)
        self.folder_var = tk.StringVar()
        self.folder_menu = tk.Menu(foot, tearoff=0, postcommand=self._fill_folder_menu)
        self.folder_btn = ttk.Menubutton(foot, textvariable=self.folder_var, menu=self.folder_menu, width=34,
                                         direction="above")
        self.folder_btn.grid(row=0, column=0, sticky="w")
        Tooltip(self.folder_btn, lambda: f"Downloads are saved in\n{self.out_var.get()}")
        btns = ttk.Frame(foot)
        btns.grid(row=0, column=2, sticky="e")
        self.clear_btn = ttk.Button(btns, text="Clear finished", command=self.clear_finished)
        self.clear_btn.grid(row=0, column=0, padx=(0, 6))
        self.stop_btn = ttk.Button(btns, text="Pause all", command=self.pause_all)
        self.stop_btn.grid(row=0, column=1, padx=(0, 6))
        self.start_btn = ttk.Button(btns, text="Download all", style=accent, command=self.start_all)
        self.start_btn.grid(row=0, column=2)
        self._update_folder_label()

    def _init_settings_vars(self, cfg: dict) -> None:
        """The variables behind the settings window (see SettingsDialog)."""
        self.settings_win = None
        self.subs_check = None
        self.profile_combo = None
        self.subs_var = tk.BooleanVar(value=cfg.get("subs", False))
        self.thumb_var = tk.BooleanVar(value=cfg.get("thumb", False))
        self.arch_var = tk.BooleanVar(value=cfg.get("archive", True))
        self.uploader_var = tk.BooleanVar(value=cfg.get("uploader", False))
        self.single_var = tk.BooleanVar(value=cfg.get("single", True))
        self.sponsor_var = tk.BooleanVar(value=cfg.get("sponsorblock", False))
        self.auto_var = tk.BooleanVar(value=cfg.get("autostart", True))
        self.chapters_var = tk.BooleanVar(value=cfg.get("chapters", False))
        self.clip_watch_var = tk.BooleanVar(value=False)                   # never on at startup
        self.clip_watch_var.trace_add("write", lambda *_: self._clip_watch_toggled())
        self.cookie_var = tk.StringVar(value=cfg.get("cookies") or NO_BROWSER)
        self.limit_var = tk.StringVar(value=cfg.get("limit", ""))
        self.parallel_var = tk.StringVar(value=str(cfg.get("parallel", 2)))
        self.sub_langs_var = tk.StringVar(value=cfg.get("sub_langs", "en,de"))
        self.name_var = tk.StringVar(value=cfg.get("name", ""))
        self.proxy_var = tk.StringVar(value=cfg.get("proxy", ""))
        self.args_var = tk.StringVar(value=cfg.get("args", ""))
        self.profile_var = tk.StringVar(value="")

    def _build_queue_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.view = QueueList(self, page)
        accent = "Accent.TButton" if sv_ttk is not None else "TButton"
        self.empty = ttk.Frame(page)           # shown over the list while it is empty (also the first-run screen)
        self.empty_icon = ttk.Label(self.empty)
        if ImageTk is not None:
            self.empty_photo = ImageTk.PhotoImage(make_icon(84))
            self.empty_icon.configure(image=self.empty_photo)
        self.empty_icon.pack(pady=(0, 14))
        self.empty_title = ttk.Label(self.empty, style="Big.TLabel")
        self.empty_title.pack()
        self.empty_hint = ttk.Label(self.empty, style="Hint.TLabel", justify="center")
        self.empty_hint.pack(pady=(8, 16))
        self.empty_actions = ttk.Frame(self.empty)
        self.empty_actions.pack()
        ttk.Button(self.empty_actions, text="Paste from clipboard", style=accent,
                   command=self._on_global_paste_menu).pack(side="left")
        ttk.Button(self.empty_actions, text="Import list …", command=self.on_import).pack(side="left", padx=(8, 0))
        self.empty_bar = ttk.Progressbar(self.empty, mode="indeterminate", length=220)
        self.empty_retry = ttk.Button(self.empty, text="Try again", style=accent, command=self._startup_tools)

    def _setup_panel(self) -> bool:
        """True while the empty list shows the big 'Setting up' screen."""
        return not self.items and self.tools_busy and "setup" in self.notices and find_ytdlp() is None

    def _update_empty(self) -> None:
        """The empty list shows one of three screens: setting up, setup failed, or 'paste a link'."""
        setting_up = self._setup_panel()
        if setting_up != self._setup_panel_shown:
            self._setup_panel_shown = setting_up
            self._render_notice()              # the bar says the same as the big screen: hide it (calls back here)
            return
        if self.items:
            self.empty.place_forget()
            self.empty_bar.stop()
            return
        for w in (self.empty_actions, self.empty_bar, self.empty_retry):
            w.pack_forget()
        if setting_up:
            self.empty_title.configure(text="Setting up …")
            self.empty_hint.configure(text="Downloading yt-dlp - this happens once, on the first start.")
            self.empty_bar.pack()
            self.empty_bar.start(40)
        elif self.setup_error:
            self.empty_bar.stop()
            self.empty_title.configure(text="Setup did not finish")
            self.empty_hint.configure(text=self.setup_error)
            self.empty_retry.pack()
        else:
            self.empty_bar.stop()
            self.empty_title.configure(text="Paste a link to get started")
            self.empty_hint.configure(text=f"Press {'⌘' if IS_MAC else 'Ctrl+'}V anywhere in this window.\n"
                                           "Playlists let you choose the videos first.")
            self.empty_actions.pack()
        self.empty.place(relx=0.5, rely=0.44, anchor="center")

    def _build_history_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        top = ttk.Frame(page)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Search").pack(side="left", padx=(0, 8))
        self.hist_filter = tk.StringVar()
        ttk.Entry(top, textvariable=self.hist_filter).pack(side="left", fill="x", expand=True)
        self.hist_filter.trace_add("write", lambda *_: self.refresh_history())
        self.hist_count = ttk.Label(top, style="Muted.TLabel")
        self.hist_count.pack(side="right", padx=(10, 0))
        self.hist = ttk.Treeview(page, columns=("title", "format", "when", "file"), show="headings",
                                 selectmode="extended")
        for col, text, width, stretch in (("title", "Title", 400, True), ("format", "Format", 170, False),
                                          ("when", "Downloaded", 150, False), ("file", "File", 64, False)):
            self.hist.heading(col, text=text, anchor="w")
            self.hist.column(col, width=width, stretch=stretch, anchor="w")
        self.hist.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(page, command=self.hist.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.hist.configure(yscrollcommand=sb.set)
        self.hist.bind("<Double-1>", lambda e: self.hist_open())
        for seq in (("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            self.hist.bind(seq, self._hist_menu)
        row = ttk.Frame(page)
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(row, text="Open file", command=self.hist_open).pack(side="left")
        ttk.Button(row, text="Show in folder", command=self.hist_reveal).pack(side="left", padx=6)
        ttk.Button(row, text="Download again", command=self.hist_again).pack(side="left")
        ttk.Button(row, text="Clear history", command=self.hist_clear).pack(side="right")
        ttk.Button(row, text="Remove", command=self.hist_remove).pack(side="right", padx=6)

    def _build_log_page(self, page) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        top = ttk.Frame(page)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Filter").pack(side="left", padx=(0, 8))
        self.log_filter = tk.StringVar()
        ttk.Entry(top, textvariable=self.log_filter).pack(side="left", fill="x", expand=True)
        self.log_filter.trace_add("write", lambda *_: self._refilter_log())
        ttk.Button(top, text="Copy", command=self.copy_log).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Clear", command=self.clear_log).pack(side="left", padx=(6, 0))
        self.log_box = tk.Text(page, wrap="none", state="disabled", relief="flat", borderwidth=0,
                               highlightthickness=1, padx=8, pady=8, font=self.font_small)
        self.text_widgets.append(self.log_box)
        self.log_box.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(page, command=self.log_box.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.log_box.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------ tabs, scrolling, small helpers

    def select_tab(self, i: int) -> None:
        self.tabbar.select(i)

    def _show_page(self, i: int) -> None:
        for j, page in enumerate(self.pages):
            if j == i:
                page.grid()
            else:
                page.grid_remove()
        if i == 0:
            self.view.schedule()
        elif i == 2:
            self.log_box.see("end")

    def _bind_wheel(self) -> None:
        """One global wheel handler: it scrolls the queue whenever the pointer is over it (the recycled rows
        are children of the canvas, so per-widget Enter/Leave bindings would keep switching off)."""
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._on_wheel, add="+")

    def _on_wheel(self, event) -> None:
        canvas = self.view.canvas
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None or not str(widget).startswith(str(canvas)) or not canvas.winfo_ismapped():
            return
        if event.num == 4:
            step = -5
        elif event.num == 5:
            step = 5
        elif abs(event.delta) >= 120:          # Windows: multiples of 120
            step = -int(event.delta / 120) * 5
        else:                                  # macOS: small numbers, fine-grained
            step = -event.delta
        canvas.yview_scroll(step, "units")

    def _placeholder(self, show: bool) -> None:
        """Grey hint text inside the empty link field."""
        if show and not self.url_var.get():
            self.placeholder_on = True
            self.url_var.set(self.placeholder_text)
            self.url_entry.configure(foreground=COLORS[getattr(self, "theme", "light")]["muted"])
        elif not show and self.placeholder_on:
            self.placeholder_on = False
            self.url_var.set("")
            self.url_entry.configure(foreground="")

    def toast(self, text: str, ms: int = 3500) -> None:
        """A short message at the bottom of the list that fades away by itself (instead of a dialog)."""
        c = COLORS[self.theme]
        if self.toast_job is not None:
            self.root.after_cancel(self.toast_job)
        self.toast_lbl.configure(text=text, background=c["toast_bg"], foreground=c["toast_fg"])
        self.toast_lbl.place(in_=self.stack, relx=0.5, rely=1.0, y=-14, anchor="s")
        self.toast_lbl.lift()
        self.toast_job = self.root.after(ms, self._hide_toast)

    def _hide_toast(self) -> None:
        self.toast_job = None
        self.toast_lbl.place_forget()

    def set_notice(self, key: str, text: str, *, actions=(), busy: bool = False, dismiss: bool = True) -> None:
        """A bar above the list for things that need attention (setup, update). One shows at a time."""
        self.notices[key] = {"text": text, "actions": list(actions), "busy": busy, "dismiss": dismiss}
        self._render_notice()

    def clear_notice(self, key: str) -> None:
        if self.notices.pop(key, None) is not None:
            self._render_notice()

    def _render_notice(self) -> None:
        for child in self.notice.winfo_children():
            child.destroy()
        keys = [k for k in self.NOTICE_ORDER if k in self.notices and not (k == "setup" and self._setup_panel_shown)]
        key = keys[0] if keys else None
        if key is None:
            self.notice.grid_remove()
            self._update_empty()
            return
        n = self.notices[key]
        self.notice.grid()
        ttk.Label(self.notice, text=n["text"]).pack(side="left")
        if n["busy"]:
            bar = ttk.Progressbar(self.notice, mode="indeterminate", length=90)
            bar.pack(side="left", padx=(12, 0))
            bar.start(40)
        if n["dismiss"]:
            ttk.Button(self.notice, text="×", width=3, command=lambda: self.clear_notice(key)).pack(side="right")
        for label, command in reversed(n["actions"]):
            ttk.Button(self.notice, text=label, command=command).pack(side="right", padx=(0, 6))
        self._update_empty()

    def open_settings(self) -> None:
        """Show the settings window (one instance)."""
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.deiconify()
            self.settings_win.lift()
            return
        self.settings_win = SettingsDialog(self)

    # ------------------------------------------------------------ what to download

    def _on_kind(self) -> None:
        self.quality_var.set(quality_label("mp3" if self.kind_var.get() == "audio" else "video"))
        self.sync_mode_options()

    def mode_key(self) -> str:
        for key, label in UI_QUALITY.items():
            if label == self.quality_var.get():
                return key
        return "video"

    def sync_mode_options(self, *_) -> None:
        """Offer the qualities of the chosen kind and grey out options without effect in audio mode."""
        kind = self.kind_var.get()
        keys = [k for k in MODES if mode_kind(k) == kind]
        self.quality_combo.configure(values=[quality_label(k) for k in keys])
        if self.mode_key() not in keys:
            self.quality_var.set(quality_label(keys[0]))
        audio_only = self.mode_key() in AUDIO_MODES
        self.kind_var.set(mode_kind(self.mode_key()))
        self.also_combo.configure(state="disabled" if audio_only else "readonly")
        self.also_lbl.configure(foreground=COLORS[self.theme]["muted"] if audio_only else "")
        if self.subs_check is not None:
            try:
                self.subs_check.configure(state="disabled" if audio_only else "normal")
            except tk.TclError:
                self.subs_check = None

    # ------------------------------------------------------------ the download folder

    def _update_folder_label(self) -> None:
        self.folder_var.set("Save to  " + tilde_path(Path(self.out_var.get() or DEFAULT_OUT), 30))

    def set_out_dir(self, path: str) -> None:
        self.out_var.set(path)
        self._update_folder_label()
        recent = [p for p in load_settings().get("recent_out", []) if p != path][:5]
        save_settings({"out": path, "recent_out": [path, *recent][:6]})

    def pick_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.set_out_dir(d)

    def _fill_folder_menu(self) -> None:
        menu = self.folder_menu
        menu.delete(0, "end")
        menu.add_command(label="Change folder …", command=self.pick_dir)
        menu.add_command(label="Open folder", command=self.open_out_folder)
        recent = [p for p in load_settings().get("recent_out", []) if p != self.out_var.get()][:5]
        if recent:
            menu.add_separator()
            for p in recent:
                menu.add_command(label=tilde_path(p, 60), command=lambda p=p: self.set_out_dir(p))

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
        self.quality_var.set(quality_label(mode))
        if d.get("out"):
            self.out_var.set(d["out"])
            self._update_folder_label()
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
            self.toast("Type a name for the profile first")
            return
        profiles = dict(load_settings().get("profiles", {}))
        profiles[name] = self.collect_options()
        save_settings({"profiles": profiles})
        self._refresh_profile_list(profiles)
        self.toast(f"Profile '{name}' saved")

    def _refresh_profile_list(self, profiles: dict) -> None:
        if self.profile_combo is not None:
            try:
                self.profile_combo.configure(values=sorted(profiles))
            except tk.TclError:
                self.profile_combo = None      # the settings window is closed

    def load_profile(self) -> None:
        d = load_settings().get("profiles", {}).get(self.profile_var.get())
        if d:
            self.apply_options(d)
            self.toast(f"Profile '{self.profile_var.get()}' loaded")

    def delete_profile(self) -> None:
        name = self.profile_var.get().strip()
        profiles = dict(load_settings().get("profiles", {}))
        if name in profiles and messagebox.askyesno("Profile", f"Delete the profile '{name}'?"):
            del profiles[name]
            save_settings({"profiles": profiles})
            self._refresh_profile_list(profiles)
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

    def _on_global_paste_menu(self) -> None:
        urls = extract_urls(self._clipboard())
        if urls:
            self.add_urls(urls)
        else:
            self.toast("There is no link in the clipboard")

    def on_add(self) -> None:
        text = "" if self.placeholder_on else self.url_var.get()
        urls = extract_urls(text)
        if not urls:
            if text.strip():
                self.toast("That does not look like a link - it must start with http:// or https://")
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

    def add_urls(self, urls: list[str], again: bool = False) -> None:
        """Queue links. A video is in the list once: adding it again jumps to its card instead (a failed or
        cancelled one is retried). again=True (History > Download again) replaces a finished card."""
        self.select_tab(0)
        known = {it.key: it for it in self.items}
        no_playlist, cookies = self.single_var.get(), self.cookies()   # Tk variables: main thread only
        added, seen, first = 0, [], None
        for url in urls:
            old = known.get(url_key(url))
            if old is not None and again and old.finished:
                self._drop([old])
                old = None
            if old is not None:
                if old not in seen:
                    seen.append(old)
                continue
            item = Item(url)
            known[item.key] = item
            self.items.append(item)
            first = first or item
            self.jobs.submit(0, self._info_job, item, no_playlist, cookies)
            added += 1
        retry = [it for it in seen if it.status in ("failed", "cancelled")]
        self._items_changed()
        if retry:
            self.retry_items(retry)
        if seen:                                                       # show what is already there
            self.selected = {seen[-1].id}
            self.anchor = self.cursor = seen[-1].id
            self.view.refresh_rows()
        target = seen[-1] if seen and not added else (self.items[-1] if added == 1 else first)
        if target is not None:
            self.view.scroll_to(target)
        if seen and not added:
            if len(seen) > 1:
                self.toast(f"{len(seen)} links are already in the queue")
            elif retry:
                self.toast("Already in the queue - trying again")
            elif seen[0].status in ("done", "skipped"):
                self.toast("Already downloaded")
            else:
                self.toast("Already in the queue")
        elif added > 1 or seen:
            self.toast(f"Added {added} links" + (f" ({len(seen)} already in the queue)" if seen else ""))

    def _items_changed(self) -> None:
        """The list of items changed (added, removed, moved): drop stale selection, redraw, update counters."""
        ids = {it.id for it in self.items}
        self.selected &= ids
        if self.anchor not in ids:
            self.anchor = None
        if self.cursor not in ids:
            self.cursor = None
        self.view.layout()
        self.update_state()

    def _info_job(self, item: Item, no_playlist: bool, cookies: str) -> None:
        """Worker thread: title, channel, duration and thumbnail of a new link."""
        if item.removed:
            return
        if find_ytdlp() is None:
            self.tools_ready.wait(timeout=180)             # first start: the tools are still being downloaded
        info = None
        if find_ytdlp() is not None and not item.removed:
            info = fetch_info(item.url, no_playlist=no_playlist, cookies_browser=cookies)
        thumb = fetch_thumbnail(info["thumbnail"]) if info and not info["is_playlist"] else None
        self.ui(lambda: self._info_done(item, info, thumb))

    def _info_done(self, item: Item, info: dict | None, thumb) -> None:
        if item.removed:
            return
        if info and info["is_playlist"] and info["entries"]:
            at = self.items.index(item)
            self._drop([item])
            chosen = self._pick_playlist(info)
            if chosen:
                subs = []
                for e in chosen:
                    sub = Item(e["url"])
                    sub.title = e["title"]
                    sub.uploader = e.get("uploader") or info["uploader"]
                    sub.duration = e.get("duration")
                    sub.status = "queued"
                    sub.thumb_url = e.get("thumbnail", "")
                    subs.append(sub)
                self.items[at:at] = subs                   # where the placeholder was
                self.log(f"Playlist '{info['title']}': {len(chosen)} videos added.")
            self._items_changed()
            self._autostart()
            return
        if info:
            item.title = info["title"] or item.url
            item.uploader = info["uploader"]
            item.duration = info["duration"]
            item.thumb_url = info["thumbnail"]
            item.thumb = thumb
            item.thumb_state = "done" if thumb is not None else "failed"
        else:
            item.uploader = "No preview available - will still try to download"
        item.status = "queued"
        self.view.touch(item)
        self.update_state()
        self._autostart()

    def request_thumb(self, item: Item) -> None:
        """Called when a row shows an item: load its thumbnail if it does not have one yet."""
        if item.thumb is not None or item.thumb_state is not None or not item.thumb_url or Image is None:
            return
        item.thumb_state = "loading"
        self.jobs.submit(1, self._thumb_job, item)

    def _thumb_job(self, item: Item) -> None:
        if item.removed:
            return
        img = fetch_thumbnail(item.thumb_url)

        def apply() -> None:
            item.thumb_state = "done" if img is not None else "failed"
            if img is not None and not item.removed:
                item.thumb = img
                self.view.touch(item)
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

    # -- selection and keyboard

    def selected_items(self) -> list[Item]:
        return [it for it in self.items if it.id in self.selected]

    def row_click(self, item: Item, event) -> None:
        self.view.canvas.focus_set()
        shift = bool(event.state & 0x1)
        toggle = bool(event.state & (0x8 if IS_MAC else 0x4))         # Command on macOS, Control elsewhere
        if shift and self.anchor is not None and any(it.id == self.anchor for it in self.items):
            self._select_range(self.anchor, item.id)
        elif toggle:
            self.selected ^= {item.id}
            self.anchor = item.id
        else:
            self.selected = {item.id}
            self.anchor = item.id
        self.cursor = item.id
        self.view.refresh_rows()

    def _select_range(self, a: int, b: int) -> None:
        ids = [it.id for it in self.items]
        i, j = sorted((ids.index(a), ids.index(b)))
        self.selected = set(ids[i:j + 1])

    def _cursor_index(self) -> int:
        for i, it in enumerate(self.items):
            if it.id == self.cursor:
                return i
        return -1

    def key_move(self, delta: int, extend: bool = False) -> str:
        """Up/Down (with Shift: extend the selection); Home/End jump to the ends."""
        if not self.items:
            return "break"
        cur = self._cursor_index()
        if abs(delta) > 1:
            i = 0 if delta < 0 else len(self.items) - 1
        else:
            i = 0 if cur < 0 else min(max(cur + delta, 0), len(self.items) - 1)
        item = self.items[i]
        if extend and self.anchor is not None and any(it.id == self.anchor for it in self.items):
            self._select_range(self.anchor, item.id)
        else:
            self.selected = {item.id}
            self.anchor = item.id
        self.cursor = item.id
        self.view.scroll_to(item)
        self.view.refresh_rows()
        return "break"

    def select_all(self) -> None:
        self.selected = {it.id for it in self.items}
        self.view.refresh_rows()

    def clear_selection(self) -> None:
        self.selected = set()
        self.view.refresh_rows()

    def key_activate(self) -> str:
        targets = self.selected_items()
        if len(targets) == 1:
            self.card_activate(targets[0])
        return "break"

    def key_remove(self) -> str:
        targets = self.selected_items()
        if targets:
            self.remove_items(targets)
        return "break"

    def move_selected(self, delta: int) -> None:
        """Move the selected items up (-1) or down (+1) - downloads start in list order."""
        ids = self.selected
        if not ids:
            return
        order = range(len(self.items)) if delta < 0 else range(len(self.items) - 1, -1, -1)
        for i in order:
            j = i + delta
            if self.items[i].id in ids and 0 <= j < len(self.items) and self.items[j].id not in ids:
                self.items[i], self.items[j] = self.items[j], self.items[i]
        self._items_changed()
        if self.cursor is not None:
            cur = next((it for it in self.items if it.id == self.cursor), None)
            if cur is not None:
                self.view.scroll_to(cur)

    # -- actions on items

    def _drop(self, items: list[Item]) -> None:
        for item in items:
            item.removed = True
            item.stop.set()
            if item in self.items:
                self.items.remove(item)

    def remove_items(self, items: list[Item]) -> None:
        self._drop(items)
        self._items_changed()
        self.pump()

    def remove_item(self, item: Item) -> None:
        self.remove_items([item])

    def cancel_item(self, item: Item) -> None:
        if item.status == "paused":            # nothing is running: just drop the hold (the partial data stays)
            item.status = "cancelled"
            self.view.touch(item)
            self.update_state()
        else:
            item.stop.set()

    def pause_item(self, item: Item) -> None:
        """Stop the download but keep what has been downloaded: 'Resume' continues from there."""
        if item.status == "downloading" and not item.pause_requested:
            item.pause_requested = True
            item.stop.set()                    # the card turns to 'Paused' when the process has ended
            self.view.touch(item)

    def resume_item(self, item: Item) -> None:
        if item.status != "paused":
            return
        item.stop = threading.Event()
        item.pause_requested = False
        item.status = "queued"
        item.retry_at = 0.0
        self.view.touch(item)
        self.start_item(item)

    def pause_all(self) -> None:
        """Pause everything that is running and hold the rest of the queue until 'Resume all'."""
        self.running = False
        for it in self.items:
            self.pause_item(it)
        self.update_state()

    stop_all = pause_all                       # the old name

    def cancel_running(self) -> None:
        self.running = False
        for it in self.items:
            if it.status == "downloading":
                it.stop.set()
        self.update_state()

    def start_over(self, items: list[Item]) -> None:
        """Delete the partial data and download again from the beginning."""
        freed = 0
        todo = [it for it in items if it.status in ("paused", "failed", "cancelled")]
        for it in todo:
            freed += discard_partial(it.dests)
            it.dests, it.interrupted, it.resumed = [], False, False
            self._reset(it)
        if todo:
            self.toast(f"Deleted {fmt_size(freed)} of partial data" if freed else "Starting over")
            self.update_state()
            self.start_all()

    def toggle_pause(self) -> str:
        """Space: pause what is running in the selection, resume what is paused."""
        targets = self.selected_items()
        for it in targets:
            if it.status == "downloading":
                self.pause_item(it)
            elif it.status == "paused":
                self.resume_item(it)
        return "break"

    def _ensure_retry_tick(self) -> None:
        if self._retry_job is None:
            self._retry_job = self.root.after(1000, self._retry_tick)

    def _retry_tick(self) -> None:
        """Once a second while an automatic retry is pending: update the countdown, start it when due."""
        self._retry_job = None
        now = time.time()
        waiting = [it for it in self.items if it.status == "queued" and it.retry_at]
        for it in waiting:
            self.view.touch(it)
        if any(it.retry_at <= now for it in waiting):
            self.pump()
        if waiting:
            self._ensure_retry_tick()

    def _reset(self, item: Item) -> None:
        item.stop = threading.Event()
        item.status, item.pct, item.error = "queued", 0.0, ""
        item.speed = item.eta = item.size = ""
        item.retries, item.retry_at, item.pause_requested = 0, 0.0, False
        self.view.touch(item)

    def retry_item(self, item: Item) -> None:
        self.retry_items([item])

    def retry_items(self, items: list[Item]) -> None:
        todo = [it for it in items if it.status in ("failed", "cancelled")]
        for it in todo:
            self._reset(it)
        if todo:
            self.update_state()
            self.start_all()

    def retry_failed(self) -> None:
        self.retry_items([it for it in self.items if it.status == "failed"])

    def update_and_retry(self, items: list[Item]) -> None:
        self.on_update_ytdlp(after=lambda: self.retry_items(items))

    def get_ffmpeg_tools(self, after=None) -> None:
        """Download ffmpeg + ffprobe (macOS: explain brew)."""
        if ffmpeg_build_name() is None:
            messagebox.showinfo("ffmpeg tools", ffmpeg_hint())
            return
        if self.tools_busy or any(it.status == "downloading" for it in self.items):
            self.toast("Please wait until the current downloads have finished")
            return
        self.set_tools_busy(True, "Downloading ffmpeg with ffprobe (about 150 MB, one time) …")

        def work():
            ok = install_ffmpeg(self.log)

            def done():
                self.set_tools_busy(False)
                self.toast("ffmpeg tools are ready" if ok else "Could not download ffmpeg - see the Log")
                if ok and after is not None:
                    after()
            self.ui(done)
        threading.Thread(target=work, daemon=True).start()

    def start_item(self, item: Item) -> None:
        """Start one waiting item right now (even if 'Download all' has not been pressed)."""
        if item.status != "queued":
            return
        if self.tools_busy:
            self.toast("yt-dlp is being set up - try again in a moment")
            return
        if not self.running:
            self.batch = {"ok": 0, "bad": 0}
            self.batch_ids = set()
        self.running = True
        self.batch_ids.add(item.id)
        self._launch(item)
        self.pump()

    def show_item(self, item: Item) -> None:
        if item.path and Path(item.path).exists():
            reveal_file(Path(item.path))
        else:
            open_folder(Path(self.out_var.get() or DEFAULT_OUT))

    def open_item_options(self, item: Item) -> None:
        if item.status == "downloading":
            self.toast("Cancel the download first to change its options")
            return
        dialog = ItemDialog(self, item)
        self.root.wait_window(dialog)
        self.view.touch(item)
        self.update_state()

    def card_activate(self, item: Item) -> None:
        """Double click: open the finished file, otherwise the options of the item."""
        if item.status in ("done", "skipped"):
            if item.path and Path(item.path).exists():
                open_path(Path(item.path))
        elif item.status != "downloading":
            self.open_item_options(item)

    def copy_links(self, items: list[Item]) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(it.url for it in items))
        self.toast("Link copied" if len(items) == 1 else f"{len(items)} links copied")

    def card_menu(self, item: Item, event, anchor=None) -> None:
        """The menu of a row (right click or the '…' button). It works on the whole selection."""
        if item.id not in self.selected:
            self.selected, self.anchor, self.cursor = {item.id}, item.id, item.id
            self.view.refresh_rows()
        targets = self.selected_items() or [item]
        menu = tk.Menu(self.root, tearoff=0)
        if len(targets) == 1:
            st = item.status
            if st in ("queued", "failed", "cancelled"):
                menu.add_command(label="Options …", command=lambda: self.open_item_options(item))
            if st == "queued":
                menu.add_command(label="Download now", command=lambda: self.start_item(item))
            if st == "downloading":
                menu.add_command(label="Pause", command=lambda: self.pause_item(item))
                menu.add_command(label="Cancel", command=lambda: self.cancel_item(item))
            if st == "paused":
                menu.add_command(label="Resume", command=lambda: self.resume_item(item))
                menu.add_command(label="Cancel", command=lambda: self.cancel_item(item))
            if st in ("failed", "cancelled"):
                menu.add_command(label="Retry (continues where it stopped)", command=lambda: self.retry_item(item))
            if st in ("paused", "failed", "cancelled") and partial_size(item.dests):
                menu.add_command(label=f"Start over (delete {fmt_size(partial_size(item.dests))} partial data)",
                                 command=lambda: self.start_over([item]))
            if st == "failed" and needs_ffprobe(item.error):
                menu.add_command(label="Get ffmpeg tools, then retry",
                                 command=lambda: self.get_ffmpeg_tools(after=lambda: self.retry_items([item])))
            if st == "failed" and suggests_update(item.error):
                menu.add_command(label="Update yt-dlp, then retry", command=lambda: self.update_and_retry([item]))
            if st in ("done", "skipped"):
                if item.path and Path(item.path).exists():
                    menu.add_command(label="Open file", command=lambda: open_path(Path(item.path)))
                menu.add_command(label="Show in folder", command=lambda: self.show_item(item))
            menu.add_separator()
            menu.add_command(label="Copy link", command=lambda: self.copy_links([item]))
            menu.add_command(label="Open link in browser", command=lambda: webbrowser.open(item.url))
            if item.error and st == "failed":
                menu.add_command(label="Copy error message", command=lambda: (
                    self.root.clipboard_clear(), self.root.clipboard_append(item.error)))
        else:
            failed = [it for it in targets if it.status in ("failed", "cancelled")]
            running = [it for it in targets if it.status == "downloading"]
            paused = [it for it in targets if it.status == "paused"]
            if failed:
                menu.add_command(label=f"Retry {len(failed)}", command=lambda: self.retry_items(failed))
            if running:
                menu.add_command(label=f"Pause {len(running)}", command=lambda: [self.pause_item(it) for it in running])
                menu.add_command(label=f"Cancel {len(running)}",
                                 command=lambda: [self.cancel_item(it) for it in running])
            if paused:
                menu.add_command(label=f"Resume {len(paused)}", command=lambda: [self.resume_item(it) for it in paused])
            menu.add_command(label=f"Copy {len(targets)} links", command=lambda: self.copy_links(targets))
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self.move_selected(-1))
        menu.add_command(label="Move down", command=lambda: self.move_selected(1))
        menu.add_separator()
        menu.add_command(label="Remove" if len(targets) == 1 else f"Remove {len(targets)} items",
                         command=lambda: self.remove_items(targets))
        if anchor is not None:
            x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height()
        else:
            x, y = event.x_root, event.y_root
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def clear_finished(self) -> None:
        self._drop([it for it in self.items if it.finished])
        self._items_changed()

    def start_all(self) -> None:
        if not self.running:
            self.batch = {"ok": 0, "bad": 0}
            self.batch_ids = set()
        self.running = True
        for it in self.items:                  # 'Download all' also resumes paused items and skips retry waits
            if it.status == "paused":
                it.stop = threading.Event()
                it.pause_requested = False
                it.status = "queued"
            if it.status == "queued":
                it.retry_at = 0.0
                self.view.touch(it)
        self.batch_ids |= {it.id for it in self.items if it.status in ("queued", "downloading")}
        self.save_options()
        self.pump()

    def pump(self) -> None:
        """Start waiting downloads while there is a free slot; finish the batch when nothing is left."""
        if self.running and not self.tools_busy:
            active = sum(1 for it in self.items if it.status == "downloading")
            now = time.time()
            for it in self.items:
                if active >= self.parallel():
                    break
                if it.status == "queued" and it.retry_at <= now:
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
        if item.interrupted:
            self._repair_partial(item)
        item.status, item.mode = "downloading", opts["mode"]
        if not item.dests:                     # fresh start; with partial data yt-dlp continues and reports real progress
            item.pct = 0.0
        item.speed = item.eta = item.size = item.error = ""
        item.retry_at, item.resumed, item.pause_requested = 0.0, False, False
        self.batch_ids.add(item.id)
        self.view.touch(item)
        threading.Thread(target=self._download_worker, args=(item, opts), daemon=True).start()

    def _repair_partial(self, item: Item) -> None:
        """The last run ended uncleanly: fetch the end of the partial files again instead of trusting it."""
        item.interrupted = False
        cut = trim_partial_tail(item.dests)
        if cut:
            self.log(f"[{shorten(item.title, 28)}] Interrupted earlier - re-fetching the last "
                     f"{fmt_size(cut)} of the partial file to be safe.")

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
        state = {"skipped": False, "last": 0.0, "error_open": False}

        def refresh_card() -> None:
            if not item.removed:
                self.view.touch(item)
                self._update_title()

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
            dest = DEST_RE.match(line.strip())
            if dest:
                if dest.group("path") not in item.dests:
                    item.dests.append(dest.group("path"))
                    self.ui(self._schedule_queue_save)
                if item.title == item.url:
                    item.title = INTERMEDIATE_RE.sub("", Path(dest.group("path")).name)
                    self.ui(refresh_card)
            if RESUME_RE.search(line):
                item.resumed = True
                self.ui(refresh_card)
            if ARCHIVED_RE.search(line):
                state["skipped"] = True
            if line.startswith("ERROR"):
                item.error = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", line).strip()
                state["error_open"] = not item.error           # yt-dlp sometimes puts the message on the next line
            elif line.strip() and (state.get("error_open") or ("giving up after" in line.lower() and not item.error)):
                item.error = re.sub(r"^\[[^\]]+\]\s*", "", line.strip())
                state["error_open"] = False
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
            item.retries = 0
        elif rc == -1:
            item.status = "paused" if item.pause_requested else "cancelled"
            item.pause_requested = False
        else:
            delay = self._auto_retry_delay(item)
            if delay is None:
                item.status = "failed"
                self.batch["bad"] += 1
            else:                              # network trouble: wait a little, then continue where it stopped
                item.status = "queued"
                item.retry_at = time.time() + delay
                self.log(f"[{shorten(item.title, 28)}] Network problem - retrying in {delay} s "
                         f"(attempt {item.retries} of {len(AUTO_RETRY_DELAYS)}).")
                self._ensure_retry_tick()
        self.view.touch(item)
        self.pump()

    def _auto_retry_delay(self, item: Item) -> "int | None":
        """Seconds until the next automatic retry, or None if the error is not worth one (or they are used up)."""
        if not is_transient_error(item.error) or item.retries >= len(AUTO_RETRY_DELAYS):
            return None
        delay = AUTO_RETRY_DELAYS[item.retries]
        item.retries += 1
        return delay

    # ------------------------------------------------------------ state display

    def update_state(self) -> None:
        counts = {s: 0 for s in STATUS_ORDER}
        speed = 0.0
        for it in self.items:
            counts[it.status] += 1
            if it.status == "downloading":
                speed += parse_speed(it.speed)
        self.tabbar.set_summary(summary_options(counts, speed))
        self.tabbar.set_text(0, f"Queue ({len(self.items)})" if self.items else "Queue")

        startable = counts["queued"] + counts["paused"]
        self.start_btn.configure(
            state="normal" if startable and not self.tools_busy else "disabled",
            text=(("Resume all" if counts["paused"] else "Download all") + (f" ({startable})" if startable else "")))
        if any(it.finished for it in self.items):
            self.clear_btn.grid()
        else:
            self.clear_btn.grid_remove()
        if counts["downloading"] or self.running:
            self.stop_btn.grid()
        else:
            self.stop_btn.grid_remove()
        self._update_empty()
        self._update_title()
        if self.restored:                      # not before the old queue is back, or it would be overwritten
            self._schedule_queue_save()

    def _update_title(self) -> None:
        """While downloading, the window title shows the overall progress (visible in the task bar / Dock)."""
        batch = [it for it in self.items if it.id in self.batch_ids and it.status != "cancelled"]
        if self.running and batch:
            done = sum(100.0 if it.status in ("done", "skipped", "failed") else it.pct for it in batch)
            title = f"{done / len(batch):.0f}%  ·  {self.base_title}"
        else:
            title = self.base_title
        if self.root.title() != title:
            self.root.title(title)

    # ------------------------------------------------------------ queue on disk, clipboard watcher

    def _schedule_queue_save(self) -> None:
        if self.save_job is None:
            self.save_job = self.root.after(400, self._save_queue)

    def _save_queue(self) -> None:
        """Keep everything that is not finished successfully, so a restart does not lose the queue."""
        self.save_job = None
        keep = []
        for it in self.items:
            if it.status in ("done", "skipped"):
                continue
            # a download that is running when this is written counts as interrupted if the app never gets to
            # save it as paused (crash, power loss): the next start repairs its partial files before resuming
            status = "paused" if it.status in ("downloading", "paused") else (
                it.status if it.status in ("failed", "cancelled") else "queued")
            keep.append({"url": it.url, "title": it.title, "uploader": it.uploader, "duration": it.duration,
                         "thumb_url": it.thumb_url, "overrides": it.overrides, "status": status,
                         "pct": it.pct, "size": it.size, "dests": it.dests,
                         "interrupted": it.status == "downloading" or it.interrupted})
        save_queue(keep)

    def _restore_queue(self) -> None:
        no_playlist, cookies = self.single_var.get(), self.cookies()
        for d in load_queue():
            item = Item(d["url"])
            item.title = d.get("title") or item.url
            item.uploader = d.get("uploader") or ""
            item.duration = d.get("duration")
            item.thumb_url = d.get("thumb_url") or ""
            item.overrides = d.get("overrides") if isinstance(d.get("overrides"), dict) else {}
            item.status = d.get("status") if d.get("status") in ("failed", "cancelled", "paused") else "queued"
            item.pct = float(d.get("pct") or 0)
            item.size = str(d.get("size") or "")
            item.dests = [str(x) for x in d.get("dests") or []]
            item.interrupted = bool(d.get("interrupted"))
            if item.title == item.url and item.status == "queued":       # never got its preview
                item.status = "fetching"
                self.jobs.submit(0, self._info_job, item, no_playlist, cookies)
            self.items.append(item)
        self.restored = True
        if self.items:
            self.log(f"{len(self.items)} item(s) restored from the last session.")
        self._items_changed()
        paused = sum(1 for it in self.items if it.status == "paused")
        if paused:
            self.set_notice("restore", f"{paused} download{'s were' if paused > 1 else ' was'} paused when the app "
                                       "was closed. They continue where they stopped.",
                            actions=[("Resume all", lambda: (self.clear_notice("restore"), self.start_all()))])

    def _clip_watch_toggled(self) -> None:
        if self.clip_watch_var.get():          # what is in the clipboard right now is not "new"
            self.watch_last = self._clipboard().strip()

    def _watch_clipboard(self) -> None:
        """While enabled in the settings, every new link copied anywhere is added to the queue."""
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
        needle = self.hist_filter.get().strip().lower()
        shown = 0
        for i in range(len(self.history) - 1, -1, -1):
            h = self.history[i]
            if needle and needle not in (h.get("title", "") + " " + h.get("url", "")).lower():
                continue
            missing = bool(h.get("path")) and not Path(h["path"]).exists()
            self.hist.insert("", "end", iid=str(i), tags=("missing",) if missing else (),
                             values=(h.get("title", ""), mode_label(h.get("mode", "")),
                                     humanize_when(h.get("time", 0)), "missing" if missing else "✓"))
            shown += 1
        total = len(self.history)
        self.hist_count.configure(text=(f"{shown} of {total}" if needle else f"{total} downloads") if total else "")
        self._history_tags()

    def _selected_history(self) -> list[dict]:
        return [self.history[int(i)] for i in self.hist.selection()]

    def _hist_menu(self, event) -> None:
        row = self.hist.identify_row(event.y)
        if row and row not in self.hist.selection():
            self.hist.selection_set(row)
        if not self.hist.selection():
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Open file", command=self.hist_open)
        menu.add_command(label="Show in folder", command=self.hist_reveal)
        menu.add_separator()
        menu.add_command(label="Copy link", command=self.hist_copy)
        menu.add_command(label="Download again", command=self.hist_again)
        menu.add_separator()
        menu.add_command(label="Remove from history", command=self.hist_remove)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def hist_open(self) -> None:
        for h in self._selected_history()[:1]:
            if h.get("path") and Path(h["path"]).exists():
                open_path(Path(h["path"]))
            else:
                self.toast("The file is no longer at its old location")

    def hist_reveal(self) -> None:
        for h in self._selected_history()[:1]:
            reveal_file(Path(h["path"])) if h.get("path") else None

    def hist_copy(self) -> None:
        urls = [h["url"] for h in self._selected_history() if h.get("url")]
        if urls:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(urls))
            self.toast("Link copied" if len(urls) == 1 else f"{len(urls)} links copied")

    def hist_again(self) -> None:
        self.add_urls([h["url"] for h in self._selected_history() if h.get("url")], again=True)

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

    # ------------------------------------------------------------ log

    def log(self, msg: str) -> None:
        def _append() -> None:
            line = str(msg)
            self.log_lines.append(line)
            needle = self.log_filter.get().strip().lower()
            if needle and needle not in line.lower():
                return
            at_end = self.log_box.yview()[1] >= 0.999                  # do not yank the view while reading
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line + "\n")
            if int(self.log_box.index("end-1c").split(".")[0]) > 3000:
                self.log_box.delete("1.0", "500.0")
            if at_end:
                self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.ui(_append)

    def _refilter_log(self) -> None:
        needle = self.log_filter.get().strip().lower()
        lines = [ln for ln in self.log_lines if not needle or needle in ln.lower()]
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("end", "\n".join(lines) + ("\n" if lines else ""))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def copy_log(self) -> None:
        needle = self.log_filter.get().strip().lower()
        text = "\n".join(ln for ln in self.log_lines if not needle or needle in ln.lower())
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.toast("Log copied to the clipboard")

    def clear_log(self) -> None:
        self.log_lines.clear()
        self._refilter_log()

    # ------------------------------------------------------------ menu bar, quitting

    def _build_menu(self) -> None:
        """Native menu bar: at the top of the screen on macOS, inside the window on Windows/Linux."""
        root, mac = self.root, IS_MAC
        mod = "Command" if mac else "Control"
        acc = (lambda key: f"⌘{key}") if mac else (lambda key: f"Ctrl+{key}")
        bar = tk.Menu(root, tearoff=0)
        self.menubar = bar

        if mac:                                # the application menu ("simple-ytdlp") must come first
            app_menu = tk.Menu(bar, name="apple", tearoff=0)
            app_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)
            bar.add_cascade(menu=app_menu)
            root.createcommand("tk::mac::ShowPreferences", self.open_settings)
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
        queue_menu.add_command(label="Download / resume all", accelerator=acc("Return") if not mac else "⌘↩",
                               command=self.start_all)
        queue_menu.add_command(label="Pause all", accelerator=acc("."), command=self.pause_all)
        queue_menu.add_command(label="Cancel running", command=self.cancel_running)
        queue_menu.add_command(label="Retry failed", command=self.retry_failed)
        queue_menu.add_separator()
        queue_menu.add_command(label="Select all", accelerator=acc("A"), command=self.select_all)
        queue_menu.add_command(label="Remove selected", accelerator="Delete", command=self.key_remove)
        queue_menu.add_command(label="Move up", accelerator="Alt+↑" if not mac else "⌥↑",
                               command=lambda: self.move_selected(-1))
        queue_menu.add_command(label="Move down", accelerator="Alt+↓" if not mac else "⌥↓",
                               command=lambda: self.move_selected(1))
        queue_menu.add_separator()
        queue_menu.add_command(label="Clear finished", command=self.clear_finished)
        bar.add_cascade(label="Queue", menu=queue_menu)

        view_menu = tk.Menu(bar, tearoff=0)
        for label, value in (("Follow system theme", "system"), ("Light theme", "light"), ("Dark theme", "dark")):
            view_menu.add_radiobutton(label=label, value=value, variable=self.theme_mode,
                                      command=self.set_theme_mode, state="normal" if sv_ttk else "disabled")
        view_menu.add_separator()
        view_menu.add_command(label="Settings …", accelerator=acc(","), command=self.open_settings)
        for i, name in enumerate(("Queue", "History", "Log")):
            view_menu.add_command(label=f"Show {name}", accelerator=acc(str(i + 1)),
                                  command=lambda i=i: self.select_tab(i))
        bar.add_cascade(label="View", menu=view_menu)

        tools_menu = tk.Menu(bar, tearoff=0)
        tools_menu.add_command(label="Update yt-dlp", command=self.on_update_ytdlp)
        tools_menu.add_command(label="Get ffmpeg tools (ffmpeg + ffprobe) …", command=self.get_ffmpeg_tools)
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
        bind("period", self.pause_all)
        for i in range(3):
            bind(f"Key-{i + 1}", lambda i=i: self.select_tab(i))
        if not mac:                            # on macOS the application menu already provides these
            bind("comma", self.open_settings)
            bind("q", self.on_close)

        # the queue list: keyboard navigation while it has the focus
        c = self.view.canvas
        c.bind("<Up>", lambda e: self.key_move(-1))
        c.bind("<Down>", lambda e: self.key_move(1))
        c.bind("<Shift-Up>", lambda e: self.key_move(-1, extend=True))
        c.bind("<Shift-Down>", lambda e: self.key_move(1, extend=True))
        c.bind("<Home>", lambda e: self.key_move(-2))
        c.bind("<End>", lambda e: self.key_move(2))
        c.bind("<Return>", lambda e: self.key_activate())
        c.bind("<Delete>", lambda e: self.key_remove())
        c.bind("<BackSpace>", lambda e: self.key_remove())
        c.bind("<Escape>", lambda e: self.clear_selection())
        c.bind("<space>", lambda e: self.toggle_pause())
        c.bind(f"<{mod}-a>", lambda e: (self.select_all(), "break")[1])
        c.bind("<Alt-Up>" if not mac else "<Option-Up>", lambda e: (self.move_selected(-1), "break")[1])
        c.bind("<Alt-Down>" if not mac else "<Option-Down>", lambda e: (self.move_selected(1), "break")[1])

    def open_out_folder(self) -> None:
        open_folder(Path(self.out_var.get() or DEFAULT_OUT))

    def show_about(self) -> None:
        AboutDialog(self)

    def on_close(self) -> None:
        """Quit: ask first while downloads run, and stop their yt-dlp/ffmpeg processes."""
        running = [it for it in self.items if it.status == "downloading"]
        if running and not messagebox.askyesno(
                "Quit", f"{len(running)} download(s) are still running. Pause them and quit?\n"
                        "They continue where they stopped the next time you start the app."):
            return
        self.running = False
        for it in running:
            it.status, it.interrupted = "paused", False    # saved as paused - a clean stop, nothing to repair
            it.pause_requested = True
            it.stop.set()                                   # the watcher threads kill the process trees
        self._save_queue()
        try:
            save_settings({"size": f"{self.root.winfo_width()}x{self.root.winfo_height()}"})
        except tk.TclError:
            pass
        self.root.withdraw()
        self.root.after(800 if running else 0, self.root.destroy)

    # ------------------------------------------------------------ tools & updates

    def set_tools_busy(self, busy: bool, text: str = "") -> None:
        self.tools_busy = busy
        if busy:
            self.set_notice("setup", text, busy=True, dismiss=False)
        else:
            self.clear_notice("setup")
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
            self.tools_ready.set()
            return
        if find_ytdlp() is not None:
            self.tools_ready.set()             # usable already - only deno or an update is pending
        self.setup_error = ""
        self.clear_notice("setup-error")
        self.set_tools_busy(True, "Setting up: downloading yt-dlp (first start only) …" if missing
                            else "Checking for yt-dlp updates …")

        def work():
            ok = True
            try:
                if missing:
                    ensure_tools(self.log)
                    ok = find_ytdlp() is not None
                else:
                    ok = update_ytdlp(self.log)
                if ok:
                    save_settings({"last_update": time.time()})
            finally:
                def done():
                    self.tools_ready.set()
                    self.set_tools_busy(False)
                    if missing and find_ytdlp() is None:
                        self.setup_error = "Could not download yt-dlp. Check your internet connection."
                        self.set_notice("setup-error", self.setup_error, dismiss=False,
                                        actions=[("Try again", self._startup_tools)])
                self.ui(done)

        threading.Thread(target=work, daemon=True).start()

    def on_update_ytdlp(self, after=None) -> None:
        if self.tools_busy or any(it.status == "downloading" for it in self.items):
            self.toast("Please wait until the current downloads have finished")
            return
        self.set_tools_busy(True, "Updating yt-dlp …")

        def work():
            try:
                if update_ytdlp(self.log):
                    save_settings({"last_update": time.time()})
                if find_js_runtime() is None:
                    install_deno(self.log)
            finally:
                def done():
                    self.set_tools_busy(False)
                    self.toast("yt-dlp is up to date")
                    if after is not None:
                        after()
                self.ui(done)

        threading.Thread(target=work, daemon=True).start()

    def _check_app_update(self) -> None:       # newer release on GitHub? (silent if not)
        found = check_app_update()
        if found:
            self.ui(lambda: self.show_app_update(found))

    def check_app_update_manually(self) -> None:
        def work():
            found = check_app_update()
            if found:
                self.ui(lambda: (self.show_app_update(found), self.select_tab(0)))
            else:
                self.ui(lambda: self.toast(f"{APP_NAME} is up to date" if __version__ != "dev"
                                           else "Development version - the update check is disabled"))
        threading.Thread(target=work, daemon=True).start()

    def show_app_update(self, update: dict) -> None:
        actions = [("Release page", lambda: webbrowser.open(update["page"]))]
        if can_self_update() and update["asset_url"]:
            actions.insert(0, ("Update now", lambda: self.run_self_update(update)))
        self.set_notice("update", f"A new version of {APP_NAME} is available: v{update['version']}",
                        actions=actions)
        self.log(f"New version available: v{update['version']}  ->  {update['page']}")

    def run_self_update(self, update: dict) -> None:
        if any(it.status == "downloading" for it in self.items) or self.tools_busy:
            self.toast("Please wait until the current downloads have finished (or cancel them)")
            return
        self.set_tools_busy(True, f"Updating {APP_NAME} …")

        def work():
            done = install_app_update(update, self.log)

            def finish():
                if done:
                    self.root.destroy()        # the new version has already been started
                    return
                self.set_tools_busy(False)
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
    p.add_argument("--make-icons", metavar="DIR", help=argparse.SUPPRESS)      # used by the release build

    args, unknown = p.parse_known_args(argv)

    if args.make_icons:
        print("\n".join(write_icons(args.make_icons)))
        return 0
    migrate_legacy_data()
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
