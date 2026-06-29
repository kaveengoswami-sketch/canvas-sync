#!/usr/bin/env python3
"""
Build the Canvas Sync setup app as a standalone Windows program.

    pip install pyinstaller
    python app/build_exe.py

Output: dist/CanvasSync/  (a folder; run CanvasSync.exe inside it).

Uses --onedir (not --onefile) so launch is near-instant. A one-file exe has to
unpack its entire bundle to a temp dir on every start, which on Windows looks
like a 10-30s hang (no console or window appears during the unpack) and makes
antivirus re-scan it each time. --onedir keeps the unpacked layout on disk, so
it starts in ~1-2s. Run this from the repository root.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

try:
    import PyInstaller.__main__
except ImportError:
    sys.exit("PyInstaller not installed. Run: pip install pyinstaller")

sep = ";" if os.name == "nt" else ":"
data = [
    f"app/index.html{sep}.",
    f"canvas_sync.py{sep}program",
    f"requirements.txt{sep}program",
    f"config.example.json{sep}program",
    f".github/workflows/canvas-sync.yml{sep}program",
]
args = ["app/app.py", "--name", "CanvasSync", "--onedir", "--windowed",
        "--noupx", "--noconfirm", "--clean",
        # our local sibling modules live in app/; pin them so the right ones
        # are bundled (there is also a PyPI package called "schedule").
        "--paths", "app",
        "--hidden-import", "ghauth", "--hidden-import", "schedule"]
icon = ROOT / "app" / "icon.ico"
if icon.exists():
    args += ["--icon", str(icon)]
for d in data:
    args += ["--add-data", d]

print("Running PyInstaller…")
PyInstaller.__main__.run(args)
print("\nDone. See dist/CanvasSync/CanvasSync.exe")
