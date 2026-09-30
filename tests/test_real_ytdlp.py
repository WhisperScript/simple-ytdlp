"""Tests with the real yt-dlp and ffmpeg against a local HTTP server - no internet, no window needed.

They cover what the download command line promises: "video and audio in one go" and exact cuts. Skipped unless
yt-dlp (pip install yt-dlp) and an ffmpeg (system or pip install imageio-ffmpeg) are available.
"""
import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gui_support as gs  # noqa: E402
from range_server import RangeServer  # noqa: E402

mod = None
FFMPEG = ""
WORK = None
_patches: list = []


def setUpModule():
    global mod, FFMPEG, WORK
    if importlib.util.find_spec("yt_dlp") is None:
        raise unittest.SkipTest("yt-dlp is not installed (pip install yt-dlp)")
    mod, _ = gs.load_app()
    FFMPEG = mod.find_ffmpeg()[0] or ""
    if not FFMPEG:
        raise unittest.SkipTest("no ffmpeg available (install it or pip install imageio-ffmpeg)")
    WORK = gs.scratch_dir("real-")
    _patches.extend([
        mock.patch.object(mod, "find_ytdlp", lambda: [sys.executable, "-m", "yt_dlp"]),
        mock.patch.object(mod, "ensure_tools", lambda log=print: True),
        mock.patch.object(mod, "find_js_runtime", lambda: []),
    ])
    for p in _patches:
        p.start()


def tearDownModule():
    for p in _patches:
        p.stop()


def make_video(path: Path, seconds: int) -> None:
    """A small real video with sound (built-in encoders only, so every ffmpeg can do it)."""
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds),
                    "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "64k", "-shortest", str(path)],
                   check=True, timeout=120)


def probe(path: Path) -> str:
    """ffmpeg's description of a file (it prints to stderr)."""
    return subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          timeout=60).stderr


def duration(path: Path) -> float:
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe(path))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


class RealYtdlp(unittest.TestCase):
    def serve(self, name: str, seconds: int):
        source = WORK / name
        make_video(source, seconds)
        server = RangeServer(source)
        self.addCleanup(server.close)
        return server.url

    def test_video_and_audio_in_one_go(self):
        out = WORK / "also-audio"
        url = self.serve("clip.mp4", 3)
        args = mod.build_args("video", out, archive=False, also_audio="mp3")
        self.assertEqual(mod.download([url], args, out, log=lambda m: None, clean_intermediates=True), (1, 0))
        files = sorted(p.name for p in out.iterdir())
        self.assertEqual(sorted(Path(f).suffix for f in files), [".mp3", ".mp4"], files)
        self.assertFalse([f for f in files if mod.INTERMEDIATE_RE.search(f)], "raw stream files were left behind")
        audio = probe(out / [f for f in files if f.endswith(".mp3")][0])
        self.assertIn("Audio: mp3", audio)
        self.assertNotIn("Video:", audio.replace("Video: png", ""))
        self.assertIn("Video: mpeg4", probe(out / [f for f in files if f.endswith(".mp4")][0]))

    def test_an_exact_cut_with_a_file_name_template(self):
        out = WORK / "cut"
        url = self.serve("long.mp4", 6)
        section, error = mod.section_arg("0:01", "0:03")
        self.assertEqual(error, "")
        args = mod.build_args("video", out, archive=False, section=section, exact_cut=True,
                              name_template="clip - %(title)s")
        self.assertEqual(mod.download([url], args, out, log=lambda m: None), (1, 0))
        files = [p for p in out.iterdir() if p.suffix == ".mp4"]
        self.assertEqual(len(files), 1, sorted(p.name for p in out.iterdir()))
        self.assertEqual(files[0].name, "clip - long.mp4")             # the template, filled in
        self.assertTrue(1.5 <= duration(files[0]) <= 2.6, duration(files[0]))


if __name__ == "__main__":
    unittest.main()
