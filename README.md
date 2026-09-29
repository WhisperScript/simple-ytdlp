# ytdl — yt-dlp frontend (macOS / Linux / Windows)

## Ready-made programs

Under **Releases** there is one program per system — nothing to install, just unpack and start:

| System | File | Start |
|---|---|---|
| Windows | `ytdl-windows.zip` | Double-click `ytdl.exe`. On the SmartScreen warning: "More info → Run anyway". |
| macOS (Apple Silicon) | `ytdl-macos.zip` | Unpack, then **right-click ytdl.app → Open**. If it is blocked: `xattr -dr com.apple.quarantine ytdl.app` |
| Linux | `ytdl-linux.tar.gz` | `tar xzf ytdl-linux.tar.gz && ./ytdl` |

On first start the program downloads yt-dlp and deno automatically (internet required,
takes a few seconds). ffmpeg is built in. The **"Update yt-dlp"** button keeps it current —
this helps when YouTube changes something and downloads suddenly fail. Once a day the
program also checks silently for a yt-dlp update at startup and installs it.

The interface follows the system theme (light/dark) and can be switched with a button.
A URL in the clipboard is inserted automatically when you switch back to the window (if the
URL box is empty), and you get a notification when a run finishes.

Settings and the downloaded tools live in the user data folder (Windows `%LOCALAPPDATA%\ytdl`,
macOS `~/Library/Application Support/ytdl`, Linux `~/.local/share/ytdl`).

The programs are not code-signed, hence the warnings on first start. Intel Macs use the
`uv` route below.

### Building it yourself / publishing a release

The workflow [.github/workflows/build.yml](.github/workflows/build.yml) builds all three
systems automatically. Push the repo to GitHub, then:

    git tag v1.0 && git push --tags

After a few minutes everything is available under **Releases**. It can also be started
manually via **Actions → Build → Run workflow** (result is a downloadable artifact, nothing is
published). Locally, e.g. on Windows:

    pip install pyinstaller imageio-ffmpeg certifi sv-ttk darkdetect
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

Modes: `video` (best quality), `video1080`, `video720`, `mp3`, `m4a`, `opus`.

Unknown flags are passed on to yt-dlp unchanged:

    uv run ytdl.py URL --playlist-items 1-5
    uv run ytdl.py URL --simulate          # only check, download nothing

The default output folder is `~/Downloads/yt-dlp`. Failed URLs end up there in
`failed.log`; with `--archive`, `archive.txt` remembers videos that were already downloaded,
so running the same list again only fetches what is new (handy for cron).

For private or age-restricted videos use `--cookies-from-browser chrome`, or the browser
dropdown in the window.

## Without uv

Works with an existing Python: `pip install imageio-ffmpeg certifi sv-ttk darkdetect`, then
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
