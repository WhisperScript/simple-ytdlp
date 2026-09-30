"""End-to-end tests of pausing and resuming with the real yt-dlp.

A local HTTP server (tests/range_server.py) plays the video host: it supports Range requests, limits the speed
and can drop connections. The window downloads from it, is paused, loses its partial file's tail or sees the
network fail - and every time the finished file must be bit-identical to the source.

Needs Tk, a display and the yt-dlp package (pip install yt-dlp); skipped otherwise.
"""
import faulthandler
import hashlib
import importlib.util
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gui_support as gs  # noqa: E402
from range_server import RangeServer  # noqa: E402

mod = root = app = server = None
WORK = OUT = SOURCE = None
SOURCE_SHA = ""
_patches: list = []


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def setUpModule():
    global mod, root, app, server, WORK, OUT, SOURCE, SOURCE_SHA
    gs.require_gui()
    if importlib.util.find_spec("yt_dlp") is None:
        raise unittest.SkipTest("yt-dlp is not installed (pip install yt-dlp)")
    mod, _ = gs.load_app()
    WORK = gs.scratch_dir("e2e-")
    OUT = WORK / "out"
    SOURCE = WORK / "big.mp4"
    SOURCE.write_bytes(os.urandom(12 * 1024 * 1024))         # yt-dlp hands a direct link over as it is
    SOURCE_SHA = sha256(SOURCE)
    server = RangeServer(SOURCE, rate=4_000_000)
    _patches.extend([
        mock.patch.object(mod, "find_ytdlp", lambda: [sys.executable, "-m", "yt_dlp"]),
        mock.patch.object(mod, "ensure_tools", lambda log=print: True),
        mock.patch.object(mod, "find_js_runtime", lambda: []),
    ])
    for p in _patches:
        p.start()
    mod, root, app = gs.start_app({"mode": "video", "parallel": 1, "autostart": False, "archive": False,
                                   "args": "--no-embed-metadata", "out": str(OUT), "single": True})
    faulthandler.dump_traceback_later(420, exit=True)


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()
    for it in list(app.items):
        it.stop.set()
    gs.pump(root, 300)
    gs.close_app(root)
    server.close()
    for p in _patches:
        p.stop()


def wait(condition, timeout=60.0):
    return gs.wait_until(root, condition, timeout)


class ResumeEndToEnd(unittest.TestCase):
    def setUp(self):
        for it in list(app.items):
            it.stop.set()
        app._drop(list(app.items))
        app.running = False
        app._items_changed()
        gs.pump(root, 300)                                   # let stopped processes finish
        server.reset()
        shutil.rmtree(OUT, ignore_errors=True)
        app.clear_log()

    def new_item(self):
        app.add_urls([server.url])
        self.assertTrue(wait(lambda: app.items and app.items[0].status == "queued", 30), "no preview arrived")
        return app.items[0]

    def until_downloaded(self, item, fraction, timeout=30):
        self.assertTrue(wait(lambda: mod.partial_size(item.dests) >= server.size * fraction, timeout),
                        f"only {mod.partial_size(item.dests):,} bytes after {timeout} s")

    def final_file(self):
        return OUT / "big.mp4"

    def test_pause_and_resume_continue_at_the_same_byte(self):
        item = self.new_item()
        app.start_item(item)
        self.assertTrue(wait(lambda: item.status == "downloading", 15))
        self.until_downloaded(item, 0.30)
        app.pause_item(item)
        self.assertTrue(wait(lambda: item.status == "paused", 15), item.status)
        kept = mod.partial_size(item.dests)
        self.assertTrue(0 < kept < server.size, f"{kept:,} of {server.size:,} bytes")
        self.assertGreater(item.pct, 10)
        gs.pump(root, 300)
        self.assertEqual(mod.partial_size(item.dests), kept, "the download kept going after 'Pause'")
        row = app.view.row_for(item)
        self.assertTrue(row.status.cget("text").startswith("Paused at") and row.primary.cget("text") == "Resume")

        app.resume_item(item)
        self.assertTrue(wait(lambda: item.status == "done", 60), item.status)
        self.assertEqual(self.final_file().stat().st_size, server.size)
        self.assertEqual(sha256(self.final_file()), SOURCE_SHA)
        starts = [r[0] for r in server.requests]
        self.assertTrue(len(starts) >= 2 and starts[-1] > 0, starts)
        self.assertLessEqual(abs(starts[-1] - kept), 65536, f"range start {starts[-1]:,}, paused at {kept:,}")
        self.assertLess(server.sent, server.size * 1.15, f"{server.sent / server.size:.2f}x of the file was transferred")
        self.assertTrue(item.resumed)
        self.assertTrue(any(h["url"] == server.url for h in app.history))

    def crash_and_tear(self, repair: bool):
        """Stop a download, append zeros to its partial file (what a power loss can leave) and continue it."""
        shutil.rmtree(OUT, ignore_errors=True)
        server.reset()
        app._drop(list(app.items))
        item = self.new_item()
        app.start_item(item)
        self.until_downloaded(item, 0.30)
        app.cancel_item(item)
        self.assertTrue(wait(lambda: item.status == "cancelled", 15), item.status)
        with open(str(item.dests[0]) + ".part", "ab") as fh:
            fh.write(b"\0" * 300_000)
        item.interrupted = repair                            # what a restored queue says after a crash
        item.retries = len(mod.AUTO_RETRY_DELAYS)            # no automatic retry in this scenario
        app.retry_item(item)
        done = wait(lambda: item.status == "done", 70)
        return item, done and sha256(self.final_file()) == SOURCE_SHA

    def test_a_torn_partial_file_is_repaired_before_continuing(self):
        _, identical = self.crash_and_tear(repair=False)
        self.assertFalse(identical, "control: without the repair the zeros end up in the finished file")
        item, identical = self.crash_and_tear(repair=True)
        self.assertEqual(item.status, "done")
        self.assertTrue(identical, "the repaired download differs from the source")
        gs.pump(root, 100)
        self.assertIn("re-fetching the last", app.log_box.get("1.0", "end"))
        self.assertGreater([r[0] for r in server.requests][-1], 0, "it should continue inside the file")

    def test_dropped_connections_are_retried_and_continued(self):
        with mock.patch.object(mod, "AUTO_RETRY_DELAYS", (1, 1, 1, 1, 1)):
            server.drop_first, server.drop_at = 11, 0.02     # more drops than yt-dlp's own three retries survive
            item = self.new_item()
            app.start_item(item)
            seen = []
            done = wait(lambda: item.status == "done" or (
                seen.append("retrying in" in (app.view.row_for(item).status.cget("text") if app.view.row_for(item) else ""))
                or False), 90)
        self.assertTrue(done, f"{item.status}: {item.error!r}")
        self.assertEqual(sha256(self.final_file()), SOURCE_SHA)
        self.assertTrue(any(seen), "the card never showed the retry countdown")
        gs.pump(root, 100)
        self.assertIn("Network problem - retrying in", app.log_box.get("1.0", "end"))
        self.assertEqual(item.retries, 0)
        self.assertGreaterEqual(sum(1 for r in server.requests if r[0] > 0), 3, [r[0] for r in server.requests])
        self.assertLess(server.sent, server.size * 1.2, f"{server.sent / server.size:.2f}x of the file was transferred")


if __name__ == "__main__":
    unittest.main()
