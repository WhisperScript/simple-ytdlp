# simple-ytdlp — yt-dlp frontend (macOS / Linux / Windows)

> **Renamed:** this project used to be called `ytdl`. Settings, history and downloaded tools are
> moved over automatically on the first start. Versions up to 2.0 cannot update themselves to the
> new name (the download location changed) - download the new version once from Releases.

## Ready-made programs

Under **Releases** there is one program per system — nothing to install, just unpack and start:

| System | File | Start |
|---|---|---|
| Windows | `simple-ytdlp-windows.zip` | Double-click `simple-ytdlp.exe`. On the SmartScreen warning: "More info → Run anyway". |
| macOS (Apple Silicon) | `simple-ytdlp-macos.zip` | Unpack, then **right-click simple-ytdlp.app → Open**. If it is blocked: `xattr -dr com.apple.quarantine simple-ytdlp.app` |
| macOS (Intel) | `simple-ytdlp-macos-intel.zip` | Same as above. |
| Linux | `simple-ytdlp-linux.tar.gz` | `tar xzf simple-ytdlp-linux.tar.gz && ./simple-ytdlp` |

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
  run in parallel (Settings → Parallel downloads).
- **Playlists:** a playlist link opens a selection list first, so you only add the videos you want.
- **Video or audio:** pick the kind, then the quality (best, 4K, 1440p, 1080p, 720p, 480p, or
  mp3 / m4a / opus).
- **History:** every finished download is kept; open the file, show it in the folder or
  download it again.
- **Per item:** right-click a card (or double-click it, or the **Options** button) to change just that
  download: video/audio quality, an exact format picked from the real format list (video + audio
  rows are combined), only a part of the video (start/end, optionally an exact re-encoded cut),
  chapters (embed markers, one file per chapter) and extra yt-dlp arguments. "Apply to all
  waiting" copies the choices to the rest of the queue. The menu also copies the link, opens it
  in the browser and shows finished files.
- **Settings (whole queue):** the *Settings …* button (Cmd/Ctrl+comma) opens a window sorted by topic: subtitles with your own languages, embedded thumbnail, skip already
  downloaded, folder per channel, file name template, SponsorBlock, chapters, speed limit, proxy,
  cookies from a browser, extra yt-dlp arguments, parallel downloads, start automatically.
- **Profiles:** save the current settings under a name ("Music", "Archive", …) and load them
  with one click.
- **Clipboard watcher:** optionally adds every link you copy, in any program.
- **The queue survives a restart:** waiting and failed items are restored next time.
- **Clear error messages:** common problems (age restriction, private video, HTTP 429, no
  network, ...) are explained on the card and say what to do; the log keeps the raw output.
- **Menu bar:** native on every system (top of the screen on macOS), with keyboard shortcuts.

Settings and the downloaded tools live in the user data folder (Windows `%LOCALAPPDATA%\simple-ytdlp`,
macOS `~/Library/Application Support/simple-ytdlp`, Linux `~/.local/share/simple-ytdlp`).

The programs are not code-signed, hence the warnings on first start.

### Updates

- **yt-dlp** updates itself (daily check at startup, or the "Update yt-dlp" button).
- **The app itself** shows a banner ("A new version of simple-ytdlp is available") when a newer
  release exists on GitHub. **"Update now"** downloads it (checksum-verified), replaces the
  running program and restarts it; your settings are kept. "Release page" opens the download
  page instead. The check is silent if you are offline, and it needs a public repo.
  - Windows/Linux: the program file is replaced in place. It must be in a folder you can
    write to (Downloads, Desktop, … but not `C:\Program Files`).
  - macOS: `simple-ytdlp.app` is replaced; move it out of the Downloads folder first (e.g. to
    Applications), otherwise macOS runs it from a read-only location and the automatic update
    falls back to the manual download.
  - If anything goes wrong the old version stays in place and you get the release page link.

### Publishing an update (for the maintainer)

Commit and push your changes, then tag a new version. The workflow writes the tag into the
app as its version number (`v1.1` → `1.1`), builds all three systems and creates the release:

    git tag v1.1 && git push origin v1.1

Use a higher number every time (`v1.1`, `v1.2`, `v1.10`, …) — that is how running apps
recognize that something newer exists.

Without a local checkout: **Actions → Build → Run workflow**, enter the tag (e.g. `v2.0`) and
optional release notes. The workflow then builds everything and publishes the release itself,
creating the tag on the selected branch. Leave the tag empty to only build artifacts.

### Building it yourself / publishing a release

The workflow [.github/workflows/build.yml](.github/workflows/build.yml) builds all three
systems automatically. Push the repo to GitHub, then:

    git tag v1.0 && git push --tags

After a few minutes everything is available under **Releases**. It can also be started
manually via **Actions → Build → Run workflow** (result is a downloadable artifact, nothing is
published). Locally, e.g. on Windows:

    pip install pyinstaller imageio-ffmpeg certifi sv-ttk darkdetect pillow
    pyinstaller --onefile --windowed --name simple-ytdlp --collect-all imageio_ffmpeg --collect-all sv_ttk simple-ytdlp.py

## Development

    python -m pip install pyflakes
    python -m pyflakes simple-ytdlp.py
    python -m unittest discover -s tests -v

The same checks run on every pull request (`.github/workflows/ci.yml`).

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

2. Copy `simple-ytdlp.py` over.
3. Start it:

       uv run simple-ytdlp.py

That's it. Without arguments the graphical interface opens. On macOS/Linux, after
`chmod +x simple-ytdlp.py` you can also start it directly with `./simple-ytdlp.py` — the shebang line calls uv.

## Usage

Interface: paste URLs one per line, choose a mode, press Start. It runs in the background,
can be cancelled at any time, and shows a log and progress bar in the window.

Command line:

    uv run simple-ytdlp.py "https://youtube.com/watch?v=XXXX"
    uv run simple-ytdlp.py -a urls.txt -m mp3 -o ~/Music --archive
    uv run simple-ytdlp.py URL --mode video1080 --subs --thumb --by-uploader

Modes: `video` (best quality), `video2160`, `video1440`, `video1080`, `video720`, `video480`,
`mp3`, `m4a`, `opus`.

Video **and** audio in one go: with a video mode, `--also-audio mp3` (or `m4a` / `opus`)
keeps the video and additionally saves a separate audio file with the same name. In the
interface this is the "Also save audio as" dropdown.

    uv run simple-ytdlp.py URL -m video1080 --also-audio mp3

Unknown flags are passed on to yt-dlp unchanged:

    uv run simple-ytdlp.py URL --playlist-items 1-5
    uv run simple-ytdlp.py URL --simulate          # only check, download nothing

The default output folder is `~/Downloads/yt-dlp`. Failed URLs end up there in
`failed.log`; with `--archive`, `archive.txt` remembers videos that were already downloaded,
so running the same list again only fetches what is new (handy for cron).

For private or age-restricted videos use `--cookies-from-browser chrome`, or the browser
dropdown in the window.

## Without uv

Works with an existing Python: `pip install imageio-ffmpeg certifi sv-ttk darkdetect pillow`, then
`python3 simple-ytdlp.py`. The script downloads yt-dlp and deno itself on first start
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
