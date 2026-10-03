# Privacy policy - simple-ytdlp browser extension

**Short version:** the extension sends the address of a page or link to the simple-ytdlp app on your own computer when
you ask it to, and nothing else. It has no server, no account, no analytics and no tracking.

## What the extension does with data

- When you click the toolbar icon, press the shortcut or choose "Download ... with simple-ytdlp" in the right-click
  menu, the extension reads the **address (URL) and title of that page or link** and sends the address to the
  simple-ytdlp desktop app.
- The app listens on `127.0.0.1` (your own computer, ports 17653-17657). The data **never leaves your computer**
  through this extension. The extension has no network access to any other address.
- The extension stores two small settings on your computer with Chrome's storage: the port the app was last found on
  and the download format you picked on the settings page.

## What the extension does not do

- It does not read, collect or send your browsing history, page contents, cookies, passwords or form data.
- It does not run in the background on pages you visit; it only acts when you use it.
- It does not send anything to the developer or to third parties, and it does not load or run remote code.

## The app

What simple-ytdlp does with the link afterwards (it downloads the video with yt-dlp) is described in the
[project's README](https://github.com/WhisperScript/simple-ytdlp#readme). The app only accepts requests from a
browser extension you approved once in the app's window.

## Contact

Questions or concerns: open an issue at https://github.com/WhisperScript/simple-ytdlp/issues
(security problems: see [SECURITY.md](../SECURITY.md)).
