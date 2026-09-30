"""Tests of the real window, driven with a fake yt-dlp (tests/fake_ytdlp.py).

They need Tk and a display - CI runs them under Xvfb, everywhere else the module is skipped.
All tests share one window; every test starts from an empty queue (see GuiCase.setUp).
"""
import faulthandler
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gui_support as gs  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_ytdlp.py"
FULL_SIZE = 4_000_000                       # what the fake "downloads"

mod = root = app = None                     # the app module, the Tk root and the App (see setUpModule)
WORK = None                                 # folder of the fake's call log, counters and thumbnails
DEFAULT_AUTOSTART = None
_patches: list = []


def setUpModule():
    global mod, root, app, WORK, DEFAULT_AUTOSTART
    gs.require_gui()
    mod, _ = gs.load_app()
    WORK = gs.scratch_dir("fake-")
    from PIL import Image
    Image.new("RGB", (640, 360), (200, 70, 70)).save(WORK / "t.jpg")
    Image.new("RGB", (360, 640), (70, 70, 200)).save(WORK / "p.jpg")
    os.environ["FAKE_YTDLP_DIR"] = str(WORK)
    _patches.extend([                        # the fake replaces yt-dlp: no network, nothing gets installed
        mock.patch.object(mod, "find_ytdlp", lambda: [sys.executable, str(FAKE)]),
        mock.patch.object(mod, "ensure_tools", lambda log=print: True),
        mock.patch.object(mod, "find_js_runtime", lambda: []),
        mock.patch.object(mod, "find_ffmpeg", lambda: ("ffmpeg", "system")),
    ])
    for p in _patches:
        p.start()
    mod, root, app = gs.start_app({"mode": "video", "parallel": 2})
    DEFAULT_AUTOSTART = app.auto_var.get()
    faulthandler.dump_traceback_later(420, exit=True)        # a stuck window must not hang the CI job


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()
    for it in list(app.items):
        it.stop.set()
    gs.pump(root, 300)
    gs.close_app(root)
    for p in _patches:
        p.stop()
    os.environ.pop("FAKE_YTDLP_DIR", None)


def U(name):
    return f"https://x.test/watch?v={name}"


def pump(ms=50):
    gs.pump(root, ms)


def wait(condition, timeout=20.0):
    return gs.wait_until(root, condition, timeout)


def mk(n, status="queued", title=None):
    it = mod.Item(U(f"t{n}"))
    it.title, it.status, it.duration = title or f"Video number {n}", status, 60 + n
    return it


def add(url):
    """Add a link and wait for its preview: returns the queued item."""
    before = len(app.items)
    app.add_urls([url])
    assert wait(lambda: len(app.items) > before and app.items[-1].status == "queued", 15), "no preview arrived"
    return app.items[-1]


def calls():
    """The argument lists the fake was started with."""
    path = WORK / "args.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def labels(menu):
    return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) != "separator"]


class Click:                                 # a minimal mouse event
    def __init__(self, state=0):
        self.state = state


class GuiCase(unittest.TestCase):
    def setUp(self):
        for it in list(app.items):
            it.stop.set()
        app._drop(list(app.items))
        app.selected.clear()
        app.anchor = app.cursor = None
        app.running = False
        app.batch_ids = set()
        for key in list(app.notices):
            app.clear_notice(key)
        app.auto_var.set(False)
        app.kind_var.set("video")
        app._on_kind()
        app.hist_filter.set("")
        app.log_filter.set("")
        app.clear_log()
        app.select_tab(0)
        self.out = gs.scratch_dir("out-")
        app.set_out_dir(str(self.out))
        (WORK / "args.log").unlink(missing_ok=True)
        app._items_changed()
        pump(30)

    def log_text(self):
        return app.log_box.get("1.0", "end")


# ------------------------------------------------------------------------------------------ the list

class VirtualList(GuiCase):
    def test_thousand_items_need_only_a_few_widgets(self):
        app.items.extend(mk(i) for i in range(1000))
        app._items_changed()
        pump(100)
        v = app.view
        self.assertLessEqual(len(v.bound) + len(v.free), 20)
        self.assertEqual(v._region[3], 1000 * mod.ROW_H - mod.CARD_GAP)
        ys = sorted(r.y0 for r in v.bound.values())
        self.assertTrue(all(y % mod.ROW_H == 0 for y in ys))
        self.assertEqual(ys, sorted(set(ys)))

    def test_scrolling_rebinds_rows(self):
        app.items.extend(mk(i) for i in range(1000))
        app._items_changed()
        v = app.view
        v.canvas.yview_moveto(1.0)
        pump(100)
        self.assertIsNotNone(v.row_for(app.items[-1]))
        self.assertIsNone(v.row_for(app.items[0]))
        self.assertTrue(v.row_for(app.items[-1]).title.cget("text").startswith("Video number 999"))
        v.scroll_to(app.items[500])
        pump(100)
        self.assertIsNotNone(v.row_for(app.items[500]))

    def test_five_thousand_items_are_fine_too(self):
        app.items.extend(mk(i) for i in range(5000))
        t0 = time.monotonic()
        app._items_changed()
        pump(100)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertLessEqual(len(app.view.bound) + len(app.view.free), 20)

    def test_many_loading_cards_do_not_freeze_the_window(self):
        app.items.extend(mod.Item(U(f"f{i}")) for i in range(200))       # 200 cards with a busy indicator
        app._items_changed()
        pump(100)
        rounds, t0 = 0, time.monotonic()
        while time.monotonic() - t0 < 1.0:
            root.update()
            rounds += 1
        self.assertGreater(rounds, 300, f"{rounds} event-loop rounds in one second")


class CardLayout(GuiCase):
    def test_long_titles_end_with_an_ellipsis_that_fits(self):
        for title in ("An extremely long video title that keeps going and going " * 4,
                      "日本語のとても長いタイトルのテストです。これは二行に収まるでしょうか？ 🎵 音楽 ライブ映像 完全版" * 3):
            with self.subTest(title=title[:12]):
                app._drop(list(app.items))
                app.items.append(mk(1, title=title))
                app._items_changed()
                pump(150)
                label = app.view.row_for(app.items[0]).title
                text, width = label.cget("text"), label.winfo_width()
                self.assertTrue(text.endswith("…"), text[-12:])
                self.assertLessEqual(app.font_title.measure(text), width)

    def test_titles_follow_the_window_width(self):
        app.items.append(mk(1, title="An extremely long video title that keeps going and going " * 4))
        app._items_changed()
        self.addCleanup(lambda: root.geometry("920x760"))
        root.geometry("780x560")
        pump(300)
        row = app.view.row_for(app.items[0])
        self.assertLessEqual(app.font_title.measure(row.title.cget("text")), row.title.winfo_width())
        narrow = len(row.title.cget("text"))
        root.geometry("1200x760")
        pump(300)
        self.assertGreater(len(row.title.cget("text")), narrow)

    def test_the_queue_summary_shrinks_instead_of_being_cut(self):
        states = ("downloading", "paused", "queued", "done", "failed")
        app.items.extend(mk(i, states[i % 5]) for i in range(10))
        for it in app.items:
            it.speed = "11.20MiB/s" if it.status == "downloading" else ""
        self.addCleanup(lambda: root.geometry("920x760"))
        shown = []
        for size in ("1400x760", "1000x760", "780x560"):
            root.geometry(size)
            app._items_changed()
            pump(200)
            summary = app.tabbar.summary
            with self.subTest(size=size):
                self.assertGreaterEqual(summary.winfo_width(), summary.winfo_reqwidth(), summary.cget("text"))
                self.assertIn("failed", summary.cget("text"))
            shown.append(summary.cget("text"))
        self.assertIn("MiB/s", shown[0], "there is room for the speed in a wide window")
        self.assertEqual([len(t) for t in shown], sorted((len(t) for t in shown), reverse=True))

    def test_fonts_are_sized_in_pixels_and_match_the_theme(self):
        body = mod.tkfont.nametofont("SunValleyBodyFont")
        px = -int(body.cget("size"))
        for font in (app.font_title, app.font_small, app.font_status, app.font_big):
            self.assertLess(int(font.cget("size")), 0)
        self.assertEqual(-int(app.font_title.cget("size")), px + 1)
        self.assertEqual(-int(app.font_status.cget("size")), px)

    def test_a_running_card_fits_its_fixed_height(self):
        it = mk(1, "downloading")
        it.pct, it.size, it.speed, it.eta, it.uploader = 40, "42.10MiB", "3.20MiB/s", "00:07", "Channel"
        it.overrides = {"mode": "mp3", "section": "*0:00:10-0:00:30"}
        app.items.append(it)
        app._items_changed()
        pump(100)
        row = app.view.row_for(it)
        card = row.geo["card"]
        for part in ("title", "meta", "status", "primary", "more", "thumb", "strip"):
            box = row.geo[part]
            self.assertTrue(card[1] <= box[1] and box[3] <= card[3], f"{part} {box} leaves the card {card}")
        self.assertLess(row.geo["status"][2], row.geo["primary"][0], "the text runs into the buttons")
        self.assertTrue(row.badge.winfo_ismapped())
        self.assertEqual(row.badge.cget("text"), "1:01")
        self.assertEqual(row.strip.cget("background"), mod.COLORS["dark"]["accent"])
        self.assertEqual(row.primary.cget("text"), "Pause")

    def test_failed_card_explains_the_error_and_offers_retry(self):
        it = mk(1, "failed")
        it.error = "ERROR: [youtube] abc: Sign in to confirm your age"
        app.items.append(it)
        app._items_changed()
        pump(100)
        row = app.view.row_for(it)
        self.assertIn("Age-restricted", row.status.cget("text"))
        self.assertEqual(row.primary.cget("text"), "Retry")
        self.assertEqual(row.strip.cget("background"), mod.COLORS["dark"]["bad"])


class CardMouse(GuiCase):
    """Clicks on the drawn cards: the buttons answer, the rest of the card selects."""

    def setUp(self):
        super().setUp()
        self.item = mk(1, "downloading")
        app.items.append(self.item)
        app._items_changed()
        pump(100)
        self.row = app.view.row_for(self.item)
        self.canvas = app.view.canvas

    def at(self, part, dx=0.5, dy=0.5):
        x0, y0, x1, y1 = self.row.geo[part]
        return int(x0 + (x1 - x0) * dx - self.canvas.canvasx(0)), int(y0 + (y1 - y0) * dy - self.canvas.canvasy(0))

    def point(self, xy):
        self.canvas.event_generate("<Motion>", x=xy[0], y=xy[1])
        self.canvas.update()

    def press_release(self, down, up=None):
        self.point(down)
        self.canvas.event_generate("<ButtonPress-1>", x=down[0], y=down[1])
        if up:
            self.point(up)
        self.canvas.event_generate("<ButtonRelease-1>", x=(up or down)[0], y=(up or down)[1])
        self.canvas.update()

    def test_the_primary_button_acts_and_does_not_select_the_card(self):
        with mock.patch.object(app, "pause_item") as pause:
            self.press_release(self.at("primary"))
        pause.assert_called_once_with(self.item)
        self.assertFalse(app.selected)

    def test_releasing_outside_the_button_does_nothing(self):
        with mock.patch.object(app, "pause_item") as pause:
            self.press_release(self.at("primary"), up=self.at("title"))
        pause.assert_not_called()

    def test_clicking_the_card_selects_it_and_the_right_button_opens_the_menu(self):
        self.point(self.at("title"))
        self.canvas.event_generate("<ButtonPress-1>", x=self.at("title")[0], y=self.at("title")[1])
        self.assertEqual(app.selected, {self.item.id})
        with mock.patch.object(app, "card_menu") as menu:
            xy = self.at("meta", 0.9)
            self.point(xy)
            self.canvas.event_generate("<Button-2>" if mod.IS_MAC else "<Button-3>", x=xy[0], y=xy[1])
        menu.assert_called_once()

    def test_the_more_button_opens_the_menu_below_itself(self):
        with mock.patch.object(app, "card_menu") as menu:
            self.press_release(self.at("more"))
        self.assertIs(menu.call_args.kwargs["anchor"].row, self.row)

    def test_loading_cards_animate_their_bar(self):
        app._drop(list(app.items))
        loading = mod.Item(U("loading1"))
        app.items.append(loading)
        app._items_changed()
        pump(200)
        row = app.view.row_for(loading)
        first = self.canvas.coords(row.i_busy)
        pump(300)
        self.assertNotEqual(first, self.canvas.coords(row.i_busy))


# ----------------------------------------------------------------------------- selection and keyboard

class Selection(GuiCase):
    def setUp(self):
        super().setUp()
        app.items.extend(mk(i) for i in range(10))
        app._items_changed()
        pump(80)
        self.items = list(app.items)
        self.ctrl = 0x8 if mod.IS_MAC else 0x4

    def ids(self, *indexes):
        return {self.items[i].id for i in indexes}

    def test_click_shift_and_ctrl(self):
        I = self.items
        app.row_click(I[2], Click())
        self.assertEqual(app.selected, self.ids(2))
        app.row_click(I[5], Click(0x1))
        self.assertEqual(app.selected, self.ids(2, 3, 4, 5))
        app.row_click(I[8], Click(self.ctrl))
        self.assertEqual(app.selected, self.ids(2, 3, 4, 5, 8))
        app.row_click(I[8], Click(self.ctrl))
        self.assertEqual(app.selected, self.ids(2, 3, 4, 5))
        self.assertEqual(app.view.row_for(I[3]).outer.cget("background"), mod.COLORS["dark"]["accent"])

    def test_select_all_and_clear(self):
        app.select_all()
        self.assertEqual(len(app.selected), 10)
        app.clear_selection()
        self.assertFalse(app.selected)

    def test_arrow_keys_home_and_end(self):
        I = self.items
        app.row_click(I[0], Click())
        app.key_move(1)
        self.assertEqual(app.selected, self.ids(1))
        app.key_move(1, extend=True)
        self.assertEqual(app.selected, self.ids(1, 2))
        app.key_move(2)
        self.assertEqual(app.selected, self.ids(9))
        app.key_move(-2)
        self.assertEqual(app.selected, self.ids(0))

    def test_move_up_and_down(self):
        I = self.items
        app.row_click(I[4], Click())
        app.move_selected(-1)
        self.assertEqual(app.items.index(I[4]), 3)
        self.assertIs(app.items[4], I[3])
        app.move_selected(1)
        app.move_selected(1)
        self.assertEqual(app.items.index(I[4]), 5)

    def test_delete_removes_exactly_the_selection(self):
        I = self.items
        app.row_click(I[4], Click())
        app.row_click(I[6], Click(0x1))
        gone = app.selected_items()
        app.key_remove()
        pump(50)
        self.assertEqual(len(gone), 3)
        self.assertEqual(len(app.items), 7)
        self.assertFalse(app.selected)
        self.assertTrue(all(it.removed and it not in app.items for it in gone))

    def test_enter_opens_the_options_of_the_selected_item(self):
        seen = []
        with mock.patch.object(app, "open_item_options", lambda it: seen.append(it)):
            app.row_click(self.items[0], Click())
            app.key_activate()
        self.assertEqual(seen, [self.items[0]])


# ------------------------------------------------------------------------------------------ menus

class Menus(GuiCase):
    def popup(self, item, event=None, anchor=None):
        captured = []
        with mock.patch.object(mod.tk.Menu, "tk_popup", lambda self, x, y: captured.append(self)):
            app.card_menu(item, event or type("E", (), {"x_root": 5, "y_root": 5})(), anchor=anchor)
        return labels(captured[-1])

    def test_failed_item_offers_retry_options_and_an_update(self):
        it = mk(1, "failed")
        it.error = "ERROR: Unable to extract uploader id"
        app.items.append(it)
        app._items_changed()
        pump(80)
        found = self.popup(it, anchor=app.view.row_for(it).more)
        self.assertTrue(any(x.startswith("Retry") for x in found))
        for wanted in ("Update yt-dlp, then retry", "Options …", "Remove", "Copy error message"):
            self.assertIn(wanted, found)

    def test_finished_item_offers_the_file(self):
        it = mk(2, "done")
        it.path = "/nonexistent/file.mp4"
        app.items.append(it)
        app._items_changed()
        found = self.popup(it)
        self.assertIn("Show in folder", found)
        self.assertIn("Copy link", found)
        self.assertFalse(any(x.startswith("Retry") for x in found))

    def test_running_and_paused_items(self):
        running, paused = mk(1, "downloading"), mk(2, "paused")
        app.items.extend([running, paused])
        app._items_changed()
        found = self.popup(running)
        self.assertEqual([x for x in found if x in ("Pause", "Cancel", "Resume")], ["Pause", "Cancel"])
        found = self.popup(paused)
        self.assertEqual([x for x in found if x in ("Pause", "Cancel", "Resume")], ["Resume", "Cancel"])

    def test_partial_data_can_be_thrown_away(self):
        it = mk(1, "paused")
        it.dests = [str(self.out / "x.mp4")]
        (self.out / "x.mp4.part").write_bytes(b"x" * 3_000_000)
        app.items.append(it)
        app._items_changed()
        found = self.popup(it)
        self.assertTrue(any(re.fullmatch(r"Start over \(delete .* partial data\)", x) for x in found), found)

    def test_several_selected_items_get_bulk_entries(self):
        a, b, c = mk(1, "failed"), mk(2, "downloading"), mk(3, "paused")
        app.items.extend([a, b, c])
        app._items_changed()
        app.selected = {a.id, b.id, c.id}
        found = self.popup(a)
        for wanted in ("Retry 1", "Pause 1", "Cancel 1", "Resume 1", "Copy 3 links", "Remove 3 items"):
            self.assertIn(wanted, found)

    def menu(self, label):
        bar = app.menubar
        for i in range(bar.index("end") + 1):
            if bar.type(i) == "cascade" and bar.entrycget(i, "label") == label:
                return root.nametowidget(bar.entrycget(i, "menu"))
        self.fail(f"no {label} menu")

    def test_theme_is_switched_from_the_view_menu_and_remembered(self):
        app.items.append(mk(1))
        app._items_changed()
        self.addCleanup(lambda: (app.theme_mode.set("dark"), app.set_theme_mode()))
        view = self.menu("View")
        for i in range(view.index("end") + 1):
            if view.type(i) == "radiobutton" and view.entrycget(i, "label") == "Light theme":
                view.invoke(i)
        pump(100)
        self.assertEqual((app.theme, mod.load_settings()["theme"]), ("light", "light"))
        waiting = mod.COLORS["light"][mod.STATUS_COLOR["queued"]]
        self.assertEqual(app.view.row_for(app.items[0]).strip.cget("background"), waiting)

    def test_number_shortcuts_switch_between_queue_history_and_log(self):
        key = "Command" if mod.IS_MAC else "Control"
        root.focus_force()
        root.update()
        root.event_generate(f"<{key}-Key-2>")
        root.update()
        self.assertEqual(app.tabbar.current, 1)
        root.event_generate(f"<{key}-Key-3>")
        root.update()
        self.assertEqual(app.tabbar.current, 2)
        root.event_generate(f"<{key}-Key-1>")
        root.update()
        self.assertEqual(app.tabbar.current, 0)

    def test_menu_bar_has_the_queue_commands(self):
        bar = app.menubar
        queue = None
        for i in range(bar.index("end") + 1):
            if bar.type(i) == "cascade" and bar.entrycget(i, "label") == "Queue":
                queue = root.nametowidget(bar.entrycget(i, "menu"))
        self.assertIsNotNone(queue)
        for wanted in ("Download / resume all", "Pause all", "Cancel running", "Retry failed", "Clear finished"):
            self.assertIn(wanted, labels(queue))


# ------------------------------------------------------------------ feedback: toast, notices, screens

class Feedback(GuiCase):
    def notice_texts(self):
        return [w.cget("text") for w in app.notice.winfo_children() if isinstance(w, mod.ttk.Label)]

    def test_toast_shows_and_hides_by_itself(self):
        app.toast("Hello toast", ms=200)
        pump(60)
        self.assertTrue(app.toast_lbl.winfo_ismapped())
        self.assertEqual(app.toast_lbl.cget("text"), "Hello toast")
        pump(350)
        self.assertFalse(app.toast_lbl.winfo_ismapped())

    def test_adding_a_link_twice_says_so(self):
        app.add_urls([U("dup")])
        pump(20)
        app.add_urls([U("dup")])
        pump(30)
        self.assertEqual(app.toast_lbl.cget("text"), "Already in the queue")
        self.assertEqual(len(app.items), 1)

    def finished(self, url, name="f.mp4", exists=True):
        item = mod.Item(url)
        item.added_mode, item.status, item.path = app.mode_key(), "done", str(self.out / name)
        if exists:
            (self.out / name).write_bytes(b"x")
        app.items.append(item)
        app._items_changed()
        return item

    def test_another_link_to_the_same_video_jumps_to_its_card(self):
        done = self.finished("https://www.youtube.com/watch?v=dupVID00001")
        app.add_urls(["https://youtu.be/dupVID00001?si=abc", "https://x.test/other"])
        pump(30)
        self.assertEqual(len(app.items), 2, "the same video must not get a second card")
        self.assertEqual(app.selected, {done.id})
        app.add_urls(["https://youtu.be/dupVID00001"])
        self.assertEqual(app.toast_lbl.cget("text"), "Already downloaded")

    def test_the_same_video_in_another_format_is_another_download(self):
        self.finished("https://youtu.be/fmtVID00001")
        app.kind_var.set("audio")
        app._on_kind()
        app.add_urls(["https://youtu.be/fmtVID00001"])
        self.assertEqual(len(app.items), 2)
        self.assertEqual(app.items[1].added_mode, "mp3")
        self.assertNotIn("name", app.items[1].overrides, "video and mp3 do not share a file name")

    def test_another_quality_of_the_same_video_gets_its_own_file_name(self):
        app.quality_var.set(mod.quality_label("video1080"))
        app.sync_mode_options()
        app.add_urls(["https://youtu.be/qltVID00001"])
        app.quality_var.set(mod.quality_label("video720"))
        app.sync_mode_options()
        app.add_urls(["https://youtu.be/qltVID00001"])
        self.assertEqual(len(app.items), 2)
        self.assertEqual(app.items[1].overrides["name"], "%(title)s [Up to 720p]")
        self.assertIn("file named by quality", mod.overrides_summary(app.items[1].overrides))

    def test_a_finished_download_whose_file_is_gone_is_downloaded_again(self):
        gone = self.finished("https://youtu.be/gonVID00001", exists=False)
        app.add_urls(["https://youtu.be/gonVID00001"])
        self.assertEqual(len(app.items), 1)
        self.assertIsNot(app.items[0], gone)
        self.assertNotIn("Already", app.toast_lbl.cget("text"))

    def test_history_skips_a_video_that_is_still_on_disk_but_not_one_that_was_deleted(self):
        (self.out / "h.mp4").write_bytes(b"x")
        app.history = [{"title": "H", "url": "https://youtu.be/hisVID00001", "path": str(self.out / "h.mp4"),
                        "mode": "video", "time": time.time()},
                       {"title": "G", "url": "https://youtu.be/hisVID00002", "path": str(self.out / "nope.mp4"),
                        "mode": "video", "time": time.time()}]
        app.arch_var.set(True)
        app.add_urls(["https://youtu.be/hisVID00001"])
        self.assertEqual(app.items, [])
        self.assertIn("History", app.toast_lbl.cget("text"))
        app.add_urls(["https://youtu.be/hisVID00002"])
        self.assertEqual(len(app.items), 1, "the file was deleted: download it again")
        app.arch_var.set(False)
        app.add_urls(["https://youtu.be/hisVID00001"])
        self.assertEqual(len(app.items), 2, "with the setting off it is always added")

    def test_yt_dlps_own_archive_is_not_used_by_the_window(self):
        app.arch_var.set(True)
        self.assertIs(app.snapshot()["archive"], False)

    def test_notices_by_priority(self):
        app.set_notice("update", "New version", actions=[("Release page", lambda: None)])
        pump(30)
        self.assertTrue(app.notice.winfo_ismapped())
        app.set_notice("setup", "Setting up", busy=True, dismiss=False)
        pump(30)
        self.assertEqual(self.notice_texts(), ["Setting up"])
        app.clear_notice("setup")
        pump(30)
        self.assertEqual(self.notice_texts(), ["New version"])
        app.clear_notice("update")
        pump(30)
        self.assertFalse(app.notice.winfo_ismapped())

    def test_empty_list_shows_the_welcome_screen(self):
        pump(50)
        self.assertTrue(app.empty.winfo_ismapped())
        self.assertEqual(app.empty_title.cget("text"), "Paste a link to get started")
        app.items.append(mk(1))
        app._items_changed()
        pump(50)
        self.assertFalse(app.empty.winfo_ismapped())

    def test_first_start_and_failed_setup_screens(self):
        with mock.patch.object(mod, "find_ytdlp", lambda: None):
            app.set_tools_busy(True, "Setting up: downloading yt-dlp (first start only) …")
            pump(50)
            self.assertEqual(app.empty_title.cget("text"), "Setting up …")
            self.assertTrue(app.empty_bar.winfo_ismapped())
            self.assertFalse(app.notice.winfo_ismapped(), "the bar would repeat what the big screen says")
            app.items.append(mk(1))
            app._items_changed()
            pump(50)
            self.assertEqual(str(app.start_btn.cget("state")), "disabled")
            self.assertTrue(app.notice.winfo_ismapped(), "with links in the list the bar is the only hint")
            app._drop(list(app.items))
            app._items_changed()
            app.set_tools_busy(False)
            app.setup_error = "Could not download yt-dlp."
            app._update_empty()
            pump(50)
            self.assertEqual(app.empty_title.cget("text"), "Setup did not finish")
            self.assertTrue(app.empty_retry.winfo_ismapped())
            app.setup_error = ""
        app._update_empty()

    def test_footer_buttons_appear_when_they_make_sense(self):
        pump(30)
        self.assertFalse(app.clear_btn.winfo_ismapped())
        self.assertFalse(app.stop_btn.winfo_ismapped())
        app.items.extend([mk(1, "done"), mk(2)])
        app._items_changed()
        pump(50)
        self.assertTrue(app.clear_btn.winfo_ismapped())
        self.assertEqual(app.start_btn.cget("text"), "Download all (1)")
        app.clear_finished()
        pump(30)
        self.assertEqual([it.status for it in app.items], ["queued"])

    def test_tab_order_follows_the_visual_order_of_the_footer(self):
        app.items.extend([mk(1, "done"), mk(2), mk(3, "downloading")])
        app._items_changed()
        pump(50)
        order, w = [], app.url_entry
        for _ in range(60):
            w = w.tk_focusNext()
            if w is None or w == app.url_entry:
                break
            order.append(w)
        self.assertLess(order.index(app.clear_btn), order.index(app.stop_btn))
        self.assertLess(order.index(app.stop_btn), order.index(app.start_btn))

    def test_window_title_shows_the_overall_progress(self):
        a, b = mk(1, "done"), mk(2, "downloading")
        b.pct = 50
        app.items.extend([a, b])
        app.batch_ids, app.running = {a.id, b.id}, True
        app._items_changed()
        pump(30)
        self.assertTrue(root.title().startswith("75%"), root.title())
        app.running = False
        app._update_title()
        self.assertEqual(root.title(), app.base_title)


class HistoryAndLog(GuiCase):
    def test_history_list_and_search(self):
        t = time.localtime()
        midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        app.history = [{"title": title, "url": U(i), "path": f"/nope/{i}.mp4", "mode": mode, "time": when}
                       for i, (title, mode, when) in enumerate([("Gamma talk", "video", midnight - 86400 * 10),
                                                                ("Beta clip", "video1080", midnight - 3600),
                                                                ("Alpha song", "mp3", time.time())])]
        app.refresh_history()
        pump(30)
        self.assertEqual(len(app.hist.get_children()), 3)
        rows = [app.hist.item(i, "values") for i in app.hist.get_children()]      # newest first
        self.assertEqual([r[1] for r in rows], ["Audio · MP3", "Video · Up to 1080p", "Video · Best quality"])
        self.assertTrue(rows[0][2].startswith("Today") and rows[1][2].startswith("Yesterday"), rows)
        self.assertRegex(rows[2][2], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(all(r[3] == "missing" for r in rows), "files that do not exist are marked")
        app.hist_filter.set("beta")
        pump(30)
        self.assertEqual(len(app.hist.get_children()), 1)
        self.assertIn("of 3", app.hist_count.cget("text"))

    def test_log_filter_copy_and_clear(self):
        for i in range(30):
            app.log(f"line {i} " + ("ERROR boom" if i == 7 else "ok"))
        pump(120)
        app.log_filter.set("error")
        pump(50)
        shown = self.log_text()
        self.assertIn("boom", shown)
        self.assertEqual(shown.strip().count("\n"), 0)
        app.log_filter.set("")
        pump(50)
        self.assertGreaterEqual(self.log_text().count("line "), 30)
        app.copy_log()
        self.assertIn("ERROR boom", root.clipboard_get())
        app.clear_log()
        self.assertEqual(self.log_text().strip(), "")


# ------------------------------------------------------------------------------------- downloads

class Downloads(GuiCase):
    def test_a_link_gets_its_preview_with_thumbnail(self):
        it = add(U("preview"))
        self.assertTrue(it.title.startswith("Rick Astley"))
        self.assertEqual(it.uploader, "Rick Astley")
        self.assertEqual(it.duration, 213)
        self.assertTrue(wait(lambda: it.thumb is not None, 10), "thumbnail was not loaded")
        self.assertIn("(1)", app.start_btn.cget("text"))

    def test_pasting_adds_every_link_once(self):
        root.clipboard_clear()
        root.clipboard_append(f"look: {U('a')} and {U('b')}\n{U('a')}")
        self.assertEqual(app._on_entry_paste(None), "break")
        self.assertEqual(len(app.items), 2)
        root.clipboard_clear()
        root.clipboard_append("just some text")
        self.assertIsNone(app._on_entry_paste(None))

    def test_unreadable_link_is_still_queued(self):
        it = add(U("bad_link"))
        self.assertIn("No preview available", it.uploader)

    def test_video_download_runs_to_the_end(self):
        it = add(U("video1"))
        app.start_all()
        self.assertTrue(wait(lambda: it.status == "done", 30), it.status)
        self.assertTrue(it.path.endswith(".mp4") and Path(it.path).stat().st_size == FULL_SIZE)
        self.assertEqual(it.pct, 100)
        self.assertEqual(mod.load_history()[-1]["url"], U("video1"))
        pump(50)
        self.assertIn("1 done", app.tabbar.summary.cget("text"))
        self.assertTrue(app.clear_btn.winfo_ismapped())
        self.assertFalse(app.running)

    def test_audio_mode(self):
        app.kind_var.set("audio")
        app._on_kind()
        self.assertEqual(app.mode_key(), "mp3")
        self.assertEqual(str(app.also_combo.cget("state")), "disabled")
        it = add(U("audio1"))
        app.start_all()
        self.assertTrue(wait(lambda: it.status == "done", 30), it.status)
        self.assertTrue(it.path.endswith(".mp3"))

    def test_links_start_on_their_own_by_default(self):
        self.assertTrue(DEFAULT_AUTOSTART, "new installs should start downloads without an extra click")
        app.auto_var.set(True)
        app.add_urls([U("auto1")])
        self.assertTrue(wait(lambda: app.items and app.items[0].status == "done", 30), app.items[0].status)

    def test_parallel_limit_is_respected(self):
        app.parallel_var.set("2")
        items = [add(U(f"slow_par{i}")) for i in range(3)]
        app.start_all()
        self.assertTrue(wait(lambda: sum(i.status == "downloading" for i in items) == 2, 10))
        self.assertEqual([i.status for i in items].count("queued"), 1)
        self.assertTrue(wait(lambda: all(i.status == "done" for i in items), 40))

    def test_cancel_keeps_the_partial_file_and_retry_continues(self):
        it = add(U("slow_cancel"))
        app.start_item(it)
        self.assertTrue(wait(lambda: it.status == "downloading" and it.pct >= 20, 15))
        app.cancel_item(it)
        self.assertTrue(wait(lambda: it.status == "cancelled", 15), it.status)
        kept = mod.partial_size(it.dests)
        self.assertGreater(kept, 0)
        self.assertIn("kept", app.view.row_for(it).status.cget("text"))
        app.retry_item(it)
        self.assertTrue(wait(lambda: it.status == "done", 40), it.status)
        self.assertTrue(it.resumed)
        self.assertEqual(Path(it.path).stat().st_size, FULL_SIZE)
        self.assertEqual(mod.partial_size(it.dests), 0)

    def test_removing_a_running_download_stops_it(self):
        it = add(U("slow_remove"))
        app.start_item(it)
        self.assertTrue(wait(lambda: it.status == "downloading", 15))
        app.remove_item(it)
        pump(800)
        self.assertEqual(app.items, [])
        self.assertFalse(app.running)

    def test_private_video_fails_with_a_friendly_message_and_is_not_retried(self):
        it = add(U("fail_private"))
        app.start_all()
        self.assertTrue(wait(lambda: it.status == "failed", 15), it.status)
        self.assertIn("Private video", mod.friendly_error(it.error))
        self.assertEqual(it.retries, 0)
        self.assertIn("Private video", app.view.row_for(it).status.cget("text"))
        app.retry_item(it)
        self.assertTrue(wait(lambda: it.status == "failed", 15))

    def test_playlist_links_expand_into_the_chosen_videos(self):
        app.single_var.set(False)                              # "Single video only" is on by default
        self.addCleanup(app.single_var.set, True)
        with mock.patch.object(app, "_pick_playlist", lambda info: info["entries"][:3]):
            app.add_urls([U("playlist1")])
            self.assertTrue(wait(lambda: len(app.items) == 3, 15), len(app.items))
        self.assertTrue(all(it.status == "queued" for it in app.items))
        self.assertTrue(app.items[0].title.startswith("Playlist video number 1"))
        self.assertIn("Playlist 'My Test Playlist': 3 videos added.", self.log_text())


# ----------------------------------------------------------------------------- pause and resume

class PauseResume(GuiCase):
    def start_slow(self, name, pct=20):
        it = add(U(f"slow_{name}"))
        app.start_item(it)
        self.assertTrue(wait(lambda: it.status == "downloading" and it.pct >= pct, 20), (it.status, it.pct))
        return it

    def test_pause_keeps_the_data_and_resume_continues_there(self):
        it = self.start_slow("one")
        app.pause_item(it)
        self.assertTrue(wait(lambda: it.status == "paused", 15), it.status)
        kept = mod.partial_size(it.dests)
        self.assertTrue(0 < kept < FULL_SIZE, kept)
        pump(500)
        self.assertEqual(mod.partial_size(it.dests), kept, "the download kept running after 'Pause'")
        row = app.view.row_for(it)
        self.assertRegex(row.status.cget("text"), r"^Paused at \d+%\s+·\s+.* kept$")
        self.assertEqual(row.primary.cget("text"), "Resume")
        self.assertEqual(row.strip.cget("background"), mod.COLORS["dark"]["warn"])
        self.assertIn("1 paused", app.tabbar.summary.cget("text"))
        self.assertEqual(app.start_btn.cget("text"), "Resume all (1)")
        app.resume_item(it)
        self.assertTrue(wait(lambda: it.status == "done", 40), it.status)
        self.assertTrue(it.resumed, "yt-dlp did not report that it continued")
        self.assertEqual(Path(it.path).stat().st_size, FULL_SIZE)
        self.assertEqual(mod.partial_size(it.dests), 0)

    def test_space_pauses_and_resumes_the_selection(self):
        it = self.start_slow("space")
        app.row_click(it, Click())
        self.assertEqual(app.toggle_pause(), "break")
        self.assertTrue(wait(lambda: it.status == "paused", 15), it.status)
        app.toggle_pause()
        self.assertTrue(wait(lambda: it.status in ("downloading", "done"), 15), it.status)
        self.assertTrue(wait(lambda: it.status == "done", 30))

    def test_pause_all_holds_the_queue_and_resume_all_finishes_it(self):
        items = [add(U(f"slow_all{i}")) for i in range(3)]
        app.parallel_var.set("2")
        app.start_all()
        self.assertTrue(wait(lambda: sum(i.status == "downloading" and i.pct >= 10 for i in items) == 2, 20))
        self.assertTrue(app.stop_btn.winfo_ismapped())
        app.pause_all()
        self.assertTrue(wait(lambda: sum(i.status == "paused" for i in items) == 2, 15), [i.status for i in items])
        pump(400)
        self.assertEqual([i.status for i in items].count("queued"), 1, "the waiting item must not start")
        self.assertFalse(app.running)
        self.assertEqual(app.start_btn.cget("text"), "Resume all (3)")
        self.assertFalse(app.stop_btn.winfo_ismapped())
        app.start_all()
        self.assertTrue(wait(lambda: all(i.status == "done" for i in items), 60), [i.status for i in items])

    def test_cancelling_a_paused_download_keeps_its_data_and_start_over_discards_it(self):
        it = self.start_slow("over")
        app.pause_item(it)
        self.assertTrue(wait(lambda: it.status == "paused", 15))
        kept = mod.partial_size(it.dests)
        app.cancel_item(it)
        self.assertEqual(it.status, "cancelled")
        self.assertEqual(mod.partial_size(it.dests), kept)
        app.start_over([it])
        self.assertIn("Deleted", app.toast_lbl.cget("text"))
        self.assertTrue(wait(lambda: it.status == "done", 40), it.status)
        self.assertFalse(it.resumed, "starting over must not continue the old data")
        self.assertEqual(Path(it.path).stat().st_size, FULL_SIZE)

    def test_download_all_also_resumes_paused_items(self):
        it = self.start_slow("dall")
        app.pause_item(it)
        self.assertTrue(wait(lambda: it.status == "paused", 15))
        app.start_all()
        self.assertTrue(wait(lambda: it.status == "done", 40), it.status)
        self.assertTrue(it.resumed)


class Persistence(GuiCase):
    def test_the_queue_is_saved_without_finished_items_and_restored(self):
        waiting, failed, done, paused = mk(1), mk(2, "failed"), mk(3, "done"), mk(4, "paused")
        waiting.overrides = {"mode": "mp3"}
        failed.error = "ERROR: boom"
        paused.pct, paused.dests = 42.0, [str(self.out / "p.mp4")]
        app.items.extend([waiting, failed, done, paused])
        app._save_queue()
        saved = {d["url"]: d for d in mod.load_queue()}
        self.assertEqual(set(saved), {waiting.url, failed.url, paused.url})
        self.assertEqual(saved[paused.url]["status"], "paused")
        self.assertFalse(saved[paused.url]["interrupted"])
        self.assertEqual(saved[waiting.url]["overrides"], {"mode": "mp3"})
        app._drop(list(app.items))
        app._restore_queue()
        self.assertEqual(sorted(it.status for it in app.items), ["failed", "paused", "queued"])
        back = {it.url: it for it in app.items}
        self.assertEqual(back[paused.url].pct, 42.0)
        self.assertEqual(back[paused.url].dests, [str(self.out / "p.mp4")])
        self.assertEqual(back[waiting.url].overrides, {"mode": "mp3"})
        pump(50)
        self.assertIn("paused when the app was closed", " ".join(
            w.cget("text") for w in app.notice.winfo_children() if isinstance(w, mod.ttk.Label)))

    def test_a_crash_during_a_download_is_repaired_before_resuming(self):
        it = add(U("slow_crash"))
        app.start_item(it)
        self.assertTrue(wait(lambda: it.status == "downloading" and mod.partial_size(it.dests) > 2_000_000, 20),
                        (it.status, it.pct))
        app._save_queue()                                     # what the last periodic save looked like
        entry = [d for d in mod.load_queue() if d["url"] == it.url][0]
        self.assertEqual((entry["status"], entry["interrupted"]), ("paused", True))
        last_save = mod.QUEUE_FILE.read_text(encoding="utf-8")
        app._drop([it])                                       # the "crash": the process is gone, the file stays
        pump(800)
        mod.QUEUE_FILE.write_text(last_save, encoding="utf-8")         # a dead app writes nothing more
        app._restore_queue()
        back = app.items[0]
        self.assertEqual((back.status, back.interrupted), ("paused", True))
        before = mod.partial_size(back.dests)
        self.assertGreater(before, 1 << 20)
        app.resume_item(back)
        self.assertTrue(wait(lambda: back.status == "done", 40), back.status)
        pump(100)
        self.assertIn("re-fetching the last", self.log_text())
        self.assertRegex(self.log_text(), r"Resuming download at byte \d+")      # continued, not started over
        self.assertEqual(Path(back.path).stat().st_size, FULL_SIZE)


# -------------------------------------------------------------------------------- automatic retries

class AutoRetry(GuiCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(mod, "AUTO_RETRY_DELAYS", (1, 1, 1))
        patch.start()
        self.addCleanup(patch.stop)

    def test_network_errors_are_retried_and_continue_from_the_partial_file(self):
        it = add(U("flaky2_net"))
        app.start_item(it)
        seen = []
        self.assertTrue(wait(lambda: it.status == "done" or (
            seen.append(app.view.row_for(it).status.cget("text")) or False), 40), it.status)
        self.assertTrue(any(re.match(r"Connection problem - retrying in \d+ s \(\d/3\)", t) for t in seen), seen[-3:])
        self.assertEqual(self.log_text().count("Network problem - retrying in"), 2)
        self.assertTrue(it.resumed)
        self.assertEqual(it.retries, 0, "the counter starts over after a success")
        self.assertEqual(Path(it.path).stat().st_size, FULL_SIZE)

    def test_the_error_on_the_line_after_ERROR_is_read_too(self):
        it = add(U("multiline1_net"))
        app.start_item(it)
        self.assertTrue(wait(lambda: it.status == "done", 40), (it.status, it.error))
        self.assertIn("Network problem - retrying in", self.log_text())

    def test_retries_are_limited(self):
        with mock.patch.object(mod, "AUTO_RETRY_DELAYS", (1,)):
            it = add(U("flaky9_limit"))
            app.start_item(it)
            self.assertTrue(wait(lambda: it.status == "failed", 30), it.status)
        self.assertEqual(it.retries, 1)
        self.assertGreater(mod.partial_size(it.dests), 0, "the partial file must stay for a manual retry")

    def test_real_errors_are_not_retried(self):
        it = add(U("notfound_gone"))
        app.start_item(it)
        self.assertTrue(wait(lambda: it.status == "failed", 15), it.status)
        self.assertEqual(it.retries, 0)
        self.assertIn("404", it.error)

    def test_download_all_skips_the_wait(self):
        with mock.patch.object(mod, "AUTO_RETRY_DELAYS", (60,)):
            it = add(U("flaky1_skip"))
            app.start_item(it)
            self.assertTrue(wait(lambda: it.status == "queued" and it.retry_at > time.time() + 30, 15), it.status)
            app.start_all()
            self.assertTrue(wait(lambda: it.status == "done", 20), it.status)


# ------------------------------------------------------------------ options, profiles, settings

class Options(GuiCase):
    def setUp(self):
        super().setUp()
        saved = app.collect_options()
        self.addCleanup(app.apply_options, saved)

    def test_item_options_and_window_settings_reach_yt_dlp(self):
        it = add(U("opts"))
        dialog = mod.ItemDialog(app, it)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        dialog.mode_var.set(mod.mode_label("video720"))
        dialog.start_var.set("0:10")
        dialog.end_var.set("0:30")
        dialog.exact_var.set(True)
        dialog.split_var.set(True)
        dialog.extra_var.set("--restrict-filenames --foo 'a b'")
        dialog.load_formats()
        self.assertTrue(wait(lambda: len(dialog.rows) == 3, 15), dialog.rows)
        self.assertEqual([r["id"] for r in dialog.rows], ["137", "18", "251"])      # storyboard skipped
        dialog.tree.selection_set(("251", "137"))
        dialog.tree.event_generate("<<TreeviewSelect>>")
        dialog.update()
        self.assertEqual(dialog.picked_format, "137+251")
        dialog.tree.selection_set(("137",))
        dialog.tree.event_generate("<<TreeviewSelect>>")
        dialog.update()
        self.assertEqual(dialog.picked_format, "137+ba/137")
        dialog.tree.selection_set(("251", "137"))
        dialog.tree.event_generate("<<TreeviewSelect>>")
        dialog.update()
        dialog.chap_var.set(True)
        dialog.accept()
        ov = it.overrides
        self.assertEqual((ov.get("mode"), ov.get("format"), ov.get("section")),
                         ("video720", "137+251", "*0:00:10-0:00:30"))
        self.assertTrue(ov.get("exact") and ov.get("split") and ov.get("chapters") is True)
        app.view.touch(it)
        self.assertIn("⚙", app.view.row_for(it).meta.cget("text"))

        app.subs_var.set(True)
        app.sub_langs_var.set("en,fr")
        app.name_var.set("%(uploader)s - %(title)s")
        app.proxy_var.set("http://p:1")
        app.args_var.set("--global-flag 1")
        app.sponsor_var.set(True)
        app.limit_var.set("2M")
        (WORK / "args.log").unlink(missing_ok=True)
        app.start_all()
        self.assertTrue(wait(lambda: any("-o" in c for c in calls()), 15))
        a = [c for c in calls() if "-o" in c][0]

        def value(flag):
            return a[a.index(flag) + 1]
        self.assertEqual(value("-f"), "137+251")
        self.assertEqual(value("--download-sections"), "*0:00:10-0:00:30")
        self.assertIn("--force-keyframes-at-cuts", a)
        self.assertTrue("--embed-chapters" in a and "--split-chapters" in a)
        self.assertEqual(value("--sub-langs"), "en,fr")
        self.assertEqual((value("--proxy"), value("--limit-rate")), ("http://p:1", "2M"))
        self.assertIn("--sponsorblock-remove", a)
        self.assertTrue(value("-o").endswith("%(uploader)s - %(title)s.%(ext)s"))
        self.assertTrue("--global-flag" in a and "--restrict-filenames" in a and a[a.index("--foo") + 1] == "a b")
        self.assertEqual(it.mode, "video720")

    def test_an_invalid_cut_time_is_rejected(self):
        it = add(U("badtime"))
        dialog = mod.ItemDialog(app, it)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        dialog.start_var.set("abc")
        with mock.patch.object(mod.messagebox, "showwarning") as warned:
            dialog.accept()
        self.assertTrue(warned.called)
        self.assertEqual(it.overrides, {})

    def delete_test_profile(self):
        app.profile_var.set("Test1")
        with mock.patch.object(mod.messagebox, "askyesno", return_value=True):
            app.delete_profile()

    def test_profiles_store_and_restore_the_settings(self):
        app.quality_var.set(mod.quality_label("video1080"))
        app.name_var.set("XYZ")
        app.profile_var.set("Test1")
        app.save_profile()
        self.addCleanup(self.delete_test_profile)
        app.name_var.set("other")
        app.kind_var.set("audio")
        app._on_kind()
        app.profile_var.set("Test1")
        app.load_profile()
        self.assertEqual((app.name_var.get(), app.mode_key(), app.kind_var.get()), ("XYZ", "video1080", "video"))
        self.assertIn("Test1", mod.load_settings()["profiles"])

    def test_settings_window_is_a_single_instance_and_saves_on_close(self):
        app.open_settings()
        app.open_settings()
        root.update()
        windows = [w for w in root.winfo_children() if isinstance(w, mod.SettingsDialog)]
        self.assertEqual(len(windows), 1)
        self.assertIsNotNone(app.subs_check)
        app.settings_win.close()
        root.update()
        self.assertIsNone(app.subs_check)
        self.assertFalse(app.settings_win.winfo_exists())

    def test_clipboard_watcher_adds_only_new_links(self):
        root.clipboard_clear()
        root.clipboard_append(U("from_clip_before"))
        root.update()
        app.clip_watch_var.set(True)
        self.addCleanup(lambda: app.clip_watch_var.set(False))
        pump(1500)
        self.assertFalse(any("from_clip_before" in i.url for i in app.items), "existing clipboard text is not added")
        root.clipboard_clear()
        root.clipboard_append(U("from_clip_new"))
        self.assertTrue(wait(lambda: any("from_clip_new" in i.url for i in app.items), 6))


# ------------------------------------------------------------------------------------- about, icons

class AboutAndIcons(GuiCase):
    def test_about_shows_the_tool_versions_and_copies_them(self):
        dialog = mod.AboutDialog(app)
        self.addCleanup(dialog.destroy)
        self.assertTrue(wait(lambda: "yt-dlp:" in dialog.info_var.get(), 10), dialog.info_var.get())
        self.assertIn("Python", dialog.info_var.get())
        dialog.copy_info()
        self.assertIn("yt-dlp:", root.clipboard_get())

    def test_icons_for_the_release_build(self):
        from PIL import Image
        names = mod.write_icons(str(self.out / "icons"))
        self.assertEqual(names, ["icon.png", "icon.ico", "icon.icns"])
        for name in names:
            self.assertGreater((self.out / "icons" / name).stat().st_size, 1000)
        with Image.open(self.out / "icons" / "icon.png") as icon:
            self.assertEqual(icon.size, (1024, 1024))
        self.assertIsNotNone(getattr(app, "icon_photo", None), "the window icon was not set")

    def test_icon_command_line_flag(self):
        import subprocess
        target = self.out / "flag"
        done = subprocess.run([sys.executable, str(gs.ROOT / "simple-ytdlp.py"), "--make-icons", str(target)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["icon.icns", "icon.ico", "icon.png"])


if __name__ == "__main__":
    unittest.main()
