"""Build simple-ytdlp-extension.zip, the package for the Chrome Web Store (and Edge Add-ons).

Only the files the browser needs go in; the manifest's version must look like 1.2.3 and is printed.
Usage: python extension/store/pack.py [output.zip]
"""
import json
import re
import sys
import zipfile
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"store"}
SKIP_FILES = {"PRIVACY.md"}


def main() -> int:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"\d+(\.\d+){0,3}", manifest["version"]):
        print(f"Bad version: {manifest['version']}")
        return 1
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "simple-ytdlp-extension.zip")
    files = sorted(p for p in EXT.rglob("*") if p.is_file() and not (SKIP_DIRS & set(p.relative_to(EXT).parts[:1]))
                   and p.name not in SKIP_FILES and "__pycache__" not in p.parts)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(EXT).as_posix())
    print(f"{out}: version {manifest['version']}, {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
