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

On Windows you can also just double-click **`Launch Canvas Sync.bat`**.

### Build a standalone .exe
```bash
pip install pyinstaller
python app/build_exe.py        # -> dist/CanvasSync.exe
```
The one-file exe bundles the program; on first deploy it clones/creates your
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

Click **Load Todoist projects** first (in step 2) so the per-course dropdowns
are populated.

**Connect & Deploy** writes `config.json`, pushes it, stores your tokens as
encrypted GitHub secrets, and (optionally) runs a test sync right away.

## Notes
- Tokens you enter are sent only to your own machine's backend, then to Canvas
  (to read courses) and to GitHub (stored as encrypted secrets). They are never
  written into the repository.
- This app is just the front-end/installer. The sync itself runs on GitHub
  Actions on the schedule in `.github/workflows/canvas-sync.yml`.
