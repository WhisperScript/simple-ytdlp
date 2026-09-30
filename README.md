# ytdl — yt-dlp frontend (macOS / Linux / Windows)

## Ready-made programs

Under **Releases** there is one program per system — nothing to install, just unpack and start:

| System | File | Start |
|---|---|---|
| Windows | `ytdl-windows.zip` | Double-click `ytdl.exe`. On the SmartScreen warning: "More info → Run anyway". |
| macOS (Apple Silicon) | `ytdl-macos.zip` | Unpack, then **right-click ytdl.app → Open**. If it is blocked: `xattr -dr com.apple.quarantine ytdl.app` |
| macOS (Intel) | `ytdl-macos-intel.zip` | Same as above. |
| Linux | `ytdl-linux.tar.gz` | `tar xzf ytdl-linux.tar.gz && ./ytdl` |

On first start the program downloads yt-dlp and deno automatically (internet required,
takes a few seconds). ffmpeg is built in. The **"Update yt-dlp"** button keeps it current —
this helps when YouTube changes something and downloads suddenly fail. Once a day the
program also checks silently for a yt-dlp update at startup and installs it.

The interface follows the system theme (light/dark) and can be switched with a button.
A URL in the clipboard is offered in the link field when you switch back to the window, and you
get a notification when a run finishes.

### The interface

- **Paste and go:** press Ctrl+V / Cmd+V (in the link field or anywhere in the window) and every
  link in the clipboard lands in the queue. Text files with one link per line can be imported.
- **Queue with previews:** each download is a card with thumbnail, title, channel, duration, live
  progress, speed and ETA. Cancel, retry, remove or show the file per card; up to 4 downloads
  run in parallel (Options → Parallel).
- **Playlists:** a playlist link opens a selection list first, so you only add the videos you want.
- **Video or audio:** pick the kind, then the quality (best, 4K, 1440p, 1080p, 720p, 480p, or
  mp3 / m4a / opus).
- **History:** every finished download is kept; open the file, show it in the folder or
  download it again.
- **Options:** subtitles, embedded thumbnail, skip already downloaded, folder per channel,
  SponsorBlock (cuts sponsor segments), speed limit, cookies from a browser, start automatically.

Settings and the downloaded tools live in the user data folder (Windows `%LOCALAPPDATA%\ytdl`,
macOS `~/Library/Application Support/ytdl`, Linux `~/.local/share/ytdl`).

The programs are not code-signed, hence the warnings on first start.

### Updates

- **yt-dlp** updates itself (daily check at startup, or the "Update yt-dlp" button).
- **The app itself** shows a banner ("A new version of ytdl is available") when a newer
  release exists on GitHub. **"Update now"** downloads it (checksum-verified), replaces the
  running program and restarts it; your settings are kept. "Release page" opens the download
  page instead. The check is silent if you are offline, and it needs a public repo.
  - Windows/Linux: the program file is replaced in place. It must be in a folder you can
    write to (Downloads, Desktop, … but not `C:\Program Files`).
  - macOS: `ytdl.app` is replaced; move it out of the Downloads folder first (e.g. to
    Applications), otherwise macOS runs it from a read-only location and the automatic update
    falls back to the manual download.
  - If anything goes wrong the old version stays in place and you get the release page link.

### Publishing an update (for the maintainer)

Commit and push your changes, then tag a new version. The workflow writes the tag into the
app as its version number (`v1.1` → `1.1`), builds all three systems and creates the release:

    git tag v1.1 && git push origin v1.1

Use a higher number every time (`v1.1`, `v1.2`, `v1.10`, …) — that is how running apps
recognize that something newer exists.

### Building it yourself / publishing a release

The workflow [.github/workflows/build.yml](.github/workflows/build.yml) builds all three
systems automatically. Push the repo to GitHub, then:

    git tag v1.0 && git push --tags

After a few minutes everything is available under **Releases**. It can also be started
manually via **Actions → Build → Run workflow** (result is a downloadable artifact, nothing is
published). Locally, e.g. on Windows:

    pip install pyinstaller imageio-ffmpeg certifi sv-ttk darkdetect pillow
    pyinstaller --onefile --windowed --name ytdl --collect-all imageio_ffmpeg --collect-all sv_ttk ytdl.py

## As a script

A single script. With [uv](https://docs.astral.sh/uv/) the target machine needs
**nothing** except uv itself — Python, the packages and ffmpeg are fetched on first start
and stored in a cache, not in the system. yt-dlp and deno are downloaded into the
user data folder.

## Setting up a new machine

1. Install uv:

   macOS / Linux:

       curl -LsSf https://astral.sh/uv/install.sh | sh

   Windows (PowerShell):

       powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

   Then open a new terminal so `uv` is on the PATH.

2. Copy `ytdl.py` over.
3. Start it:

       uv run ytdl.py

That's it. Without arguments the graphical interface opens. On macOS/Linux, after
`chmod +x ytdl.py` you can also start it directly with `./ytdl.py` — the shebang line calls uv.

## Usage

Interface: paste URLs one per line, choose a mode, press Start. It runs in the background,
can be cancelled at any time, and shows a log and progress bar in the window.

Command line:

    uv run ytdl.py "https://youtube.com/watch?v=XXXX"
    uv run ytdl.py -a urls.txt -m mp3 -o ~/Music --archive
    uv run ytdl.py URL --mode video1080 --subs --thumb --by-uploader

Modes: `video` (best quality), `video2160`, `video1440`, `video1080`, `video720`, `video480`,
`mp3`, `m4a`, `opus`.

Video **and** audio in one go: with a video mode, `--also-audio mp3` (or `m4a` / `opus`)
keeps the video and additionally saves a separate audio file with the same name. In the
interface this is the "Also save audio as" dropdown.

    uv run ytdl.py URL -m video1080 --also-audio mp3

Unknown flags are passed on to yt-dlp unchanged:

    uv run ytdl.py URL --playlist-items 1-5
    uv run ytdl.py URL --simulate          # only check, download nothing

The default output folder is `~/Downloads/yt-dlp`. Failed URLs end up there in
`failed.log`; with `--archive`, `archive.txt` remembers videos that were already downloaded,
so running the same list again only fetches what is new (handy for cron).

For private or age-restricted videos use `--cookies-from-browser chrome`, or the browser
dropdown in the window.

## Without uv

Works with an existing Python: `pip install imageio-ffmpeg certifi sv-ttk darkdetect pillow`, then
`python3 ytdl.py`. The script downloads yt-dlp and deno itself on first start
(`--update` updates yt-dlp). An already installed ffmpeg is preferred, otherwise the bundled
one is used. On Linux you may need `sudo apt install python3-tk` for the interface.

## What happens automatically

- **ffmpeg**: A system ffmpeg is preferred; if there is none, a bundled binary
  (`imageio-ffmpeg`) is used. It lacks ffprobe — standard cases such as mp3 extraction and
  video merging work fine (tested).
- **JS engine**: YouTube now needs one, otherwise high-resolution formats are missing.
  The script downloads deno on first start; an existing deno/node/bun is used otherwise.
- **Playlists**: In the interface "Single video only" is on by default, so a video URL with
  `&list=…` does not pull in the whole playlist. Turn it off for real playlists.
- **Windows**: no flashing console windows from subprocesses.
