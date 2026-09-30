#!/usr/bin/env python3
"""Regenerates the screenshots of the README (docs/img/*.png), in the dark and the light theme.

The real window is filled with made-up downloads (no network and no yt-dlp needed), photographed from a
virtual display and - for the "how it works" pictures - annotated with numbered call-outs.

Run it on Linux with Tk, sv-ttk and Pillow installed:

    xvfb-run -a -s "-screen 0 1800x1100x24" python docs/make_screenshots.py

The pictures show the Linux fonts; on Windows and macOS the text looks a little different.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
sys.path.insert(0, str(ROOT / "tests"))
import gui_support as gs  # noqa: E402
from PIL import Image, ImageDraw, ImageFont, ImageGrab  # noqa: E402

CALLOUT = (217, 72, 15)                                   # call-out colour: visible on light and dark
PAGE = {"dark": (13, 17, 23), "light": (255, 255, 255)}   # GitHub's page background around the pictures
EDGE = {"dark": (62, 68, 76), "light": (208, 215, 222)}   # thin line around a window
WINDOW = (920, 760)


# ------------------------------------------------------------------------------------ made-up videos

def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def sky(size, top, bottom):
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    for y in range(size[1]):
        d.line([(0, y), (size[0], y)], fill=mix(top, bottom, y / (size[1] - 1)))
    return img


def scene_alps(w=640, h=360):
    img = sky((w, h), (255, 160, 110), (255, 226, 190))
    d = ImageDraw.Draw(img)
    d.ellipse((w * .66, h * .12, w * .66 + 78, h * .12 + 78), fill=(255, 246, 214))
    d.polygon([(0, h * .75), (w * .16, h * .42), (w * .32, h * .64), (w * .50, h * .28), (w * .72, h * .68),
               (w * .86, h * .44), (w, h * .70), (w, h), (0, h)], fill=(128, 112, 170))
    d.polygon([(w * .50, h * .28), (w * .45, h * .42), (w * .49, h * .39), (w * .51, h * .44), (w * .55, h * .39),
               (w * .56, h * .42)], fill=(255, 255, 255))
    d.polygon([(0, h), (0, h * .80), (w * .22, h * .58), (w * .42, h * .82), (w * .62, h * .60), (w * .85, h * .86),
               (w, h * .72), (w, h)], fill=(62, 56, 104))
    return img


def scene_bread(w=640, h=360):
    img = sky((w, h), (246, 226, 190), (214, 168, 120))
    d = ImageDraw.Draw(img)
    d.ellipse((w * .22, h * .22, w * .78, h * .92), fill=(178, 108, 52))
    d.ellipse((w * .25, h * .24, w * .75, h * .84), fill=(214, 150, 84))
    for i in range(3):
        x = w * (.36 + .12 * i)
        d.line([(x, h * .34), (x + w * .06, h * .64)], fill=(245, 214, 160), width=9)
    return img


def scene_piano(w=640, h=360):
    img = sky((w, h), (22, 28, 64), (70, 52, 110))
    d = ImageDraw.Draw(img)
    top = h * .58
    d.rectangle((0, top, w, h), fill=(245, 245, 248))
    key = w / 14
    for i in range(1, 14):
        d.line([(key * i, top), (key * i, h)], fill=(150, 150, 160), width=2)
    for i in (1, 2, 4, 5, 6, 8, 9, 11, 12, 13):
        d.rectangle((key * i - key * .3, top, key * i + key * .3, top + (h - top) * .62), fill=(24, 24, 32))
    d.ellipse((w * .62, h * .10, w * .62 + 64, h * .10 + 64), fill=(255, 240, 200))
    return img


def scene_city(w=640, h=360):
    img = sky((w, h), (16, 20, 52), (86, 46, 110))
    d = ImageDraw.Draw(img)
    x, seed = 0, 7
    while x < w:
        seed = (seed * 37 + 11) % 97
        bw, bh = 38 + seed % 40, h * (.30 + (seed % 50) / 100)
        d.rectangle((x, h - bh, x + bw, h), fill=(24, 22, 46))
        for wy in range(int(h - bh + 12), int(h - 10), 22):
            for wx in range(int(x + 8), int(x + bw - 8), 16):
                if (wx * 7 + wy * 3 + seed) % 5:
                    d.rectangle((wx, wy, wx + 7, wy + 10), fill=(255, 214, 120))
        x += bw + 4
    return img


def scene_yoga(w=640, h=360):
    img = sky((w, h), (186, 230, 214), (236, 246, 230))
    d = ImageDraw.Draw(img)
    d.ellipse((w * .10, h * .58, w * .90, h * 1.10), fill=(140, 196, 170))
    d.ellipse((w * .44, h * .22, w * .56, h * .42), fill=(96, 70, 110))
    d.polygon([(w * .40, h * .80), (w * .47, h * .44), (w * .53, h * .44), (w * .60, h * .80)], fill=(96, 70, 110))
    d.ellipse((w * .28, h * .74, w * .72, h * .86), fill=(96, 70, 110))
    return img


CARDS = {
    "yoga": ("Beginner yoga for a stiff back - 15 minutes", "Morning Flow", 903, scene_yoga),
    "alps": ("Sunrise over the Alps - 4K timelapse", "Nature Channel", 272, scene_alps),
    "bread": ("How to bake sourdough bread at home", "Kitchen Science", 728, scene_bread),
    "piano": ("Relaxing piano music for studying and sleep", "Calm Sounds", 3765, scene_piano),
    "city": ("Night walk through the city - 4K", "Street Views", 2294, scene_city),
}


# ------------------------------------------------------------------------------------------- drawing

def font(size):
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Notes:
    """Numbered call-outs on a picture: a ring around a part of the window, a label, a line between them."""

    S = 3                                                 # drawn at 3x and scaled down: smooth edges

    def __init__(self, img):
        self.img = img.convert("RGBA")
        self.layer = Image.new("RGBA", (img.width * self.S, img.height * self.S), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.layer)
        self.font = font(17 * self.S)

    def _s(self, *values):
        return [v * self.S for v in values]

    def callout(self, rect, text, number, at, ring=True, pad=5):
        """rect: what is meant (x0, y0, x1, y1); at: where the label goes (x, y of its top left corner)."""
        S, d = self.S, self.d
        x0, y0, x1, y1 = rect
        if ring:
            d.rounded_rectangle(self._s(x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=10 * S, outline=CALLOUT,
                                width=3 * S)
        box = d.textbbox((0, 0), text, font=self.font)
        tw = (box[2] - box[0]) / S
        badge = 26
        pw, ph = badge + 8 + tw + 14, 34
        px = min(max(at[0], 6), self.img.width - pw - 6)
        py = min(max(at[1], 6), self.img.height - ph - 6)
        # the line first, so the label covers its end
        cx, cy = x0 + (x1 - x0) / 2, y0 + (y1 - y0) / 2
        lx, ly = min(max(cx, px), px + pw), min(max(cy, py), py + ph)
        ex, ey = min(max(lx, x0 - pad), x1 + pad), min(max(ly, y0 - pad), y1 + pad)
        d.line(self._s(lx, ly, ex, ey), fill=CALLOUT, width=3 * S)
        d.ellipse(self._s(ex - 5, ey - 5, ex + 5, ey + 5), fill=CALLOUT)
        d.rounded_rectangle(self._s(px, py, px + pw, py + ph), radius=17 * S, fill=CALLOUT)
        d.ellipse(self._s(px + 4, py + 4, px + 4 + badge, py + 4 + badge), fill=(255, 255, 255))
        nb = d.textbbox((0, 0), str(number), font=self.font)
        d.text((self._s(px + 4 + badge / 2)[0] - (nb[0] + nb[2]) / 2, self._s(py + 17)[0] - (nb[1] + nb[3]) / 2),
               str(number), font=self.font, fill=CALLOUT)
        d.text((self._s(px + badge + 12)[0] - box[0], self._s(py + 17)[0] - (box[1] + box[3]) / 2),
               text, font=self.font, fill=(255, 255, 255))

    def result(self):
        layer = self.layer.resize(self.img.size, Image.LANCZOS)
        return Image.alpha_composite(self.img, layer).convert("RGB")


def canvas(img, theme, top=0, bottom=0, left=0, right=0):
    """The picture on a larger background of the page's colour (room for call-out labels); also its offset."""
    out = Image.new("RGB", (img.width + left + right, img.height + top + bottom), PAGE[theme])
    out.paste(img, (left, top))
    return out, (left, top)


def framed(img, theme):
    """A window picture with a thin line around it."""
    out = Image.new("RGB", (img.width + 2, img.height + 2), EDGE[theme])
    out.paste(img, (1, 1))
    return out


def save(img, name, theme):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}-{theme}.png"
    img.save(path, optimize=True)
    print(f"{path.relative_to(ROOT)}  {img.width}x{img.height}  {path.stat().st_size // 1024} KiB", flush=True)


# ------------------------------------------------------------------------------------------- the window

class Shots:
    def __init__(self, theme):
        self.theme = theme
        self.mod, self.root, self.app = gs.start_app({"theme": theme, "mode": "video", "parallel": 2, "autostart": True})
        self.root.geometry(f"{WINDOW[0]}x{WINDOW[1]}+40+40")
        self.folder = Path.home() / "Downloads" / "yt-dlp"
        self.app.out_var.set(str(self.folder))
        self.app._update_folder_label()
        self.thumbs = {}
        tmp = Path(tempfile.mkdtemp(prefix="thumbs-"))
        for key, (_title, _channel, _secs, scene) in CARDS.items():
            scene().save(tmp / f"{key}.jpg")
            self.thumbs[key] = self.mod.fetch_thumbnail((tmp / f"{key}.jpg").as_uri())
        self.part = tmp / "sourdough.mp4"
        Path(str(self.part) + ".part").write_bytes(b"")
        with open(str(self.part) + ".part", "r+b") as fh:          # a sparse 12.4 MiB "partial download"
            fh.truncate(13_000_000)

    # -- state

    def clear(self):
        app = self.app
        app._drop(list(app.items))
        app.selected.clear()
        app.running, app.batch_ids = False, set()
        app.clear_selection()
        app._items_changed()

    def card(self, key, status, **fields):
        title, channel, seconds, _ = CARDS[key]
        item = self.mod.Item(f"https://video.example/watch?v={key}")
        item.title, item.uploader, item.duration = title, channel, seconds
        item.thumb, item.thumb_state, item.status = self.thumbs[key], "done", status
        for name, value in fields.items():
            setattr(item, name, value)
        self.app.items.append(item)
        return item

    def show(self, running=True):
        app = self.app
        app.running = running
        app.batch_ids = {it.id for it in app.items}
        app._items_changed()
        for it in app.items:
            app.view.touch(it)
        app.update_state()
        app._ensure_retry_tick()
        app.view.canvas.yview_moveto(0.0)
        self.pump(400)

    def pump(self, ms=300):
        gs.pump(self.root, ms)

    # -- pictures

    def grab(self, widget=None):
        widget = widget or self.root
        widget.update_idletasks()
        widget.update()
        x, y, w, h = widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()
        return ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=os.environ.get("DISPLAY")).convert("RGB")

    def rect(self, widget, origin=None):
        """A widget's box in the coordinates of the window picture."""
        ox, oy = origin or (self.root.winfo_rootx(), self.root.winfo_rooty())
        x, y = widget.winfo_rootx() - ox, widget.winfo_rooty() - oy
        return x, y, x + widget.winfo_width(), y + widget.winfo_height()

    def queue(self):
        """The mixed queue of the hero picture."""
        self.clear()
        self.card("yoga", "done", pct=100, size="210.4 MiB", path=str(self.folder / "yoga.mp4"))
        self.card("alps", "downloading", pct=64, size="812.40MiB", speed="11.20MiB/s", eta="00:22")
        self.card("bread", "downloading", pct=23, size="210.10MiB", speed="6.40MiB/s", eta="00:38")
        self.card("piano", "queued")
        self.card("city", "queued")
        self.show()

    def pause_states(self):
        self.clear()
        self.card("alps", "downloading", pct=64, size="812.40MiB", speed="11.20MiB/s", eta="00:22", resumed=True)
        self.card("bread", "paused", pct=32, dests=[str(self.part)])
        self.card("piano", "queued", retries=2, retry_at=time.time() + 40)
        self.show()

    # -- the set

    def hero(self):
        self.queue()
        save(framed(self.grab(), self.theme), "hero", self.theme)

    def step_paste(self):
        self.clear()
        self.show(running=False)
        out, (dx, dy) = canvas(framed(self.grab(), self.theme), self.theme, top=64)
        n = Notes(out)

        def at(widget):
            r = self.rect(widget)
            return r[0] + dx + 1, r[1] + dy + 1, r[2] + dx + 1, r[3] + dy + 1
        n.callout(at(self.app.url_entry), "Paste the link here - or press Ctrl+V (Cmd+V on a Mac) anywhere", 1,
                  (dx + 30, 12))
        button = at(self.app.empty_actions.winfo_children()[0])
        n.callout(button, "Or click this button", 2, (button[0], button[3] + 34))
        save(n.result(), "step1-paste", self.theme)

    def step_choose(self):
        app = self.app
        self.clear()
        self.show(running=False)
        kinds = app.quality_combo.master.winfo_children()[0]             # the Video | Audio switch
        also = app.also_lbl.master                                        # "Also save audio" + its menu
        top = self.rect(app.url_entry)[1] - 10
        bottom = self.rect(app.quality_combo)[3] + 12
        crops = {}
        for kind in ("video", "audio"):
            app.kind_var.set(kind)
            app._on_kind()
            self.pump(250)
            shot = self.grab()
            crops[kind] = framed(shot.crop((0, top, shot.width, bottom)), self.theme)
        boxes = {name: self.rect(w) for name, w in (("kinds", kinds), ("quality", app.quality_combo), ("also", also))}
        app.kind_var.set("video")
        app._on_kind()
        gap, margin = 70, 64
        h = crops["video"].height
        out = Image.new("RGB", (crops["video"].width, h * 2 + gap + margin), PAGE[self.theme])
        out.paste(crops["video"], (0, 0))
        out.paste(crops["audio"], (0, h + gap))
        n = Notes(out)

        def at(name, row):
            r = boxes[name]
            return r[0] + 1, r[1] - top + 1 + row * (h + gap), r[2] + 1, r[3] - top + 1 + row * (h + gap)
        y = h + 18
        n.callout(at("kinds", 0), "Video or audio?", 1, (16, y))
        n.callout(at("quality", 0), "Quality", 2, (232, y))
        n.callout(at("also", 0), "Video and a separate audio file", 3, (398, y))
        q = at("quality", 1)
        n.callout(q, "Audio: MP3, M4A or Opus", 4, (q[0], q[3] + 18))
        save(n.result(), "step2-choose", self.theme)

    def step_files(self):
        app = self.app
        self.clear()
        self.card("yoga", "done", pct=100, size="210.4 MiB", path=str(self.folder / "yoga.mp4"))
        self.card("alps", "downloading", pct=64, size="812.40MiB", speed="11.20MiB/s", eta="00:22")
        self.card("piano", "queued")
        self.root.geometry(f"{WINDOW[0]}x560+40+40")
        self.show()
        out, (dx, dy) = canvas(framed(self.grab(), self.theme), self.theme, bottom=70)

        def at(widget):
            r = self.rect(widget)
            return r[0] + dx + 1, r[1] + dy + 1, r[2] + dx + 1, r[3] + dy + 1
        n = Notes(out)
        row = app.view.row_for(app.items[0])
        status = at(row.status)
        n.callout(at(row.primary), "Opens the folder", 1, (status[0] + 250, status[1] - 6))
        chip = at(app.folder_btn)
        n.callout(chip, "Your files are saved here - click to pick another folder", 2, (chip[0] + 10, chip[3] + 30))
        save(n.result(), "step3-files", self.theme)
        self.root.geometry(f"{WINDOW[0]}x{WINDOW[1]}+40+40")
        self.pump(200)

    def pause_resume(self):
        app = self.app
        self.pause_states()
        shot = self.grab()
        top = self.rect(app.tabbar)[1] - 2
        rows = [app.view.row_for(it) for it in app.items]
        bottom = self.rect(rows[-1].outer)[3] + 8
        out, (dx, dy) = canvas(framed(shot.crop((0, top, shot.width, bottom)), self.theme), self.theme,
                               right=330, bottom=64)

        def at(widget):
            r = self.rect(widget)
            return r[0] + dx + 1, r[1] - top + dy + 1, r[2] + dx + 1, r[3] - top + dy + 1
        n = Notes(out)
        pause, resume = at(rows[0].primary), at(rows[1].primary)
        n.callout(pause, "Pause any time", 1, (pause[2] + 70, pause[1] - 46))
        n.callout(resume, "Continue where it stopped", 2, (resume[2] + 40, resume[3] + 12))
        status = at(rows[2].status)
        text = rows[2].status.cget("text")
        n.callout((status[0], status[1], status[0] + app.font_status.measure(text) + 8, status[3]),
                  "Internet dropped? It retries by itself", 3, (status[0], out.height - 50), pad=2)
        save(n.result(), "pause-resume", self.theme)

    def playlist(self):
        entries = [{"title": t, "url": f"https://video.example/p{i}", "duration": s, "uploader": "Alpine Trails"}
                   for i, (t, s) in enumerate([("Day 1 - Up to the first hut", 1324), ("Day 2 - Across the glacier", 1511),
                                               ("Packing list and gear", 602), ("Day 3 - Storm warning", 1187),
                                               ("Day 4 - The summit", 2033), ("Bloopers and outtakes", 244),
                                               ("Day 5 - Down to the valley", 1466), ("Thank you for 100k", 97)])]
        d = self.mod.PlaylistDialog(self.root, "Five days in the Alps - the whole trip", entries,
                                    self.mod.COLORS[self.theme])
        d.geometry("+60+60")
        for i in (2, 5, 7):
            d.checked[i] = False
            d._render(i)
        d._update_count()
        d.lift()
        self.pump(400)
        shot = self.grab(d)
        out, (dx, dy) = canvas(framed(shot, self.theme), self.theme, right=330)

        def at(r):
            return r[0] + dx + 1, r[1] + dy + 1, r[2] + dx + 1, r[3] + dy + 1
        n = Notes(out)
        ox, oy = d.winfo_rootx(), d.winfo_rooty()
        tree = at(self.rect(d.tree, (ox, oy)))
        n.callout(tree, "Click a video to tick or untick it", 1, (tree[2] + 30, tree[1] + 150))
        add = at(self.rect(d.add_btn, (ox, oy)))
        n.callout(add, "Add the ticked videos", 2, (add[2] + 30, add[1] - 70))
        save(n.result(), "playlist", self.theme)
        d.destroy()

    def settings(self):
        self.app.open_settings()
        win = self.app.settings_win
        win.geometry("+60+40")
        win.lift()
        self.pump(500)
        shot = self.grab(win)
        shot = shot.crop((0, 0, shot.width, min(shot.height, 560)))
        save(framed(shot, self.theme), "settings", self.theme)
        win.close()

    def close(self):
        gs.close_app(self.root)


def main():
    themes = sys.argv[1:] or ["dark", "light"]
    mod, _ = gs.load_app()
    patches = [
        mock.patch.object(mod, "find_ytdlp", lambda: ["yt-dlp"]),                  # nothing is started here
        mock.patch.object(mod, "ensure_tools", lambda log=print: True),
        mock.patch.object(mod, "find_js_runtime", lambda: []),
        mock.patch.object(mod, "find_ffmpeg", lambda: ("ffmpeg", "system")),
    ]
    for p in patches:
        p.start()
    try:
        for theme in themes:
            shots = Shots(theme)
            try:
                shots.hero()
                shots.step_paste()
                shots.step_choose()
                shots.step_files()
                shots.pause_resume()
                shots.playlist()
                shots.settings()
            finally:
                shots.close()
    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    main()
