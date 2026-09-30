"""Tests for the non-GUI core of simple-ytdlp (run: python -m unittest discover -s tests)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("simple_ytdlp", ROOT / "simple-ytdlp.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class Progress(unittest.TestCase):
    def test_regular_line(self):
        p = app.parse_progress("[download]  45.3% of   10.00MiB at    2.50MiB/s ETA 00:02")
        self.assertEqual(p, {"pct": 45.3, "size": "10.00MiB", "speed": "2.50MiB/s", "eta": "00:02"})

    def test_estimated_size_and_unknown(self):
        p = app.parse_progress("[download]   0.0% of ~  42.10MiB at  Unknown B/s ETA Unknown")
        self.assertEqual((p["pct"], p["size"], p["speed"]), (0.0, "42.10MiB", "Unknown"))

    def test_final_line(self):
        p = app.parse_progress("[download] 100% of   10.00MiB in 00:00:04 at 2.40MiB/s")
        self.assertEqual((p["pct"], p["speed"], p["eta"]), (100.0, "2.40MiB/s", ""))

    def test_other_lines(self):
        self.assertIsNone(app.parse_progress("[download] Destination: x.mp4"))
        self.assertIsNone(app.parse_progress("[info] something"))


class OutputFile(unittest.TestCase):
    def run_lines(self, lines, audio):
        found = {}
        for line in lines:
            app.track_output_file(found, line, audio)
        return app.output_file(found)

    def test_merged_video_wins_over_streams_and_audio_copy(self):
        lines = ["[download] Destination: /o/T.f137.mp4", "[download] Destination: /o/T.f251.webm",
                 '[Merger] Merging formats into "/o/T.mkv"', "[ExtractAudio] Destination: /o/T.mp3"]
        self.assertEqual(self.run_lines(lines, audio=False), "/o/T.mkv")

    def test_audio_mode_takes_extracted_file(self):
        lines = ["[download] Destination: /o/T.webm", "[ExtractAudio] Destination: /o/T.mp3"]
        self.assertEqual(self.run_lines(lines, audio=True), "/o/T.mp3")

    def test_already_downloaded(self):
        self.assertEqual(self.run_lines(["[download] /o/T.mp4 has already been downloaded"], False), "/o/T.mp4")


class Helpers(unittest.TestCase):
    def test_extract_urls(self):
        text = "a https://a.b/c?x=1, and (https://d.e/f). https://a.b/c?x=1"
        self.assertEqual(app.extract_urls(text), ["https://a.b/c?x=1", "https://d.e/f"])

    def test_durations_and_sizes(self):
        self.assertEqual(app.fmt_duration(3725), "1:02:05")
        self.assertEqual(app.fmt_duration(65), "1:05")
        self.assertEqual(app.fmt_duration(None), "")
        self.assertEqual(app.fmt_size(1536), "1.5 KiB")

    def test_looks_like_url(self):
        self.assertTrue(app.looks_like_url("https://example.com/x"))
        self.assertFalse(app.looks_like_url("hello world"))
        self.assertFalse(app.looks_like_url("https://a.b\nhttps://c.d"))


class TimeRange(unittest.TestCase):
    def test_parse_clock(self):
        self.assertEqual(app.parse_clock("90"), 90)
        self.assertEqual(app.parse_clock("1:30"), 90)
        self.assertEqual(app.parse_clock("1:02:03"), 3723)
        self.assertEqual(app.parse_clock("0:01.5"), 1.5)
        self.assertIsNone(app.parse_clock("abc"))
        self.assertIsNone(app.parse_clock("1:2:3:4"))

    def test_section_arg(self):
        self.assertEqual(app.section_arg("0:10", "0:30"), ("*0:00:10-0:00:30", ""))
        self.assertEqual(app.section_arg("", "30"), ("*0:00:00-0:00:30", ""))
        self.assertEqual(app.section_arg("1:00", ""), ("*0:01:00-inf", ""))
        self.assertEqual(app.section_arg("", ""), ("", ""))

    def test_section_errors(self):
        self.assertTrue(app.section_arg("x", "5")[1])
        self.assertTrue(app.section_arg("30", "10")[1])


class BuildArgs(unittest.TestCase):
    out = Path("/o")

    def args(self, mode="video", **kw):
        return app.build_args(mode, self.out, **kw)

    def value(self, args, flag):
        return args[args.index(flag) + 1]

    def test_quality_modes(self):
        self.assertEqual(self.value(self.args("video"), "-f"), "bv*+ba/b")
        self.assertEqual(self.value(self.args("video1440"), "-f"), "bv*[height<=1440]+ba/b[height<=1440]/b")
        self.assertIn("-x", self.args("mp3"))
        self.assertEqual(self.value(self.args("opus"), "--audio-format"), "opus")

    def test_format_override_beats_mode_but_audio_still_extracts(self):
        a = self.args("mp3", format_override="251")
        self.assertEqual(self.value(a, "-f"), "251")
        self.assertIn("--audio-format", a)

    def test_also_audio_only_for_video(self):
        self.assertIn("-k", self.args("video", also_audio="mp3"))
        self.assertNotIn("-k", self.args("mp3", also_audio="mp3"))

    def test_subtitles_skip_audio_and_validate_languages(self):
        self.assertEqual(self.value(self.args(subs=True, sub_langs="en,fr"), "--sub-langs"), "en,fr")
        self.assertEqual(self.value(self.args(subs=True, sub_langs="en; rm -rf"), "--sub-langs"), "en,de")
        self.assertNotIn("--write-subs", self.args("mp3", subs=True))

    def test_name_template(self):
        self.assertTrue(self.value(self.args(), "-o").endswith("%(title)s.%(ext)s"))
        self.assertTrue(self.value(self.args(name_template="a - %(title)s"), "-o").endswith("a - %(title)s.%(ext)s"))
        self.assertIn("%(uploader)s/", self.value(self.args(sort_by_uploader=True), "-o"))
        self.assertTrue(self.value(self.args(name_template="x.%(ext)s"), "-o").endswith("x.%(ext)s"))

    def test_cut_chapters_and_extras(self):
        a = self.args(section="*0:00:10-0:00:30", exact_cut=True, embed_chapters=True, split_chapters=True,
                      proxy="http://p:1", limit_rate="2M", sponsorblock=True, extra=["--foo", "bar"])
        self.assertEqual(self.value(a, "--download-sections"), "*0:00:10-0:00:30")
        for flag in ("--force-keyframes-at-cuts", "--embed-chapters", "--split-chapters", "--sponsorblock-remove"):
            self.assertIn(flag, a)
        self.assertEqual(self.value(a, "--proxy"), "http://p:1")
        self.assertEqual(a[-2:], ["--foo", "bar"])

    def test_defaults_add_nothing_special(self):
        a = self.args()
        for flag in ("--download-sections", "--proxy", "--limit-rate", "--embed-chapters", "--write-subs"):
            self.assertNotIn(flag, a)


class Errors(unittest.TestCase):
    def test_known_errors(self):
        self.assertIn("Age-restricted", app.friendly_error("ERROR: Sign in to confirm your age"))
        self.assertIn("Private video", app.friendly_error("This video is private"))
        self.assertIn("429", app.friendly_error("HTTP Error 429: Too Many Requests"))
        self.assertIn("Video unavailable", app.friendly_error("Video unavailable. This video has been removed"))

    def test_unknown_error_is_unchanged(self):
        self.assertEqual(app.friendly_error("something odd"), "something odd")


class InfoParsing(unittest.TestCase):
    def test_playlist_entries(self):
        info = app.parse_info({"_type": "playlist", "title": "P", "entries": [
            {"id": "abc", "ie_key": "Youtube", "url": "https://www.youtube.com/watch?v=abc", "title": "T", "duration": 5},
            None,
            {"id": "q", "ie_key": "Youtube", "title": "N"},           # url built from the id
            {"title": "no url at all"}]})
        self.assertTrue(info["is_playlist"])
        self.assertEqual([e["url"] for e in info["entries"]],
                         ["https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=q"])
        self.assertTrue(info["entries"][0]["thumbnail"].endswith("/abc/mqdefault.jpg"))

    def test_single_video(self):
        info = app.parse_info({"title": "V", "uploader": "U", "duration": 12,
                               "thumbnails": [{"url": "a"}, {"url": "b"}]})
        self.assertEqual((info["title"], info["uploader"], info["thumbnail"], info["is_playlist"]), ("V", "U", "b", False))

    def test_formats_and_expression(self):
        rows = app.parse_formats({"formats": [
            {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 130},
            {"format_id": "137", "ext": "mp4", "width": 1920, "height": 1080, "vcodec": "avc1.64", "acodec": "none"},
            {"format_id": "18", "ext": "mp4", "width": 640, "height": 360, "vcodec": "avc1", "acodec": "mp4a"},
            {"format_id": "sb0", "vcodec": "none", "acodec": "none"}]})
        self.assertEqual([r["id"] for r in rows], ["137", "18", "251"])
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(app.format_expression([by_id["251"], by_id["137"]]), "137+251")
        self.assertEqual(app.format_expression([by_id["137"]]), "137+ba/137")
        self.assertEqual(app.format_expression([by_id["18"]]), "18")
        self.assertEqual(app.format_expression([]), "")

    def test_overrides_summary(self):
        text = app.overrides_summary({"mode": "video720", "section": "*0:00:10-inf", "extra": "--x"})
        self.assertIn("max. 720p", text)
        self.assertIn("cut 0:00:10-end", text)
        self.assertIn("custom arguments", text)
        self.assertEqual(app.overrides_summary({}), "")


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_queue_roundtrip_and_empty_removes_file(self):
        app.QUEUE_FILE = self.dir / "queue.json"
        app.save_queue([{"url": "https://a.b", "status": "queued", "overrides": {"mode": "mp3"}}])
        self.assertEqual(app.load_queue()[0]["overrides"], {"mode": "mp3"})
        app.save_queue([])
        self.assertEqual(app.load_queue(), [])
        self.assertFalse(app.QUEUE_FILE.exists())

    def test_broken_files_do_not_crash(self):
        app.QUEUE_FILE = self.dir / "queue.json"
        app.QUEUE_FILE.write_text("{not json")
        self.assertEqual(app.load_queue(), [])
        app.HISTORY_FILE = self.dir / "history.json"
        app.HISTORY_FILE.write_text('"a string"')
        self.assertEqual(app.load_history(), [])

    def test_history_is_capped(self):
        app.HISTORY_FILE = self.dir / "history.json"
        app.save_history([{"title": str(i)} for i in range(app.HISTORY_MAX + 20)])
        saved = json.loads(app.HISTORY_FILE.read_text())
        self.assertEqual(len(saved), app.HISTORY_MAX)
        self.assertEqual(saved[-1]["title"], str(app.HISTORY_MAX + 19))

    def test_legacy_data_folder_is_moved(self):
        new = self.dir / app.APP_NAME
        old = self.dir / app.LEGACY_APP_NAME
        old.mkdir()
        (old / "settings.json").write_text('{"mode": "mp3"}')
        app.data_dir = lambda: new
        app.migrate_legacy_data()
        self.assertTrue((new / "settings.json").exists())
        self.assertFalse(old.exists())
        app.migrate_legacy_data()              # a second run changes nothing


if __name__ == "__main__":
    unittest.main()
