"""The extension's files are what the Chrome Web Store needs (no browser or window required)."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def pil():
    try:
        from PIL import Image
    except ImportError:
        raise unittest.SkipTest("Pillow is not installed")
    return Image


class Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and "src" in a:
            self.refs.append(a["src"])
        if tag == "link" and "href" in a:
            self.refs.append(a["href"])
        if tag == "script" and "src" not in a:
            self.refs.append("<inline script>")
        if tag == "img" and "src" in a:
            self.refs.append(a["src"])


class Manifest(unittest.TestCase):
    def test_basics(self):
        self.assertEqual(MANIFEST["manifest_version"], 3)
        self.assertRegex(MANIFEST["version"], r"^\d+(\.\d+){0,3}$")
        self.assertLessEqual(len(MANIFEST["name"]), 45)
        self.assertLessEqual(len(MANIFEST["description"]), 132)
        self.assertNotIn("key", MANIFEST, "the store assigns the key; a fixed one would clash with it")

    def test_permissions_stay_minimal(self):
        self.assertEqual(sorted(MANIFEST["permissions"]), ["activeTab", "contextMenus", "storage"])
        self.assertEqual(MANIFEST["host_permissions"], ["http://127.0.0.1/*"], "only this computer")
        for banned in ("content_scripts", "externally_connectable", "web_accessible_resources",
                       "optional_host_permissions", "optional_permissions"):
            self.assertNotIn(banned, MANIFEST)

    def test_every_referenced_file_exists(self):
        paths = list(MANIFEST["icons"].values()) + list(MANIFEST["action"]["default_icon"].values())
        paths += [MANIFEST["action"]["default_popup"], MANIFEST["background"]["service_worker"],
                  MANIFEST["options_ui"]["page"]]
        for rel in paths:
            self.assertTrue((EXT / rel).is_file(), rel)

    def test_icons_have_their_sizes(self):
        Image = pil()
        for size, rel in MANIFEST["icons"].items():
            with Image.open(EXT / rel) as img:
                self.assertEqual(img.size, (int(size), int(size)), rel)

    def test_pages_load_only_their_own_files_and_no_inline_script(self):
        for page in ("popup.html", "options.html"):
            refs = Refs()
            refs.feed((EXT / page).read_text(encoding="utf-8"))
            self.assertTrue(refs.refs)
            for ref in refs.refs:
                self.assertFalse(ref.startswith(("http:", "https:", "//", "<inline")), f"{page}: {ref}")
                self.assertTrue((EXT / ref).is_file(), f"{page}: {ref}")

    def test_no_remote_code_and_only_the_local_address(self):
        for path in EXT.glob("*.js"):
            code = path.read_text(encoding="utf-8")
            self.assertNotRegex(code, r"\beval\s*\(|new Function|importScripts\(\s*['\"]https?:")
            hosts = set(re.findall(r"https?://([^/'\"`:\s$]+)", code))
            self.assertLessEqual(hosts, {"127.0.0.1", "github.com"}, f"{path.name}: {hosts}")

    def test_ports_match_the_apps(self):
        spec = __import__("importlib.util").util.spec_from_file_location("m", ROOT / "simple-ytdlp.py")
        app = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(app)
        found = re.search(r"const PORTS = \[([^\]]+)\]", (EXT / "client.js").read_text(encoding="utf-8")).group(1)
        self.assertEqual([int(p) for p in found.split(",")], list(app.BRIDGE_PORTS))


class StorePackage(unittest.TestCase):
    def test_listing_assets_have_the_required_sizes(self):
        Image = pil()
        for name, size in (("screenshot-1.png", (1280, 800)), ("screenshot-2.png", (1280, 800)),
                           ("promo-small.png", (440, 280))):
            with Image.open(EXT / "store" / name) as img:
                self.assertEqual(img.size, size, name)

    def test_listing_text_fits_the_limits(self):
        text = (EXT / "store" / "listing.md").read_text(encoding="utf-8")
        summary = re.search(r"\*\*Summary \(max 132 characters\):\*\*\n(.+)", text).group(1)
        self.assertLessEqual(len(summary), 132)
        self.assertEqual(summary, MANIFEST["description"])
        self.assertTrue((EXT / "PRIVACY.md").is_file())
        self.assertIn("extension/PRIVACY.md", text)

    def test_firefox_listing_fits_the_limits(self):
        text = (EXT / "store" / "listing-firefox.md").read_text(encoding="utf-8")
        summary = re.search(r"\*\*Summary \(max 250 characters\):\*\*\n(.+)", text).group(1)
        self.assertLessEqual(len(summary), 250)
        self.assertIn("extension/PRIVACY.md", text)

    def test_the_zip_holds_the_extension_and_nothing_else(self):
        out = Path(tempfile.mkdtemp()) / "ext.zip"
        done = subprocess.run([sys.executable, str(EXT / "store" / "pack.py"), str(out)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        self.assertIn("manifest.json", names)
        self.assertIn("icons/icon128.png", names)
        self.assertFalse([n for n in names if n.startswith("store/") or n.endswith(".md") or ".." in n], names)


def load_pack():
    spec = importlib.util.spec_from_file_location("pack", EXT / "store" / "pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NODE_SCRIPT = r"""
// Loads the extension's scripts the way Firefox does (listed in the manifest, no importScripts) and clicks the
// right-click entry once.
const vm = require("vm"), fs = require("fs"), path = require("path");
const dir = process.argv[1], order = JSON.parse(process.argv[2]);
const calls = [];
const chrome = {
  runtime: { onInstalled: { addListener() {} } },
  contextMenus: { removeAll() {}, create() {}, onClicked: { addListener() {} } },
  action: { setBadgeText(a) { calls.push(["badge", a.text]); }, setBadgeBackgroundColor() {}, setTitle() {} },
  storage: { local: { async get(d) { return { ...d, port: 17653 }; }, async set() {} } },
};
const fetch = async (url, init) => {
  calls.push(["fetch", url, JSON.parse(init.body)]);
  const body = url.endsWith("/v1/status") ? { ok: true, app: "simple-ytdlp", paired: true }
                                          : { ok: true, added: 1, skipped: 0 };
  return { status: 200, json: async () => body };
};
const context = vm.createContext({ chrome, fetch, setTimeout, clearTimeout, AbortController, console });
for (const file of order) vm.runInContext(fs.readFileSync(path.join(dir, file), "utf8"), context, { filename: file });
(async () => {
  await vm.runInContext("onMenu", context)({ menuItemId: "linkAudio", linkUrl: "https://x.test/v" }, { id: 7 });
  console.log(JSON.stringify(calls));
})();
"""


class FirefoxPackage(unittest.TestCase):
    def setUp(self):
        self.pack = load_pack()
        self.manifest = self.pack.firefox_manifest(MANIFEST)

    def test_the_chrome_manifest_stays_as_it_is(self):
        self.assertEqual(MANIFEST["background"], {"service_worker": "background.js"})
        self.assertNotIn("browser_specific_settings", MANIFEST)

    def test_background_is_an_event_page_and_the_rest_is_shared(self):
        m = self.manifest
        self.assertEqual(m["background"], {"scripts": ["client.js", "background.js"]})
        self.assertNotIn("minimum_chrome_version", m)
        for key in ("permissions", "host_permissions", "action", "options_ui", "icons", "commands", "version", "name"):
            self.assertEqual(m[key], MANIFEST[key], key)

    def test_add_on_id_and_data_declaration(self):
        gecko = self.manifest["browser_specific_settings"]["gecko"]
        self.assertRegex(gecko["id"], r"^[\w.-]+@[\w.-]+$")
        self.assertEqual(gecko["data_collection_permissions"], {"required": ["none"]})
        self.assertIn("strict_min_version", self.manifest["browser_specific_settings"]["gecko_android"])

    def test_the_zip_has_the_firefox_manifest_and_the_same_files(self):
        folder = Path(tempfile.mkdtemp())
        chrome_zip, firefox_zip = folder / "c.zip", folder / "f.zip"
        self.pack.build(chrome_zip)
        self.pack.build(firefox_zip, firefox=True)
        with zipfile.ZipFile(chrome_zip) as c, zipfile.ZipFile(firefox_zip) as f:
            self.assertEqual(sorted(c.namelist()), sorted(f.namelist()))
            self.assertIn("service_worker", json.loads(c.read("manifest.json"))["background"])
            self.assertIn("scripts", json.loads(f.read("manifest.json"))["background"])
            self.assertEqual(c.read("background.js"), f.read("background.js"))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_scripts_work_without_import_scripts(self):
        """Firefox has no importScripts: the two files are loaded one after the other into one scope."""
        done = subprocess.run(["node", "-e", NODE_SCRIPT, str(EXT), json.dumps(["client.js", "background.js"])],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual([c[0] for c in calls], ["fetch", "fetch", "badge"])
        self.assertEqual(calls[1][2], {"urls": ["https://x.test/v"], "mode": "mp3"})
        self.assertEqual(calls[2][1], "\u2713")


if __name__ == "__main__":
    unittest.main()
