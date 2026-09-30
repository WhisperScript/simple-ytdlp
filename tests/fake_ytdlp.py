#!/usr/bin/env python3
"""A stand-in for yt-dlp for the GUI tests. It understands just enough options and behaves according to
keywords in the URL, so the interface can be driven quickly and deterministically without a network.

URL keywords
    bad          no information available (extractor error)
    playlist     a playlist of seven videos
    cjk emoji long portrait nothumb hours     variations of the video information
    slow         a longer download (about 5 seconds instead of 1)
    fail         "private video" error (not worth a retry)
    flakyN       the first N runs stop with a network error halfway, later runs continue from the partial file
    multilineN   like flakyN, but the error text comes on the line after "ERROR:" (as yt-dlp sometimes prints it)
    notfound     "HTTP Error 404" after an empty "ERROR:" line

Environment
    FAKE_YTDLP_DIR   folder for the call log (args.log), the flaky counters and the thumbnails (t.jpg, p.jpg)
"""
import hashlib
import json
import os
import re
import sys
import time

args = sys.argv[1:]
work = os.environ.get("FAKE_YTDLP_DIR", "")


def emit(text):
    print(text, flush=True)


if "--version" in args:
    emit("2099.01.01")
    sys.exit(0)

url = args[-1]
if work:
    with open(os.path.join(work, "args.log"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\n")


def thumb(name):
    return "file://" + os.path.join(work, name).replace("\\", "/") if work else ""


def run_info():
    if "bad" in url:
        sys.stderr.write("ERROR: Unsupported URL\n")
        sys.exit(1)
    if "--flat-playlist" not in args:                     # the format list of the options window
        emit(json.dumps({"title": "Formats test", "formats": [
            {"format_id": "137", "ext": "mp4", "width": 1920, "height": 1080, "vcodec": "avc1.640028",
             "acodec": "none", "filesize": 50000000, "fps": 60, "format_note": "1080p60", "tbr": 4000},
            {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 130,
             "filesize": 3000000, "format_note": "medium"},
            {"format_id": "18", "ext": "mp4", "width": 640, "height": 360, "vcodec": "avc1.42001E",
             "acodec": "mp4a.40.2", "format_note": "360p", "tbr": 500},
            {"format_id": "sb0", "vcodec": "none", "acodec": "none"}]}))
        return
    if "playlist" in url and "--no-playlist" not in args:
        entries = [{"title": f"Playlist video number {i} with a rather long title to test truncation",
                    "url": f"https://x.test/watch?v={i}", "duration": 60 * i + 7, "uploader": "Chan",
                    "thumbnail": thumb("t.jpg")} for i in range(1, 8)]
        emit(json.dumps({"_type": "playlist", "title": "My Test Playlist", "entries": entries}))
        return
    title, uploader, duration, image = ("Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
                                        "Rick Astley", 213, "t.jpg")
    if "cjk" in url:
        title, uploader = "日本語のとても長いタイトルのテストです。これは二行に収まるでしょうか？ 🎵 音楽 ライブ映像 完全版", "チャンネル名"
    if "emoji" in url:
        title, uploader = "🔥🔥 Best of 2024 🎧 lofi beats to relax/study to 🌙✨ [1 HOUR]", "Lofi Girl 🎀"
    if "long" in url:
        title = "An extremely long video title that keeps going and going " * 4
        uploader = "A channel with a really really long name that also does not end soon"
    if "portrait" in url:
        image, title, duration = "p.jpg", "Portrait short #shorts", 42
    if "nothumb" in url:
        image = "missing.jpg"
    if "hours" in url:
        duration = 3 * 3600 + 25 * 60 + 9
    emit(json.dumps({"title": title, "uploader": uploader, "duration": duration, "thumbnail": thumb(image)}))


def runs_so_far():
    """How many times this URL has been downloaded before (persisted, so 'flaky' links recover)."""
    if not work:
        return 0
    path = os.path.join(work, "runs-" + hashlib.md5(url.encode()).hexdigest()[:10])
    try:
        n = int(open(path).read())
    except (OSError, ValueError):
        n = 0
    with open(path, "w") as fh:
        fh.write(str(n + 1))
    return n


def run_download():
    audio = "-x" in args
    template = args[args.index("-o") + 1]
    name = re.sub(r"\W+", "_", url.split("//")[-1])[:30]
    path = (template.replace("%(title)s", name).replace("%(uploader)s", "Chan")
            .replace("%(ext)s", "mp3" if audio else "mp4"))
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if "fail" in url:
        emit("ERROR: [youtube] xyz: Video unavailable. This video is private")
        sys.exit(1)
    if "notfound" in url:
        emit("ERROR:")
        emit("[generic] Unable to download webpage: HTTP Error 404: Not Found")
        sys.exit(1)

    emit(f"[youtube] Extracting URL: {url}")
    emit(f"[download] Destination: {path}")
    total, step = 4_000_000, 100_000                      # a 4 MB "file", written in chunks
    part = path + ".part"
    pos = os.path.getsize(part) if os.path.exists(part) else 0
    if pos:
        emit(f"[download] Resuming download at byte {pos}")
    delay = 0.12 if "slow" in url else 0.03

    m = re.search(r"(?:flaky|multiline)(\d+)", url)
    break_at = total // 2 if m and runs_so_far() < int(m.group(1)) else None

    with open(part, "ab") as fh:
        while pos < total:
            chunk = min(step, total - pos)
            fh.write(b"x" * chunk)
            fh.flush()
            pos += chunk
            pct = pos * 100.0 / total
            emit(f"[download] {pct:5.1f}% of ~   3.81MiB at    {0.4 if 'slow' in url else 3.2:.2f}MiB/s "
                 f"ETA 00:{max(0, int((100 - pct) / 8)):02d} (frag {pos // step}/{total // step})")
            if break_at is not None and pos >= break_at:
                if "multiline" in url:
                    emit("ERROR:")
                    emit("[download] Got error: The read operation timed out")
                else:
                    emit("ERROR: unable to download video data: HTTP Error 503: Service Unavailable")
                sys.exit(1)
            time.sleep(delay)
    os.replace(part, path)
    emit(f'[Merger] Merging formats into "{path}"' if not audio else f"[ExtractAudio] Destination: {path}")


if "--dump-single-json" in args:
    run_info()
else:
    run_download()
