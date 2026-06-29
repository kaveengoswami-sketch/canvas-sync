# Canvas Sync — Setup App

A small native desktop app that makes setup easy: type your tokens, pick your
courses from live dropdowns, and click **Connect & Deploy**. It pushes your
settings to GitHub so the sync runs in the cloud (your laptop can be off).

You don't write any code or touch git — the app handles all of that for you.

## Run it

```bash
pip install -r app/requirements.txt
python app/app.py
```

### Build & install as a real Windows app
```bash
pip install pyinstaller pillow
python app/make_icon.py        # generates app/icon.ico
python app/build_exe.py        # -> dist/CanvasSync/ (folder; windowed, no console)
powershell -ExecutionPolicy Bypass -File app/install_windows.ps1
```
The installer copies the app folder to `%LOCALAPPDATA%\Programs\CanvasSync` and
adds a Start Menu shortcut. After that it behaves like any normal app: press the
Windows key, type **Canvas Sync**, launch it, and right-click to **pin to
taskbar**. No console window — just the app.

> Built with PyInstaller `--onedir` (a folder, not a single `.exe`) so it starts
> in ~1-2s. A one-file build has to unpack itself to a temp folder on every
> launch, which on Windows looks like a 10-30s hang before the window appears.

The app bundles the program; on first deploy it clones/creates your
repo under `%LOCALAPPDATA%\CanvasSync` and pushes your settings from there.

> Requires the [GitHub CLI](https://cli.github.com) (`gh`) installed and logged
> in — the app uses it to create your repo and store secrets. The app's
> **GitHub** card shows your connection status and a one-click connect button.

## What each step does

1. **GitHub** — confirms you're connected and picks your repo (`you/canvas-sync`).
   GitHub is the free always-on engine that runs the sync.
2. **Notifications** — your Todoist token (with a Test button) and an ntfy topic
   for grade + token-expiry pushes (Random generates a secret one).
3. **Schools & courses** — add each Canvas account (URL + token + optional expiry
   date), **Fetch courses**, then tick exactly which courses to track. For each
   course, pick a **Todoist project** from the dropdown (or "➕ New project" to
   create one). The app **auto-guesses** a matching project by name (e.g. a
   "Chemistry" course → your "Chemistry" project) — you can override any guess
   from the dropdown. Leave "Track all" on to sync everything.
4. **Schedule** — set the daily run times in your **local timezone** (defaults to
   **9:00 AM**, **5:00 PM**, **12:00 AM**). Click a date/time field to open its
   picker. The app converts these to UTC cron lines on deploy.

Click **Load Todoist projects** first (in step 2) so the per-course dropdowns
are populated.

**Connect & Deploy** writes `config.json`, writes your chosen run times into the
GitHub Actions workflow (`.github/workflows/canvas-sync.yml`), pushes them,
stores your tokens as encrypted GitHub secrets, and (optionally) runs a test
sync right away.

## Notes
- Tokens you enter are sent only to your own machine's backend, then to Canvas
  (to read courses) and to GitHub (stored as encrypted secrets). They are never
  written into the repository.
- This app is just the front-end/installer. The sync itself runs on GitHub
  Actions on the schedule in `.github/workflows/canvas-sync.yml`.
