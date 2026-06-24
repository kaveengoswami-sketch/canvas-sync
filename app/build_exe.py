#!/usr/bin/env python3
"""
Build a standalone Windows .exe of the Canvas Sync setup app.

    pip install pyinstaller
    python app/build_exe.py

Output: dist/CanvasSync.exe  (one file, no console window).
Run this from the repository root.
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
args = ["app/app.py", "--name", "CanvasSync", "--onefile", "--windowed",
        "--noconfirm", "--clean"]
for d in data:
    args += ["--add-data", d]

print("Running PyInstaller…")
PyInstaller.__main__.run(args)
print("\nDone. See dist/CanvasSync.exe")
