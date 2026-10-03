"""The extension's files are what the Chrome Web Store needs (no browser or window required)."""
import json
import re
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


if __name__ == "__main__":
    unittest.main()
