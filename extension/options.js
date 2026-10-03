"use strict";

const $ = (id) => document.getElementById(id);

async function status() {
  const app = await YTDLP.findApp();
  const box = $("state");
  if (!app) {
    box.className = "state bad";
    $("mark").textContent = "!";
    $("message").textContent = "simple-ytdlp is not running. Open the app to use the extension.";
  } else if (!app.status.paired) {
    box.className = "state ok";
    $("mark").textContent = "✓";
    $("message").textContent = "The app is running. It asks you once to allow this extension.";
  } else {
    box.className = "state ok";
    $("mark").textContent = "✓";
    $("message").textContent = `Connected to simple-ytdlp ${app.status.version}, ${app.status.queued} in the list.`;
  }
}

async function main() {
  $("mode").value = await YTDLP.savedMode();
  $("mode").addEventListener("change", async () => {
    await chrome.storage.local.set({ mode: $("mode").value });
    $("saved").hidden = false;
    setTimeout(() => { $("saved").hidden = true; }, 1500);
  });
  status();
}
main();
