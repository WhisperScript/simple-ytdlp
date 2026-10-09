# Third-party components

simple-ytdlp itself is licensed under the [MIT License](LICENSE). The programs on the
[Releases page](https://github.com/WhisperScript/simple-ytdlp/releases) bundle, and the app downloads, the components
below. They keep their own licenses; follow the links for the full texts and the source code.

## Inside the downloadable programs (Windows, macOS, Linux)

| Component | License | Where |
|---|---|---|
| Python and its standard library (including Tcl/Tk) | PSF License; Tcl/Tk license (BSD-style) | https://docs.python.org/3/license.html, https://www.tcl.tk/software/tcltk/license.html |
| PyInstaller (packages the program) | GPL-2.0-or-later with an exception that allows distributing the packaged programs under any license | https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt |
| sv-ttk (window theme) | MIT | https://github.com/rdbende/Sun-Valley-ttk-theme |
| darkdetect (dark mode detection) | BSD-3-Clause | https://github.com/albertosottile/darkdetect |
| Pillow (thumbnails, icons) | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| certifi (certificate bundle) | MPL-2.0 | https://github.com/certifi/python-certifi |
| tkinterdnd2 (drag and drop) | MIT | https://github.com/Eliav2/tkinterdnd2 |
| imageio-ffmpeg (Python wrapper) | BSD-2-Clause | https://github.com/imageio/imageio-ffmpeg |
| **FFmpeg** (static build shipped by imageio-ffmpeg) | **GPL-3.0-or-later** (built with `--enable-gpl --enable-version3`) | https://ffmpeg.org/, build: https://johnvansickle.com/ffmpeg/ |

## Downloaded by the app when you use it

| Component | License | Where |
|---|---|---|
| yt-dlp (does the downloading) | Unlicense (public domain) | https://github.com/yt-dlp/yt-dlp |
| FFmpeg build with ffprobe (Tools → Get ffmpeg tools) | GPL-3.0-or-later | https://github.com/yt-dlp/FFmpeg-Builds |

## About FFmpeg

FFmpeg is a separate program that simple-ytdlp starts for converting and merging files; it is not linked into
simple-ytdlp's own code. Its source code is available from https://ffmpeg.org/download.html (the exact version is
printed by `ffmpeg -version`) and, for the builds named above, from their authors' pages. If you cannot get the source
there, open an issue at https://github.com/WhisperScript/simple-ytdlp/issues and it will be provided.

## Browser extension

The browser extension (`extension/`) has no third-party code.
