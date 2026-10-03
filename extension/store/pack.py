"""Build the extension packages: simple-ytdlp-extension.zip (Chrome Web Store, Edge Add-ons) and, with --firefox,
simple-ytdlp-extension-firefox.zip (addons.mozilla.org).

Both hold the same files. The Firefox package differs only in its manifest: Firefox runs the background script as an
event page (no service worker), and wants an add-on ID and the data-collection declaration.
Only the files the browser needs go in; the manifest's version must look like 1.2.3 and is printed.

Usage: python extension/store/pack.py [--firefox] [output.zip]
"""
import json
import re
import sys
import zipfile
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"store"}
SKIP_FILES = {"PRIVACY.md"}
GECKO_ID = "simple-ytdlp@whisperscript"
GECKO_MIN_VERSION = "140.0"                 # the first desktop Firefox that knows data_collection_permissions
GECKO_ANDROID_MIN_VERSION = "142.0"         # ... and Android (the extension needs the desktop app anyway)


def load_manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def firefox_manifest(manifest: dict) -> dict:
    """The manifest of the Firefox package (the Chrome one stays untouched)."""
    out = dict(manifest)
    out.pop("minimum_chrome_version", None)
    out["background"] = {"scripts": ["client.js", "background.js"]}
    out["browser_specific_settings"] = {"gecko": {
        "id": GECKO_ID,
        "strict_min_version": GECKO_MIN_VERSION,
        "data_collection_permissions": {"required": ["none"]},
    }, "gecko_android": {"strict_min_version": GECKO_ANDROID_MIN_VERSION}}
    return out


def files() -> list:
    return sorted(p for p in EXT.rglob("*") if p.is_file() and not (SKIP_DIRS & set(p.relative_to(EXT).parts[:1]))
                  and p.name not in SKIP_FILES and "__pycache__" not in p.parts)


def build(out: Path, firefox: bool = False) -> int:
    manifest = load_manifest()
    if not re.fullmatch(r"\d+(\.\d+){0,3}", manifest["version"]):
        raise SystemExit(f"Bad version: {manifest['version']}")
    paths = files()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            name = path.relative_to(EXT).as_posix()
            if name == "manifest.json" and firefox:
                zf.writestr(name, json.dumps(firefox_manifest(manifest), indent=2) + "\n")
            else:
                zf.write(path, name)
    print(f"{out}: version {manifest['version']}, {len(paths)} files" + (" (Firefox)" if firefox else ""))
    return len(paths)


def main() -> int:
    args = sys.argv[1:]
    firefox = "--firefox" in args
    args = [a for a in args if a != "--firefox"]
    default = "simple-ytdlp-extension-firefox.zip" if firefox else "simple-ytdlp-extension.zip"
    build(Path(args[0] if args else default), firefox)
    return 0


if __name__ == "__main__":
    sys.exit(main())
