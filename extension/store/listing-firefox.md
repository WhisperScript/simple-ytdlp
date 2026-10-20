# addons.mozilla.org (Firefox) listing - simple-ytdlp

Submit at https://addons.mozilla.org/developers/ -> Submit a New Add-on -> **On this site** (listed).
The package is `simple-ytdlp-extension-firefox.zip` (on every GitHub release, or `python extension/store/pack.py --firefox`).
Same code as the Chrome package; only the manifest differs (event page instead of a service worker, add-on ID,
data-collection declaration). It needs Firefox 140 or newer.

## Listing

**Name:** simple-ytdlp - Send to app
**Add-on URL (slug):** simple-ytdlp
**Summary (max 250 characters):**
Send the video or playlist you are looking at to the simple-ytdlp desktop app with one click. Needs the free simple-ytdlp app (Windows, macOS, Linux); the extension only talks to it on your own computer.

**Categories:** Download Management (and Other)
**Description:** the same text as in `listing.md` (Chrome).
**Support site:** https://github.com/WhisperScript/simple-ytdlp
**Support URL for problems:** https://github.com/WhisperScript/simple-ytdlp/issues
**Privacy policy:** https://github.com/WhisperScript/simple-ytdlp/blob/main/extension/PRIVACY.md
**Screenshots:** `store/screenshot-1.png`, `store/screenshot-2.png`
**License:** MIT License (choose it in the form; the text is in the repository's `LICENSE`).

## Data collection (the manifest already says "none")

The extension collects no data and sends nothing off the computer; it only passes the address of the page the user
chooses to the desktop app on the same computer (`127.0.0.1`).

## Notes to the reviewer

- The add-on is a remote control for a desktop app. To try it: install simple-ytdlp from
  https://github.com/WhisperScript/simple-ytdlp/releases/latest and start it, open any video page (for example
  https://www.youtube.com/watch?v=jNQXAC9IVRw) and click the add-on's toolbar button. The app asks once to allow the
  add-on - choose Yes; the link then appears in the app's download list. Without the app the popup says
  "simple-ytdlp is not running".
- Permissions: `activeTab` (address of the tab the user clicked on), `contextMenus` (right-click entries), `storage`
  (the app's port and the chosen format), host permission `http://127.0.0.1/*` (the app on the user's own computer; it
  listens on ports 17653-17657). No other host is contacted.
- No build step, no minified or generated code: the package is the source. To reproduce the zip from the repository:
  `python extension/store/pack.py --firefox`.
