// One click: the page in the current tab goes to the simple-ytdlp app right when the popup opens.
"use strict";

const $ = (id) => document.getElementById(id);

function show(kind, text, buttons = []) {
  $("state").className = "state " + kind;
  $("mark").textContent = kind === "ok" ? "✓" : kind === "bad" ? "!" : "";
  $("message").textContent = text;
  const box = $("actions");
  box.replaceChildren();
  for (const { label, primary, run } of buttons) {
    const button = document.createElement("button");
    button.textContent = label;
    if (primary) button.className = "primary";
    button.addEventListener("click", run);
    box.append(button);
  }
}

async function main() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab && tab.url;
  $("page").textContent = (tab && tab.title) || "";
  if (!YTDLP.isWebLink(url)) {
    show("bad", "This page has no link to download. Open a video or playlist page first.");
    return;
  }
  const app = await YTDLP.findApp();
  if (!app) {
    show("bad", "simple-ytdlp is not running. Open the app, then try again.", [
      { label: "Try again", primary: true, run: main },
      { label: "Get the app", run: () => chrome.tabs.create({ url: YTDLP.RELEASES }) },
    ]);
    return;
  }
  if (!app.status.paired) show("wait", "Allow this extension in the simple-ytdlp window …");
  else show("wait", "Sending …");
  const result = await YTDLP.send([url], await YTDLP.savedMode());
  const retry = [{ label: "Try again", primary: true, run: main }];
  switch (result.state) {
    case "added": return show("ok", "Added to the download list.");
    case "exists": return show("ok", "Already in the list - nothing added twice.");
    case "denied": return show("bad", "Not allowed. Click the icon again to be asked once more.", retry);
    case "busy": return show("bad", "The app is waiting for your answer in its window.", retry);
    case "offline": return show("bad", "simple-ytdlp is not running. Open the app, then try again.", retry);
    default: return show("bad", "Could not reach simple-ytdlp (" + (result.detail || "unknown") + ").", retry);
  }
}

$("settings").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});
main();
