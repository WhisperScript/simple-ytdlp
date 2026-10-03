#!/usr/bin/env python3
"""Builds the pictures for the Chrome Web Store listing (extension/store/*.png).

The real popup and settings page of the extension are photographed in Chromium (Playwright) while the real app
window - filled with made-up downloads, like the README's pictures - runs on a virtual display:

    pip install playwright pillow sv-ttk && playwright install chromium
    xvfb-run -a -s "-screen 0 1800x1100x24" python extension/store/make_assets.py

Writes screenshot-1.png and screenshot-2.png (1280x800) and promo-small.png (440x280).
"""
import json
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "docs")]
import gui_support as gs  # noqa: E402
import make_screenshots as ms  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

BG_TOP, BG_BOTTOM = (28, 38, 66), (12, 16, 30)
WHITE, SOFT = (255, 255, 255), (186, 196, 222)


def font(size, bold=True):
    names = ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if bold else \
            ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def background(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    return img


def card(img, shot, xy, width):
    """Paste a screenshot (scaled to width) with rounded corners and a soft shadow."""
    shot = shot.convert("RGB")
    shot = shot.resize((width, round(shot.height * width / shot.width)), Image.LANCZOS)
    x, y = xy
    shadow = Image.new("RGBA", (shot.width + 80, shot.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((40, 48, 40 + shot.width, 48 + shot.height), 14, fill=(0, 0, 0, 120))
    from PIL import ImageFilter
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    img.paste(shadow, (x - 40, y - 40), shadow)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), 12, fill=255)
    img.paste(shot, (x, y), mask)
    return shot.size


def headline(img, text, sub):
    d = ImageDraw.Draw(img)
    d.text((64, 52), text, font=font(46), fill=WHITE)
    d.text((64, 118), sub, font=font(24, False), fill=SOFT)


class Driver:
    """The Chromium with the extension (tests/extension_driver.py), controlled while the window keeps running."""

    def __init__(self, root, shots_dir):
        self.root = root
        self.answers = queue.Queue()
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "extension_driver.py"),
                                      str(ROOT / "extension"), str(shots_dir)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        self.hello = self.answer(60)

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.answers.put(json.loads(line))
            except ValueError:
                pass

    def answer(self, timeout=90):
        got = []

        def ready():
            try:
                got.append(self.answers.get_nowait())
            except queue.Empty:
                pass
            return bool(got)

        if not gs.wait_until(self.root, ready, timeout):
            raise SystemExit("Chromium did not answer")
        if got[0].get("error"):
            raise SystemExit(got[0]["error"])
        return got[0]

    def __call__(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return self.answer()

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(15)
        except Exception:
            self.proc.kill()


def main():
    mod, _ = gs.load_app()
    patches = [mock.patch.object(mod, "find_ytdlp", lambda: ["yt-dlp"]),
               mock.patch.object(mod, "ensure_tools", lambda log=print: True),
               mock.patch.object(mod, "find_js_runtime", lambda: []),
               mock.patch.object(mod, "find_ffmpeg", lambda: ("ffmpeg", "system"))]
    for p in patches:
        p.start()
    work = Path(tempfile.mkdtemp(prefix="store-"))
    mod.__version__ = "2.6"                                     # what "Connected to simple-ytdlp ..." shows
    shots = ms.Shots("light")
    driver = None
    try:
        app = shots.app
        driver = Driver(shots.root, work)
        app.trusted.add(driver.hello["base"])
        # the real popup talking to the real window; the list is staged afterwards with the README's made-up videos
        out = driver("popup_stub https://video.example/watch?v=alps")
        assert out["state"] == "state ok", out
        driver("shot popup-added body")
        driver("options")
        driver("shot options main")
        driver.close()                                          # the browser window would cover the app's
        driver = None
        shots.pump(300)

        shots.clear()
        shots.card("yoga", "done", pct=100, size="210.4 MiB", path=str(shots.folder / "yoga.mp4"))
        shots.card("bread", "downloading", pct=41, size="210.10MiB", speed="6.40MiB/s", eta="00:38")
        alps = shots.card("alps", "queued")
        shots.show()
        app.selected = {alps.id}
        app.view.refresh_rows()
        shots.pump(300)
        window = shots.grab()

        app.open_settings()
        win = app.settings_win
        win.geometry("+60+20")
        win.lift()
        shots.pump(500)
        dialog = shots.grab(win)
        box = next(w for w in _walk(win) if w.winfo_class() == "TLabelframe" and _text(w) == "Login and network")
        top = box.winfo_rooty() - win.winfo_rooty() - 14
        section = dialog.crop((0, top, dialog.width, top + box.winfo_height() + 16))
        win.close()

        popup = Image.open(work / "popup-added.png")
        options = Image.open(work / "options.png")

        one = background(1280, 800)
        headline(one, "From the video to your list in one click",
                 "Click the icon on any video or playlist - it lands in simple-ytdlp.")
        pw, ph = card(one, popup, (64, 250), 420)
        card(one, window, (560, 190), 656)
        ImageDraw.Draw(one).text((64, 250 + ph + 44), "Alt+Shift+D works too.", font=font(22, False), fill=SOFT)
        one.save(HERE / "screenshot-1.png", optimize=True)

        two = background(1280, 800)
        headline(two, "Local only. You decide.",
                 "Talks to the app on your own computer - nothing else. The app asks once to allow it.")
        ow, oh = card(two, options, (64, 290), 420)
        sw, sh = card(two, section, (540, 290), 676)
        d = ImageDraw.Draw(two)
        for i, line in enumerate(("Listens on 127.0.0.1 only - never on the network",
                                  "Web pages cannot use it - only an extension you allow",
                                  "No account, no tracking, no data sent anywhere")):
            d.text((540, 290 + sh + 60 + i * 44), "\u2713  " + line, font=font(21, False), fill=WHITE)
        two.save(HERE / "screenshot-2.png", optimize=True)

        tile = background(440, 280)
        icon = mod.make_icon(128).resize((96, 96), Image.LANCZOS)
        tile.paste(icon, (40, 40), icon if icon.mode == "RGBA" else None)
        d = ImageDraw.Draw(tile)
        d.text((40, 160), "Send to simple-ytdlp", font=font(30), fill=WHITE)
        d.text((40, 206), "One click. Right from your browser.", font=font(17, False), fill=SOFT)
        tile.save(HERE / "promo-small.png", optimize=True)
        for name in ("screenshot-1", "screenshot-2", "promo-small"):
            print(HERE / f"{name}.png")
    finally:
        if driver:
            driver.close()
        shots.close()
        for p in reversed(patches):
            p.stop()


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _text(widget):
    try:
        return widget.cget("text")
    except Exception:
        return ""


if __name__ == "__main__":
    main()
