"""Remote control of a real Chromium with the extension loaded (used by test_extension_e2e.py, run with Playwright).

Reads one command per line from stdin and answers each with one JSON line on stdout:
  popup_real            open the real popup on a page (what a click on the toolbar icon does)
  popup_stub URL        open popup.html in a tab that pretends URL is the current tab; reports what it shows
  menu ID URL           run the right-click handler; reports the badge it leaves
  options [MODE]        open the settings page (optionally pick MODE); reports its status line
  shot NAME [SELECTOR]  screenshot of the last opened extension page (or one element) into the folder in argv[2]
  quit
"""
import http.server
import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

EXT = Path(sys.argv[1]).resolve()
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None


class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<title>A video worth keeping</title><h1>A page</h1>")

    def log_message(self, *args):
        pass


def say(**data):
    print(json.dumps(data), flush=True)


def main():
    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            tempfile.mkdtemp(), headless=False, viewport={"width": 1000, "height": 700}, device_scale_factor=2,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        base = sw.url.rsplit("/", 1)[0]
        say(ready=True, base=base, site=site.server_address[1])
        last = None
        for line in sys.stdin:
            cmd, _, rest = line.strip().partition(" ")
            if cmd == "quit":
                break
            try:
                last = run(ctx, sw, base, site, cmd, rest, last)
            except Exception as exc:
                say(error=str(exc)[:500])
        ctx.close()


def run(ctx, sw, base, site, cmd, rest, last):
    """One command; returns the page to take screenshots of."""
    if cmd == "popup_real":
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{site.server_address[1]}/watch?v=real{rest}")
        page.bring_to_front()
        sw.evaluate("chrome.action.openPopup()")
        say(ok=True)
    elif cmd == "popup_stub":
        last = ctx.new_page()
        last.add_init_script("chrome.tabs.query = async () => [{id: 1, title: 'A video worth keeping', url: %s}];"
                             % json.dumps(rest))
        last.goto(f"{base}/popup.html")
        last.wait_for_selector("#state:not(.wait)", timeout=60000)
        say(state=last.eval_on_selector("#state", "e => e.className"), message=last.inner_text("#message"),
            buttons=last.eval_on_selector_all("button", "b => b.map(x => x.textContent)"))
    elif cmd == "menu":
        menu_id, _, url = rest.partition(" ")
        badge = sw.evaluate(
            "async ([id, url]) => { await onMenu({menuItemId: id, linkUrl: url, pageUrl: url}, {id: -1});"
            " return chrome.action.getBadgeText({}); }", [menu_id, url])
        say(badge=badge)
    elif cmd == "options":
        last = ctx.new_page()
        last.goto(f"{base}/options.html")
        if rest:
            last.select_option("#mode", rest)
            last.wait_for_selector("#saved:not([hidden])")
        last.wait_for_selector("#state:not(.wait)")
        say(message=last.inner_text("#message"), mode=last.input_value("#mode"))
    elif cmd == "shot":
        name, _, selector = rest.partition(" ")
        target = last.locator(selector) if selector else last
        target.screenshot(path=str(SHOTS / f"{name}.png"))
        say(ok=True)
    else:
        say(error=f"unknown command {cmd}")
    return last


main()
