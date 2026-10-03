// Talks to the simple-ytdlp desktop app. The app listens on 127.0.0.1 only (ports 17653-17657) and
// accepts requests from this extension after the user allowed it once in the app's window.
// Shared by the popup and the background worker; nothing here leaves the computer.
"use strict";

const YTDLP = (() => {
  const PORTS = [17653, 17654, 17655, 17656, 17657];
  const RELEASES = "https://github.com/WhisperScript/simple-ytdlp/releases/latest";

  async function post(port, path, body, timeoutMs) {
    const control = new AbortController();
    const timer = setTimeout(() => control.abort(), timeoutMs);
    try {
      const response = await fetch(`http://127.0.0.1:${port}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: control.signal,
      });
      return { status: response.status, body: await response.json().catch(() => ({})) };
    } finally {
      clearTimeout(timer);
    }
  }

  // The port the app listens on (it takes the first free one); the last good one is tried first.
  async function findApp() {
    const { port } = await chrome.storage.local.get({ port: 0 });
    for (const candidate of [port, ...PORTS.filter((p) => p !== port)].filter(Boolean)) {
      try {
        const answer = await post(candidate, "/v1/status", {}, 1200);
        if (answer.body && answer.body.app === "simple-ytdlp") {
          await chrome.storage.local.set({ port: candidate });
          return { port: candidate, status: answer.body };
        }
      } catch (error) {
        // nothing listens there
      }
    }
    return null;
  }

  // state: added | exists | denied | busy | offline | error
  async function send(urls, mode) {
    const app = await findApp();
    if (!app) return { state: "offline" };
    try {
      // Waiting for the user's answer in the app window takes a while the first time.
      const answer = await post(app.port, "/v1/add", { urls, mode: mode || "" }, 130000);
      const body = answer.body || {};
      if (answer.status === 200 && body.ok) {
        return { state: body.added > 0 ? "added" : "exists", added: body.added, skipped: body.skipped };
      }
      if (answer.status === 403 && body.error === "denied") return { state: "denied" };
      if (answer.status === 409) return { state: "busy" };
      return { state: "error", detail: body.error || String(answer.status) };
    } catch (error) {
      return { state: "error", detail: String(error && error.message || error) };
    }
  }

  async function savedMode() {
    const { mode } = await chrome.storage.local.get({ mode: "" });
    return mode;
  }

  function isWebLink(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url);
  }

  return { findApp, send, savedMode, isWebLink, RELEASES };
})();
