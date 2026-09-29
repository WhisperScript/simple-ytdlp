#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["imageio-ffmpeg", "certifi"]
# ///
"""
ytdl.py - plattformuebergreifendes Frontend fuer yt-dlp (macOS, Linux, Windows).

yt-dlp und deno (JS-Engine fuer YouTube) werden beim ersten Start automatisch in den
Nutzerordner geladen und lassen sich per Knopfdruck aktualisieren. ffmpeg kommt
mitgeliefert (imageio-ffmpeg), falls kein System-ffmpeg da ist.

Einfachster Weg (nur uv noetig):
    uv run ytdl.py
    ./ytdl.py                      (macOS/Linux, nach chmod +x)

Ohne uv, mit vorhandenem Python (dann: pip install imageio-ffmpeg certifi):
    python3 ytdl.py "https://youtube.com/watch?v=XXXX"
    python3 ytdl.py -a urls.txt --mode mp3
    python3 ytdl.py --gui

Als fertige Programme (Windows/macOS/Linux): siehe README, Abschnitt "Fertige Programme".
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
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "ytdl"
DEFAULT_OUT = Path.home() / "Downloads" / "yt-dlp"

MODES = {
    "video":      "Video - beste Qualitaet",
    "video1080":  "Video - max. 1080p",
    "video720":   "Video - max. 720p",
    "mp3":        "Audio - mp3",
    "m4a":        "Audio - m4a",
    "opus":       "Audio - opus",
}
AUDIO_MODES = ("mp3", "m4a", "opus")

BROWSERS = ["", "chrome", "firefox", "safari", "edge", "brave", "chromium", "vivaldi", "opera"]


# ---------------------------------------------------------------- Ordner & Einstellungen

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
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _no_window() -> dict:
    """Unter Windows kein Konsolenfenster aufblitzen lassen."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ---------------------------------------------------------------- Tools laden

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
    try:                                   # gepackte Pythons haben oft keine CA-Zertifikate
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
    """Laedt die aktuelle yt-dlp-Standalone-Binary in den Nutzerordner."""
    with _tools_lock:
        url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{_ytdlp_asset()}"
        log("Lade yt-dlp ...")
        try:
            _fetch(url, managed_ytdlp(), log)
            _make_executable(managed_ytdlp())
        except Exception as e:
            log(f"FEHLER beim Laden von yt-dlp: {e}")
            return False
        log("yt-dlp bereit.")
        return True


def update_ytdlp(log=print) -> bool:
    """Aktualisiert yt-dlp (laedt es, falls noch nicht vorhanden)."""
    if not managed_ytdlp().exists():
        return install_ytdlp(log)
    log("Pruefe auf yt-dlp-Update ...")
    return _stream([str(managed_ytdlp()), "-U"], log) == 0


def install_deno(log=print) -> bool:
    with _tools_lock:
        url = f"https://github.com/denoland/deno/releases/latest/download/deno-{_deno_triple()}.zip"
        zpath = BIN_DIR / "deno.zip"
        log("Lade deno (JS-Engine fuer YouTube) ...")
        try:
            _fetch(url, zpath, log)
            with zipfile.ZipFile(zpath) as zf:
                zf.extract(_exe("deno"), BIN_DIR)
            zpath.unlink(missing_ok=True)
            _make_executable(managed_deno())
        except Exception as e:
            log(f"FEHLER beim Laden von deno: {e}")
            return False
        log("deno bereit.")
        return True


def ensure_tools(log=print) -> bool:
    """Stellt sicher, dass yt-dlp und eine JS-Engine da sind. False = yt-dlp fehlt."""
    if find_ytdlp() is None and not install_ytdlp(log):
        return False
    if find_js_runtime() is None:
        install_deno(log)                  # nicht kritisch: nur hochaufloesende Formate
    return True


# ---------------------------------------------------------------- Tools finden

def find_ytdlp() -> list[str] | None:
    """Liefert das Kommando-Prefix fuer yt-dlp oder None."""
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
    """(Pfad, Quelle) des zu nutzenden ffmpeg. Quelle: system | bundled | none."""
    exe = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if exe:
        return exe, "system"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe(), "bundled"
    except Exception:
        return None, "none"


def find_js_runtime() -> list[str] | None:
    """yt-dlp-Argumente fuer die JS-Engine (YouTube braucht sie fuer alle Formate)."""
    if managed_deno().exists():
        return ["--js-runtimes", f"deno:{managed_deno()}"]
    for name in ("deno", "node", "bun"):
        path = shutil.which(name) or shutil.which(name + ".exe")
        if path:
            return ["--js-runtimes", f"{name}:{path}"]
    return None


def ffmpeg_hint() -> str:
    if sys.platform == "darwin":
        return "ffmpeg fehlt - installieren mit:  brew install ffmpeg"
    if os.name == "nt":
        return "ffmpeg fehlt - installieren mit:  winget install Gyan.FFmpeg"
    return "ffmpeg fehlt - installieren mit:  sudo apt install ffmpeg  (bzw. dnf/pacman)"


# ---------------------------------------------------------------- Argumentbau

def build_args(mode: str, out_dir: Path, *, subs=False, thumb=False,
               archive=False, cookies_browser="", sort_by_uploader=False,
               no_playlist=False, extra: list[str] | None = None) -> list[str]:
    out_dir = Path(out_dir).expanduser()
    tmpl = "%(uploader)s/%(title)s.%(ext)s" if sort_by_uploader else "%(title)s.%(ext)s"

    args = [
        "-o", str(out_dir / tmpl),
        "--no-overwrites",
        "--retries", "3",
        "--embed-metadata",
        "--newline",              # Fortschritt zeilenweise -> gut fuers Log
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

    if subs and mode not in AUDIO_MODES:      # Untertitel lassen sich nicht in Audio einbetten
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", "de,en", "--embed-subs"]
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


# ---------------------------------------------------------------- Ausfuehrung

def _kill_tree(proc: subprocess.Popen) -> None:
    """Beendet den Prozess samt Kindern (yt-dlp startet ffmpeg)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, **_no_window())
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _stream(cmd: list[str], log, stop_flag=None) -> int:
    """Startet cmd und schiebt jede Ausgabezeile an log(). Liefert Returncode."""
    extra = _no_window()
    if os.name != "nt":
        extra["start_new_session"] = True     # eigene Prozessgruppe -> sauber abbrechbar
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace", **extra
        )
    except OSError as e:
        log(f"FEHLER: {e}")
        return 127

    if stop_flag is not None:
        def watch():                          # greift auch, wenn gerade keine Ausgabe kommt
            while proc.poll() is None:
                if stop_flag.wait(0.3):
                    _kill_tree(proc)
                    log("-- abgebrochen --")
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


def download(urls: list[str], args: list[str], out_dir: Path, log=print, stop_flag=None) -> tuple[int, int]:
    if not ensure_tools(log):
        log("Konnte yt-dlp nicht laden. Internetverbindung pruefen und erneut versuchen.")
        return (0, len(urls))
    base = find_ytdlp()
    assert base is not None

    ffmpeg, source = find_ffmpeg()
    if source == "bundled":
        args = args + ["--ffmpeg-location", ffmpeg]
    elif source == "none":
        log("WARNUNG: " + ffmpeg_hint())

    js = find_js_runtime()
    if js:
        args = args + js
    else:
        log("Hinweis: keine JS-Engine gefunden - evtl. fehlen hochaufloesende Formate.")

    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    failed_log = out_dir / "failed.log"

    ok = bad = 0
    for i, url in enumerate(urls, 1):
        if stop_flag is not None and stop_flag.is_set():
            log("-- abgebrochen --")
            break
        log(f"\n[{i}/{len(urls)}] {url}")
        rc = _stream(base + args + ["--", url], log, stop_flag)
        if stop_flag is not None and stop_flag.is_set():
            break
        if rc == 0:
            ok += 1
        else:
            bad += 1
            log(f"FEHLGESCHLAGEN ({rc}): {url}")
            with failed_log.open("a", encoding="utf-8") as fh:
                fh.write(url + "\n")

    log(f"\nFertig. {ok} erfolgreich, {bad} fehlgeschlagen.")
    if bad:
        log(f"Liste der Fehlschlaege: {failed_log}")
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

PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
ITEM_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s")


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("Tkinter ist nicht installiert.")
        print("Linux:  sudo apt install python3-tk   (bzw. python3-tkinter)")
        return 1

    cfg = load_settings()

    root = tk.Tk()
    root.title("yt-dlp Downloader")
    root.geometry("780x680")
    root.minsize(660, 560)

    pad = {"padx": 8, "pady": 4}
    main = ttk.Frame(root, padding=10)
    main.pack(fill="both", expand=True)
    main.columnconfigure(0, weight=1)

    ttk.Label(main, text="URLs (eine pro Zeile):").grid(row=0, column=0, sticky="w")
    url_box = tk.Text(main, height=7, wrap="none")
    url_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
    main.rowconfigure(1, weight=1)

    opts = ttk.Frame(main)
    opts.grid(row=2, column=0, columnspan=2, sticky="ew")
    opts.columnconfigure(1, weight=1)

    ttk.Label(opts, text="Modus:").grid(row=0, column=0, sticky="w", **pad)
    mode_var = tk.StringVar(value=MODES.get(cfg.get("mode", ""), MODES["video"]))
    ttk.Combobox(opts, textvariable=mode_var, values=list(MODES.values()),
                 state="readonly").grid(row=0, column=1, columnspan=2, sticky="ew", **pad)

    ttk.Label(opts, text="Zielordner:").grid(row=1, column=0, sticky="w", **pad)
    out_var = tk.StringVar(value=cfg.get("out") or str(DEFAULT_OUT))
    ttk.Entry(opts, textvariable=out_var).grid(row=1, column=1, sticky="ew", **pad)

    def pick_dir():
        d = filedialog.askdirectory(initialdir=out_var.get() or str(Path.home()))
        if d:
            out_var.set(d)

    ttk.Button(opts, text="Waehlen ...", command=pick_dir).grid(row=1, column=2, **pad)

    ttk.Label(opts, text="Cookies aus Browser:").grid(row=2, column=0, sticky="w", **pad)
    cookie_var = tk.StringVar(value=cfg.get("cookies", ""))
    ttk.Combobox(opts, textvariable=cookie_var, values=BROWSERS,
                 state="readonly", width=14).grid(row=2, column=1, sticky="w", **pad)

    checks = ttk.Frame(main)
    checks.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 8))
    subs_var = tk.BooleanVar(value=cfg.get("subs", False))
    thumb_var = tk.BooleanVar(value=cfg.get("thumb", False))
    arch_var = tk.BooleanVar(value=cfg.get("archive", True))
    uploader_var = tk.BooleanVar(value=cfg.get("uploader", False))
    single_var = tk.BooleanVar(value=cfg.get("single", True))
    for text, var in (("Untertitel (de/en)", subs_var), ("Thumbnail einbetten", thumb_var),
                      ("Archiv (kein Doppel-Download)", arch_var),
                      ("Nach Kanal sortieren", uploader_var),
                      ("Nur einzelnes Video (keine Playlist)", single_var)):
        ttk.Checkbutton(checks, text=text, variable=var).pack(side="left", padx=6)

    btns = ttk.Frame(main)
    btns.grid(row=4, column=0, columnspan=2, sticky="ew")
    start_btn = ttk.Button(btns, text="Download starten")
    start_btn.pack(side="left")
    stop_btn = ttk.Button(btns, text="Abbrechen", state="disabled")
    stop_btn.pack(side="left", padx=6)
    ttk.Button(btns, text="Ordner oeffnen",
               command=lambda: open_folder(Path(out_var.get() or DEFAULT_OUT))).pack(side="left")
    update_btn = ttk.Button(btns, text="yt-dlp aktualisieren")
    update_btn.pack(side="right")

    status_var = tk.StringVar(value="")
    ttk.Label(main, textvariable=status_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
    bar = ttk.Progressbar(main, maximum=100)
    bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 0))

    log_box = tk.Text(main, height=14, wrap="none", state="disabled",
                      background="#111", foreground="#ddd", insertbackground="#ddd")
    log_box.grid(row=7, column=0, sticky="nsew", pady=(8, 0))
    main.rowconfigure(7, weight=2)
    sb = ttk.Scrollbar(main, command=log_box.yview)
    sb.grid(row=7, column=1, sticky="ns", pady=(8, 0))
    log_box.configure(yscrollcommand=sb.set)

    def gui_log(msg: str) -> None:
        def _append():
            m = PROGRESS_RE.search(str(msg))
            if m:
                bar["value"] = float(m.group(1))
            m = ITEM_RE.match(str(msg))
            if m:
                status_var.set(f"Download {m.group(1)} von {m.group(2)}")
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
            messagebox.showwarning("Keine URLs", "Bitte mindestens eine URL eintragen.")
            return
        save_settings({"mode": mode_key(), "out": out_var.get(), "cookies": cookie_var.get(),
                       "subs": subs_var.get(), "thumb": thumb_var.get(), "archive": arch_var.get(),
                       "uploader": uploader_var.get(), "single": single_var.get()})
        stop_flag.clear()
        set_busy(True)
        bar["value"] = 0
        status_var.set("")
        out_dir = Path(out_var.get() or DEFAULT_OUT)
        args = build_args(mode_key(), out_dir, subs=subs_var.get(), thumb=thumb_var.get(),
                          archive=arch_var.get(), cookies_browser=cookie_var.get(),
                          sort_by_uploader=uploader_var.get(), no_playlist=single_var.get())

        def work():
            try:
                ok, bad = download(urls, args, out_dir, gui_log, stop_flag)
                root.after(0, lambda: status_var.set(f"Fertig: {ok} erfolgreich, {bad} fehlgeschlagen"))
            finally:
                root.after(0, lambda: set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def on_update():
        set_busy(True)
        stop_btn.configure(state="disabled")

        def work():
            try:
                update_ytdlp(gui_log)
                if find_js_runtime() is None:
                    install_deno(gui_log)
            finally:
                root.after(0, lambda: set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    start_btn.configure(command=on_start)
    update_btn.configure(command=on_update)
    stop_btn.configure(command=lambda: (stop_flag.set(), gui_log("Abbruch angefordert ...")))

    gui_log(f"Bereit. Zielordner: {out_var.get()}")
    _, _src = find_ffmpeg()
    if _src == "none":
        gui_log("Hinweis: " + ffmpeg_hint())

    if find_ytdlp() is None or find_js_runtime() is None:      # Erststart: Tools im Hintergrund holen
        set_busy(True)
        stop_btn.configure(state="disabled")

        def first_run():
            try:
                ensure_tools(gui_log)
            finally:
                root.after(0, lambda: set_busy(False))

        threading.Thread(target=first_run, daemon=True).start()

    root.mainloop()
    return 0


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Plattformuebergreifender yt-dlp-Wrapper (CLI + GUI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Beispiele:\n"
               "  python3 ytdl.py URL1 URL2\n"
               "  python3 ytdl.py -a urls.txt -m mp3 -o ~/Music\n"
               "  python3 ytdl.py --gui\n",
    )
    p.add_argument("urls", nargs="*", help="Eine oder mehrere URLs")
    p.add_argument("-a", "--batch-file", help="Textdatei mit einer URL pro Zeile (# = Kommentar)")
    p.add_argument("-m", "--mode", choices=list(MODES), default="video",
                   help="Download-Modus (Standard: video)")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="Zielordner")
    p.add_argument("--subs", action="store_true", help="Untertitel de/en mitziehen")
    p.add_argument("--thumb", action="store_true", help="Thumbnail einbetten")
    p.add_argument("--archive", action="store_true", help="archive.txt fuehren (kein Doppel-Download)")
    p.add_argument("--by-uploader", action="store_true", help="In Unterordner je Kanal sortieren")
    p.add_argument("--cookies-from-browser", default="", metavar="BROWSER",
                   help="z.B. chrome, firefox, safari - fuer private/altersbeschraenkte Videos")
    p.add_argument("--gui", action="store_true", help="Grafische Oberflaeche starten")
    p.add_argument("--update", action="store_true", help="yt-dlp installieren/aktualisieren und beenden")

    args, unknown = p.parse_known_args(argv)

    if args.update:
        return 0 if update_ytdlp() else 1
    if args.gui or (not args.urls and not args.batch_file):
        return run_gui()

    urls = list(args.urls)
    if args.batch_file:
        urls += read_url_file(args.batch_file)
    if not urls:
        p.error("Keine URLs angegeben.")

    out_dir = Path(args.out).expanduser()
    ytdlp_args = build_args(args.mode, out_dir, subs=args.subs, thumb=args.thumb,
                            archive=args.archive, cookies_browser=args.cookies_from_browser,
                            sort_by_uploader=args.by_uploader, extra=unknown)
    ok, bad = download(urls, ytdlp_args, out_dir)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(130)
