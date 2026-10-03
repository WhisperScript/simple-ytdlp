// Right-click menu entries, a keyboard shortcut (see manifest) and the badge that confirms them.
"use strict";
importScripts("client.js");

const MENU = {
  link: { title: "Download link with simple-ytdlp", contexts: ["link"] },
  linkAudio: { title: "Download link as MP3 with simple-ytdlp", contexts: ["link"] },
  pageAudio: { title: "Download this page as MP3 with simple-ytdlp", contexts: ["page"] },
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    for (const [id, entry] of Object.entries(MENU)) chrome.contextMenus.create({ id, ...entry });
  });
});

function flash(tabId, text, color, title) {
  const where = tabId >= 0 ? { tabId } : {};
  chrome.action.setBadgeBackgroundColor({ color, ...where });
  chrome.action.setBadgeText({ text, ...where });
  chrome.action.setTitle({ title, ...where });
  setTimeout(() => {
    chrome.action.setBadgeText({ text: "", ...where });
    chrome.action.setTitle({ title: "Download with simple-ytdlp", ...where });
  }, 4000);
}

const MESSAGES = {
  added: ["✓", "#2e7d32", "Sent to simple-ytdlp"],
  exists: ["✓", "#2e7d32", "Already in simple-ytdlp"],
  offline: ["!", "#c62828", "simple-ytdlp is not running - open the app and try again"],
  denied: ["!", "#c62828", "Not allowed in the simple-ytdlp window"],
  busy: ["!", "#c62828", "simple-ytdlp is waiting for your answer in its window"],
  error: ["!", "#c62828", "Could not reach simple-ytdlp"],
};

async function onMenu(info, tab) {
  const url = info.menuItemId === "pageAudio" ? info.pageUrl : info.linkUrl;
  const tabId = tab ? tab.id : -1;
  if (!YTDLP.isWebLink(url)) return flash(tabId, "!", "#c62828", "That is not a web link");
  const mode = info.menuItemId === "link" ? await YTDLP.savedMode() : "mp3";
  const result = await YTDLP.send([url], mode);
  const [text, color, title] = MESSAGES[result.state] || MESSAGES.error;
  flash(tabId, text, color, title);
}

chrome.contextMenus.onClicked.addListener(onMenu);
