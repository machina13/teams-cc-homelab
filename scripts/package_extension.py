#!/usr/bin/env python3
"""Create loadable Chrome extension ZIP without secrets or host state."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "extension"
OUT = ROOT / "teams-cc-groq-v2.zip"
ALLOW = ("manifest.json", "content.js", "meeting_prompt.js", "background.js", "popup.html",
         "popup.js", "updater.js", "offscreen.html", "offscreen.js", "options.html", "options.js")
with ZipFile(OUT, "w", compression=ZIP_DEFLATED) as zipfile:
    for name in ALLOW:
        item = SRC / name
        if not item.is_file():
            raise SystemExit(f"Missing extension file: {item}")
        zipfile.write(item, arcname=name)
print(f"Built {OUT}")
