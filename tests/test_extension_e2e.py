"""The Chrome extension against the real window: a Chromium with the extension loaded (Playwright) sends links
to the running app. Covers the real popup, the popup's states, the right-click menu and the settings page.

Needs Tk, a display, Playwright with its Chromium (pip install playwright && playwright install chromium);
skipped otherwise.
"""
import faulthandler
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gui_support as gs  # noqa: E402

HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_ytdlp.py"
EXTENSION = HERE.parent / "extension"

mod = root = app = driver = None
answers: queue.Queue = queue.Queue()
state = {"allow": True, "questions": []}
_patches: list = []


def pump(ms=50):
    gs.pump(root, ms)


def wait(condition, timeout=30.0):
    return gs.wait_until(root, condition, timeout)


def command(line, timeout=90.0):
    """Send a command to the Chromium driver while the window keeps running; returns its JSON answer."""
    driver.stdin.write(line + "\n")
    driver.stdin.flush()
    got = []

    def ready():
        try:
            got.append(answers.get_nowait())
        except queue.Empty:
            pass
        return bool(got)

    if not wait(ready, timeout):
        raise AssertionError(f"no answer to {line!r}")
    return got[0]


def setUpModule():
    global mod, root, app, driver
    gs.require_gui()
    if importlib.util.find_spec("playwright") is None:
        raise unittest.SkipTest("playwright is not installed (pip install playwright && playwright install chromium)")
    work = gs.scratch_dir("ext-")
    os.environ["FAKE_YTDLP_DIR"] = str(work)
    mod, _ = gs.load_app()
    _patches.extend([
        mock.patch.object(mod, "find_ytdlp", lambda: [sys.executable, str(FAKE)]),
        mock.patch.object(mod, "ensure_tools", lambda log=print: True),
        mock.patch.object(mod, "find_js_runtime", lambda: []),
        mock.patch.object(mod, "find_ffmpeg", lambda: ("ffmpeg", "system")),
    ])
    for p in _patches:
        p.start()

    def ask(title, message, **kw):
        state["questions"].append(message)
        return state["allow"]

    _patches.append(mock.patch.object(mod.messagebox, "askyesno", ask))
    _patches[-1].start()
    mod, root, app = gs.start_app({"mode": "video", "parallel": 1, "autostart": False})
    faulthandler.dump_traceback_later(300, exit=True)
    driver = subprocess.Popen([sys.executable, str(HERE / "extension_driver.py"), str(EXTENSION)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def read():
        for line in driver.stdout:
            try:
                answers.put(json.loads(line))
            except ValueError:
                pass

    threading.Thread(target=read, daemon=True).start()
    hello = None
    wait_for = []

    def got_hello():
        try:
            wait_for.append(answers.get_nowait())
        except queue.Empty:
            pass
        return bool(wait_for)

    if not gs.wait_until(root, got_hello, 60) or driver.poll() is not None:
        driver.kill()
        raise unittest.SkipTest("Chromium with the extension did not start")
    hello = wait_for[0]
    assert hello.get("ready"), hello


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()
    try:
        driver.stdin.write("quit\n")
        driver.stdin.flush()
        driver.wait(15)
    except Exception:
        driver.kill()
    for it in list(app.items):
        it.stop.set()
    gs.pump(root, 200)
    gs.close_app(root)
    for p in reversed(_patches):
        p.stop()
    os.environ.pop("FAKE_YTDLP_DIR", None)


def U(name):
    return f"https://x.test/watch?v={name}"


class Extension(unittest.TestCase):
    def setUp(self):
        for it in list(app.items):
            it.stop.set()
        app._drop(list(app.items))
        app.auto_var.set(False)
        app.bridge_var.set(True)
        app.trusted.clear()
        state.update(allow=True, questions=[])
        app._items_changed()
        pump(30)

    def test_1_not_running_is_explained(self):
        app.bridge_var.set(False)
        out = command(f"popup_stub {U('off')}")
        self.assertEqual(out["state"], "state bad")
        self.assertIn("not running", out["message"])
        self.assertIn("Try again", out["buttons"])
        self.assertIn("Get the app", out["buttons"])
        self.assertEqual(app.items, [])

    def test_2_the_real_popup_adds_the_page_after_one_confirmation(self):
        command("popup_real 1")
        self.assertTrue(wait(lambda: len(app.items) == 1, 40), "the popup did not send the page")
        self.assertIn("/watch?v=real1", app.items[0].url)
        self.assertEqual(len(state["questions"]), 1)
        self.assertIn("chrome-extension://", state["questions"][0])
        command("popup_real 2")
        self.assertTrue(wait(lambda: len(app.items) == 2, 40))
        self.assertEqual(len(state["questions"]), 1, "asked only the first time")

    def test_3_popup_states(self):
        out = command(f"popup_stub {U('pop')}")
        self.assertEqual((out["state"], out["message"]), ("state ok", "Added to the download list."))
        self.assertEqual([it.url for it in app.items], [U("pop")])
        out = command(f"popup_stub {U('pop')}")
        self.assertEqual(out["state"], "state ok")
        self.assertIn("Already in the list", out["message"])
        self.assertEqual(len(app.items), 1)
        out = command("popup_stub chrome://extensions")
        self.assertEqual(out["state"], "state bad")
        self.assertIn("no link", out["message"])

    def test_4_denying_in_the_app(self):
        state["allow"] = False
        out = command(f"popup_stub {U('deny')}")
        self.assertEqual(out["state"], "state bad")
        self.assertIn("Not allowed", out["message"])
        self.assertEqual(app.items, [])

    def test_5_right_click_menu_and_icon_mode(self):
        out = command(f"menu linkAudio {U('menuaudio')}")
        self.assertEqual(out["badge"], "✓")
        self.assertTrue(wait(lambda: len(app.items) == 1, 20))
        self.assertEqual(app.items[0].overrides.get("mode"), "mp3")
        out = command("options mp3")
        self.assertEqual(out["mode"], "mp3")
        self.assertIn("Connected to simple-ytdlp", out["message"])
        out = command(f"popup_stub {U('iconmode')}")
        self.assertEqual(out["state"], "state ok")
        self.assertEqual(app.items[-1].overrides.get("mode"), "mp3", "the icon follows the saved choice")
        command("options ")


if __name__ == "__main__":
    unittest.main()
