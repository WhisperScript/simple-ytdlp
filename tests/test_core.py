"""Tests for the non-GUI core of simple-ytdlp (run: python -m unittest discover -s tests)."""
import importlib.util
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


class DisplayHelpers(unittest.TestCase):
    def test_fit_text_cuts_with_an_ellipsis_at_the_right_place(self):
        measure = lambda t: len(t) * 10                       # 10 px per character
        self.assertEqual(app.fit_text(measure, "hello", 50), "hello")
        self.assertEqual(app.fit_text(measure, "hello world", 60), "hello…")
        self.assertEqual(app.fit_text(measure, "hello world", 5), "")
        self.assertEqual(app.fit_text(measure, "  spaced   out  ", 1000), "spaced out")
        self.assertEqual(app.fit_text(measure, "anything", 0), "")

    def test_fit_text_never_exceeds_the_limit(self):
        measure = lambda t: sum(14 if ord(c) > 255 else 7 for c in t)        # wide characters like CJK
        for text in ("x" * 40, "日本語のとても長いタイトル" * 3, "mixed 日本語 title"):
            for limit in range(0, 200, 9):
                self.assertLessEqual(measure(app.fit_text(measure, text, limit)), limit)

    def test_tilde_path(self):
        home = Path.home()
        self.assertEqual(app.tilde_path(home), "~")
        self.assertTrue(app.tilde_path(home / "Downloads" / "yt-dlp").startswith("~"))
        long = app.tilde_path(home / ("very-long-folder-name-" * 6) / "end", limit=40)
        self.assertLessEqual(len(long), 40)
        self.assertIn("…", long)
        self.assertTrue(long.endswith("end"))

    def test_humanize_when(self):
        import time
        now = time.mktime((2026, 9, 30, 15, 0, 0, 0, 0, -1))
        at = lambda d, h=9, m=5: time.mktime((2026, 9, d, h, m, 0, 0, 0, -1))
        self.assertEqual(app.humanize_when(at(30), now), "Today 09:05")
        self.assertEqual(app.humanize_when(at(29, 23, 59), now), "Yesterday 23:59")
        self.assertRegex(app.humanize_when(at(27), now), r"^[A-Z][a-z]{2} 09:05$")      # this week: weekday
        self.assertEqual(app.humanize_when(at(10), now), "2026-09-10")

    def test_speed(self):
        self.assertEqual(app.parse_speed("3.20MiB/s"), 3.2 * 1024 ** 2)
        self.assertEqual(app.parse_speed("512KiB/s"), 512 * 1024)
        self.assertEqual(app.parse_speed("Unknown"), 0.0)
        self.assertEqual(app.parse_speed(""), 0.0)
        self.assertEqual(app.fmt_rate(2.5 * 1024 ** 2), "2.5 MiB/s")

    def test_quality_labels(self):
        self.assertEqual(app.quality_label("video1080"), "Up to 1080p")
        self.assertEqual(app.mode_label("mp3"), "Audio · MP3")
        self.assertEqual(app.mode_label("video"), "Video · Best quality")
        self.assertEqual(app.mode_label("nonsense"), "")
        self.assertEqual(set(app.UI_QUALITY), set(app.MODES))                              # every mode has a short label
        self.assertEqual(len(set(app.UI_QUALITY.values())), len(app.UI_QUALITY))           # and they are unique


class Resume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def part(self, name, size, byte=b"x"):
        (self.dir / (name + ".part")).write_bytes(byte * size)
        return str(self.dir / name)

    def test_partial_size_counts_only_unfinished_files(self):
        a = self.part("a.f137.mp4", 1000)
        b = self.part("b.webm", 500)
        done = self.dir / "done.mp4"
        done.write_bytes(b"x" * 9999)                                  # a complete file has no .part
        self.assertEqual(app.partial_size([a, b, str(done)]), 1500)
        self.assertEqual(app.partial_size([]), 0)
        self.assertEqual(app.partial_size(None), 0)

    def test_trim_cuts_the_tail_after_an_unclean_stop(self):
        dest = self.part("clip.mp4", 3000)
        self.assertEqual(app.trim_partial_tail([dest], margin=1000), 1000)
        self.assertEqual((self.dir / "clip.mp4.part").stat().st_size, 2000)

    def test_trim_never_goes_below_zero(self):
        dest = self.part("small.mp4", 300)
        self.assertEqual(app.trim_partial_tail([dest], margin=1000), 300)
        self.assertEqual((self.dir / "small.mp4.part").stat().st_size, 0)

    def test_trim_leaves_fragment_downloads_alone(self):
        dest = self.part("stream.mp4", 3000)
        (self.dir / "stream.mp4.ytdl").write_text("{}")                # yt-dlp tracks fragments in this file
        self.assertEqual(app.trim_partial_tail([dest], margin=1000), 0)
        self.assertEqual((self.dir / "stream.mp4.part").stat().st_size, 3000)

    def test_trim_handles_missing_files(self):
        self.assertEqual(app.trim_partial_tail([str(self.dir / "nope.mp4")]), 0)

    def test_discard_removes_partial_and_state_files(self):
        dest = self.part("x.mp4", 700)
        (self.dir / "x.mp4.ytdl").write_text("12345")
        self.assertEqual(app.discard_partial([dest]), 705)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_transient_errors_get_a_retry_real_ones_do_not(self):
        yes = ["ERROR: [download] Got error: Downloaded 2621440 bytes, expected 31083202 bytes. Giving up after 3 retries",
               "urlopen error [Errno -3] Temporary failure in name resolution", "HTTP Error 503: Service Unavailable",
               "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))",
               "The read operation timed out", "IncompleteRead(1024 bytes read, 2048 more expected)"]
        no = ["Unable to download webpage: HTTP Error 404: Not Found", "Private video", "HTTP Error 403: Forbidden",
              "Sign in to confirm your age", "Unsupported URL: https://example.com", ""]
        for text in yes:
            self.assertTrue(app.is_transient_error(text), text)
        for text in no:
            self.assertFalse(app.is_transient_error(text), text)

    def test_retry_delays_grow(self):
        self.assertEqual(list(app.AUTO_RETRY_DELAYS), sorted(app.AUTO_RETRY_DELAYS))
        self.assertGreaterEqual(len(app.AUTO_RETRY_DELAYS), 3)

    def test_update_hint_and_dropping_connection_messages(self):
        self.assertTrue(app.suggests_update("ERROR: Unable to extract uploader id; please report this issue"))
        self.assertTrue(app.suggests_update("nsig extraction failed: Some formats may be missing"))
        self.assertFalse(app.suggests_update("Private video"))
        self.assertIn("update it", app.friendly_error("ERROR: Unable to extract player response"))
        self.assertIn("keeps dropping", app.friendly_error("Got error: Downloaded 2621440 bytes, expected 31083202 bytes. Giving up after 3 retries"))

    def test_resume_line_is_recognised(self):
        m = app.RESUME_RE.search("[download] Resuming download at byte 10008260")
        self.assertEqual(int(m.group("n")), 10008260)


class QueueStorageV2(unittest.TestCase):
    def test_overrides_summary_includes_paused_fields_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            app.QUEUE_FILE = Path(tmp) / "queue.json"
            entry = {"url": "https://a.b", "status": "paused", "pct": 32.0, "dests": ["/o/x.mp4"], "interrupted": True}
            app.save_queue([entry])
            self.assertEqual(app.load_queue()[0], entry)


if __name__ == "__main__":
    unittest.main()


class ReleaseNotes(unittest.TestCase):
    def test_markdown_becomes_plain_text_and_the_generated_part_is_cut(self):
        body = "## simple-ytdlp 2.5\n\n- **Drag and drop:** drop a `link`\n- More\n\n\n## What's Changed\n* PR by @x"
        self.assertEqual(app.release_notes_text(body), "simple-ytdlp 2.5\n\n\u2022 Drag and drop: drop a link\n\u2022 More")


class DropsAndLive(unittest.TestCase):
    def test_links_in_dropped_text(self):
        self.assertEqual(app.links_from_drop("look https://a.b/c and\nhttps://d.e/f", str.split), ["https://a.b/c", "https://d.e/f"])

    def test_links_in_dropped_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            lst, shortcut = Path(tmp) / "list.txt", Path(tmp) / "x.url"
            lst.write_text("# videos\nhttps://a.b/1\nhttps://a.b/2\n")
            shortcut.write_text("[InternetShortcut]\nURL=https://c.d/3\n")
            self.assertEqual(app.links_from_drop(f"{lst} {shortcut}", str.split),
                             ["https://a.b/1", "https://a.b/2", "https://c.d/3"])
            self.assertEqual(app.links_from_drop(str(Path(tmp) / "missing.txt"), str.split), [])

    def test_live_streams_are_recognised(self):
        self.assertTrue(app.parse_info({"title": "t", "is_live": True})["live"])
        self.assertTrue(app.parse_info({"title": "t", "live_status": "is_upcoming"})["live"])
        self.assertFalse(app.parse_info({"title": "t", "live_status": "not_live"})["live"])


class TimeAndPower(unittest.TestCase):
    def test_clock_times(self):
        now = time.mktime((2026, 9, 30, 12, 0, 0, 0, 0, -1))
        self.assertEqual(time.localtime(app.parse_clock_time("14:30", now))[2:5], (30, 14, 30))
        later = app.parse_clock_time("02:30", now)
        self.assertEqual(time.localtime(later)[2:5], (1, 2, 30), "already past today: tomorrow")
        self.assertEqual(app.parse_clock_time(" 9.05 ", now), app.parse_clock_time("9:05", now))
        for bad in ("", "25:00", "12:60", "noon", "1230"):
            self.assertIsNone(app.parse_clock_time(bad, now))

    def test_power_commands(self):
        self.assertIsNone(app.power_command("hibernate"))
        self.assertTrue(app.power_command("sleep") and app.power_command("shutdown"))

    def test_free_space_of_a_folder_that_does_not_exist_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertGreater(app.free_space(Path(tmp) / "a" / "b"), 0)

    def test_full_disk_error_is_explained(self):
        self.assertIn("disk is full", app.friendly_error("OSError: [Errno 28] No space left on device"))


class CompatAndCookies(unittest.TestCase):
    def test_compat_prefers_mp4_h264_without_lowering_the_resolution(self):
        a = app.build_args("video1080", Path("/o"), compat=True)
        self.assertEqual(a[a.index("-S") + 1], "res,vcodec:h264,acodec:m4a")
        self.assertEqual(a[a.index("--merge-output-format") + 1], "mp4")
        self.assertEqual(a[a.index("-f") + 1], "bv*[height<=1080]+ba/b[height<=1080]/b")

    def test_compat_leaves_audio_and_chosen_formats_alone(self):
        self.assertNotIn("-S", app.build_args("mp3", Path("/o"), compat=True))
        self.assertNotIn("-S", app.build_args("video", Path("/o"), compat=True, format_override="137+251"))
        self.assertNotIn("-S", app.build_args("video", Path("/o")))

    def test_cookie_sources(self):
        self.assertEqual(app.cookie_args(""), [])
        self.assertEqual(app.cookie_args("firefox"), ["--cookies-from-browser", "firefox"])
        self.assertEqual(app.cookie_args("file:/c/cookies.txt"), ["--cookies", "/c/cookies.txt"])
        a = app.build_args("video", Path("/o"), cookies_browser="file:/c/cookies.txt")
        self.assertIn("--cookies", a)
        self.assertNotIn("--cookies-from-browser", a)

    def test_unreadable_browser_cookies_are_explained(self):
        for err in ("ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271",
                    "ERROR: Failed to decrypt with DPAPI"):
            self.assertIn("cookies.txt", app.friendly_error(err))


class FfmpegTools(unittest.TestCase):
    def test_ffprobe_errors_are_recognised(self):
        err = "ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path using --ffmpeg-location"
        self.assertTrue(app.needs_ffprobe(err))
        self.assertIn("ffprobe is missing", app.friendly_error(err))
        self.assertEqual(app.friendly_error("ffmpeg not found"), "ffmpeg is missing")
        self.assertFalse(app.needs_ffprobe("ffmpeg not found"))

    def test_managed_ffmpeg_is_used_with_its_folder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(app, "BIN_DIR", Path(tmp)), \
                mock.patch.object(app.shutil, "which", lambda name: None):
            (Path(tmp) / app._exe("ffmpeg")).write_bytes(b"x")
            self.assertEqual(app.find_ffmpeg(), (str(Path(tmp) / app._exe("ffmpeg")), "managed"))


class SameVideo(unittest.TestCase):
    def test_youtube_links_to_one_video_share_a_key(self):
        same = ["https://www.youtube.com/watch?v=abc123XYZ_-", "https://youtu.be/abc123XYZ_-?si=tracking",
                "https://m.youtube.com/watch?v=abc123XYZ_-&t=90s&list=PL1", "https://youtube.com/shorts/abc123XYZ_-",
                "https://www.youtube.com/embed/abc123XYZ_-"]
        self.assertEqual({app.url_key(u) for u in same}, {"youtube:abc123XYZ_-"})

    def test_different_videos_and_playlists_differ(self):
        self.assertNotEqual(app.url_key("https://youtu.be/aaa"), app.url_key("https://youtu.be/bbb"))
        self.assertEqual(app.url_key("https://www.youtube.com/playlist?list=PL9"), "youtube-list:PL9")

    def test_other_sites_ignore_noise_but_not_content(self):
        self.assertEqual(app.url_key("https://www.example.com/v/1/?utm_source=x#top"), app.url_key("https://example.com/v/1"))
        self.assertNotEqual(app.url_key("https://example.com/watch?id=1"), app.url_key("https://example.com/watch?id=2"))


class QueueSummary(unittest.TestCase):
    COUNTS = {"downloading": 2, "paused": 1, "queued": 4, "fetching": 1, "done": 3, "skipped": 1, "failed": 2}

    def test_most_detailed_text_comes_first_and_shrinks_step_by_step(self):
        options = app.summary_options(self.COUNTS, 11_744_051)
        self.assertEqual(options[0], "2 downloading · 11.2 MiB/s  ·  1 paused  ·  5 waiting  ·  4 done  ·  2 failed")
        self.assertEqual(options[1], "2 downloading  ·  1 paused  ·  5 waiting  ·  4 done  ·  2 failed")
        self.assertEqual(options[2], "2 active · 1 paused · 5 queued · 4 done · 2 failed")
        self.assertEqual(options[3], "2 active · 1 paused · 5 queued · 2 failed")
        self.assertEqual([len(o) for o in options], sorted((len(o) for o in options), reverse=True))

    def test_failures_survive_every_step(self):
        self.assertTrue(all(o.endswith("2 failed") for o in app.summary_options(self.COUNTS, 1)))

    def test_no_repeats_and_empty_queue(self):
        self.assertEqual(app.summary_options({"failed": 1}), ["1 failed"])
        self.assertEqual(app.summary_options({}), [""])
        self.assertEqual(len(set(app.summary_options({"done": 2}))), len(app.summary_options({"done": 2})))


class LegacyData(unittest.TestCase):
    """The app used to be called ytdl; its settings, history and tools move to the new folder once."""

    def run_migration(self, prepare):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        prepare(base)
        with mock.patch.object(app, "data_dir", lambda: base / "simple-ytdlp"):
            app.migrate_legacy_data()
        return base

    def test_old_folder_is_moved(self):
        def prepare(base):
            (base / "ytdl").mkdir()
            (base / "ytdl" / "settings.json").write_text('{"mode": "mp3"}')
        base = self.run_migration(prepare)
        self.assertEqual(json.loads((base / "simple-ytdlp" / "settings.json").read_text()), {"mode": "mp3"})
        self.assertFalse((base / "ytdl").exists())

    def test_existing_new_folder_wins(self):
        def prepare(base):
            (base / "ytdl").mkdir()
            (base / "ytdl" / "settings.json").write_text("old")
            (base / "simple-ytdlp").mkdir()
            (base / "simple-ytdlp" / "settings.json").write_text("new")
        base = self.run_migration(prepare)
        self.assertEqual((base / "simple-ytdlp" / "settings.json").read_text(), "new")
        self.assertTrue((base / "ytdl" / "settings.json").exists())

    def test_nothing_to_move(self):
        base = self.run_migration(lambda base: None)
        self.assertFalse((base / "simple-ytdlp").exists())
