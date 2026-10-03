# Chrome Web Store listing - simple-ytdlp

Copy these into the developer dashboard (https://chrome.google.com/webstore/devconsole).

## Store listing

**Name:** simple-ytdlp - Send to app
**Category:** Productivity
**Language:** English
**Summary (max 132 characters):**
Send the video or playlist you are looking at to the simple-ytdlp desktop app with one click.

**Description:**

> Send what you are watching to simple-ytdlp - with one click.
>
> simple-ytdlp is a free desktop app that downloads videos and audio from YouTube and many other sites. This extension is its remote control in your browser: open a video or playlist, click the icon, and it appears in the app's download list. No copying and pasting of links.
>
> HOW IT WORKS
> - Install the simple-ytdlp app (Windows, macOS, Linux): https://github.com/WhisperScript/simple-ytdlp/releases/latest
> - Open the app, then click the extension's icon on any video or playlist page.
> - The first time, the app asks you once to allow the extension.
>
> FEATURES
> - One click (or Alt+Shift+D) sends the current page to the app.
> - Right-click any link: "Download link with simple-ytdlp" or "... as MP3".
> - Choose in the settings whether the icon downloads video or audio.
> - Playlists, duplicates, pause and resume are handled by the app.
>
> PRIVACY
> The extension talks only to the simple-ytdlp app on your own computer (127.0.0.1). It sends the address of the page you click on - nothing else, to no one else. No account, no tracking. The app only accepts requests from an extension you approved.
>
> This extension needs the simple-ytdlp desktop app. Open source: https://github.com/WhisperScript/simple-ytdlp
>
> simple-ytdlp is an independent project and is not affiliated with or endorsed by YouTube or Google.

**Graphic assets:** `icons/icon128.png` (store icon), `store/screenshot-1.png`, `store/screenshot-2.png` (1280x800),
`store/promo-small.png` (440x280).

## Privacy practices tab

**Single purpose:** Send the web page or link the user chooses to the simple-ytdlp desktop app, which downloads it.

**Permission justifications**

| Permission | Justification |
|---|---|
| `activeTab` | Reads the address and title of the current tab only when the user clicks the extension's icon or uses its shortcut, to send that address to the app. |
| `contextMenus` | Adds "Download link with simple-ytdlp" entries to the right-click menu. |
| `storage` | Remembers the app's port and the download format chosen on the settings page. |
| Host permission `http://127.0.0.1/*` | Connects to the simple-ytdlp app running on the user's own computer (the app listens on `127.0.0.1`, ports 17653-17657). No other host is contacted. |

**Remote code:** No, I am not using remote code.

**Data usage:** The extension sends the URL of the page or link the user chooses to an app on the user's own computer.
It does not collect or transmit user data to the developer or any third party. Tick none of the data-collection
categories, and confirm all three certifications (no sale of data, no unrelated use, no creditworthiness use).

**Privacy policy URL:** https://github.com/WhisperScript/simple-ytdlp/blob/main/extension/PRIVACY.md

## Notes for the reviewer (test instructions)

The extension is a remote control for a desktop app, so a reviewer needs the app:
1. Download simple-ytdlp from https://github.com/WhisperScript/simple-ytdlp/releases/latest and start it.
2. Open any video page, for example https://www.youtube.com/watch?v=jNQXAC9IVRw, and click the extension's icon.
3. The app asks once to allow the extension - choose Yes. The link then appears in the app's download list.
Without the app running, the popup says "simple-ytdlp is not running".

## Releasing a new version

1. Raise `version` in `extension/manifest.json` (the store only accepts a higher number than the published one).
2. Run `python extension/store/pack.py` (or take `simple-ytdlp-extension.zip` from the GitHub release).
3. Dashboard -> your item -> Package -> Upload new package -> Submit for review.
