#!/usr/bin/env python3
"""
Canvas Sync — desktop setup app.

A friendly front-end for configuring the Canvas → Todoist sync. Enter your
tokens, pick courses from live dropdowns, route each course to a Todoist
project, and click "Connect & Deploy" — the app ships your settings to GitHub
so the sync runs in the cloud (laptop off).

Run from source:  python app/app.py
Build an app:      see build_exe.py  (PyInstaller one-dir, windowed)
"""

import os
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path

import requests
import webview  # pip install pywebview

import webbrowser
try:
    import ghauth
except Exception:
    ghauth = None

try:
    from schedule import local_times_to_cron
except Exception:  # fallback so the app still runs if schedule.py is missing
    def local_times_to_cron(times, offset_hours):
        out = []
        for t in times or []:
            try:
                hh, mm = t.strip().split(":")
                if len(hh) != 2 or len(mm) != 2:
                    continue
                h, m = int(hh), int(mm)
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    continue
            except Exception:
                continue
            u = (h * 60 + m - round(offset_hours * 60)) % 1440
            out.append(f"{u % 60} {u // 60} * * *")
        return out

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(__file__).resolve().parent
DEV_REPO_ROOT = APP_DIR.parent


def resource_path(rel: str) -> Path:
    """Path to a bundled resource (works in dev and inside a PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", str(APP_DIR)))
    return base / rel


# Where the working copy of the repo lives. In dev we use the checkout we're in;
# as a frozen exe we keep one under %LOCALAPPDATA% and materialize files into it.
if FROZEN:
    WORKDIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CanvasSync" / "repo"
    UI_FILE = resource_path("index.html")
else:
    WORKDIR = DEV_REPO_ROOT
    UI_FILE = APP_DIR / "index.html"

# Program files the deployed repo needs (source -> path inside the repo).
BUNDLED = {
    "program/canvas_sync.py": "canvas_sync.py",
    "program/requirements.txt": "requirements.txt",
    "program/config.example.json": "config.example.json",
    "program/canvas-sync.yml": ".github/workflows/canvas-sync.yml",
}

# Persisted setup state (tokens, course picks, project routing, schedule) so the
# app reopens fully populated instead of blank. User-scoped under %LOCALAPPDATA%.
# Note: this holds your Canvas/Todoist tokens in plaintext on your own machine —
# same trust boundary the app already uses (tokens are typed in and shipped to
# GitHub secrets). Delete this file to clear saved credentials.
SETTINGS_PATH = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                 / "CanvasSync" / "setup.json")


def _augmented_env():
    env = dict(os.environ)
    extra = [
        r"C:\Program Files\nodejs",
        str(Path(os.environ.get("APPDATA", "")) / "npm"),
        r"C:\Program Files\GitHub CLI",
        r"C:\Program Files\Git\cmd",
    ]
    extra = [p for p in extra if p and os.path.isdir(p)]
    env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def _run(args, cwd=None, **kw):
    # CREATE_NO_WINDOW stops a console from flashing for every gh/git call.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           env=_augmented_env(), cwd=str(cwd or WORKDIR),
                           creationflags=flags, **kw)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return False, str(e)


def _git_identity_ready():
    """Ensure git has a commit identity in WORKDIR. On a fresh machine neither
    user.name nor user.email is set — `gh auth setup-git` configures credentials,
    not identity — so `git commit` fails and the deploy would push nothing. Set a
    sensible local default for whichever value is missing (leaves existing ones)."""
    have_name, _ = _run(["git", "config", "user.name"])
    if not have_name:
        _run(["git", "config", "user.name", "Canvas Sync"])
    have_email, _ = _run(["git", "config", "user.email"])
    if not have_email:
        _run(["git", "config", "user.email",
              "canvas-sync@users.noreply.github.com"])


def env_name_for(key: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", key.upper()).strip("_") or "SCHOOL"
    return f"CANVAS_{slug}_TOKEN"


def materialize_program_files():
    """Copy bundled program files into WORKDIR if they're missing (frozen exe)."""
    for src, dst in BUNDLED.items():
        target = WORKDIR / dst
        if target.exists():
            continue
        s = resource_path(src)
        if s.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, target)


def todoist_headers(token):
    return {"Authorization": f"Bearer {token}"}


WORKFLOW_TMPL = '''name: Canvas sync

on:
  schedule:
__CRONS__
  workflow_dispatch: {}

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Restore notification state
        uses: actions/cache@v4
        with:
          path: state.json
          key: canvas-sync-state-${{ github.run_id }}
          restore-keys: |
            canvas-sync-state-
      - name: Run sync
        env:
__ENVS__
        run: python canvas_sync.py
'''


def write_workflow(crons, secret_names):
    """Generate .github/workflows/canvas-sync.yml with the chosen schedule and
    the exact secret env vars this user's schools need."""
    crons = crons or ["7 16 * * *"]
    cron_lines = "\n".join(f'    - cron: "{c}"' for c in crons)
    env_lines = "\n".join(f"          {n}: ${{{{ secrets.{n} }}}}" for n in secret_names)
    path = WORKDIR / ".github" / "workflows" / "canvas-sync.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = WORKFLOW_TMPL.replace("__CRONS__", cron_lines).replace("__ENVS__", env_lines)
    path.write_text(content, encoding="utf-8")


class Api:
    # ---------- Persistence ----------
    def load_settings(self):
        """Return the saved setup state (or an empty dict on first run)."""
        try:
            if SETTINGS_PATH.exists():
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                return {"ok": True, "settings": data}
        except Exception as e:
            return {"ok": False, "error": str(e), "settings": {}}
        return {"ok": True, "settings": {}}

    def save_settings(self, settings):
        """Persist the current setup state so the app reopens populated."""
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- GitHub ----------
    def github_status(self):
        ok, out = _run(["gh", "auth", "status"], cwd=Path.home())
        if not ok:
            return {"connected": False, "detail": "Not logged in to GitHub CLI."}
        m = re.search(r"account (\S+)", out)
        return {"connected": True, "user": m.group(1) if m else "?",
                "workflow_scope": "workflow" in out}

    def github_device_start(self):
        # Begin the in-app device flow and open the browser to the verify page.
        # No console window — the code is shown inside the app UI.
        if ghauth is None:
            return {"ok": False, "error": "auth module unavailable"}
        res = ghauth.start_device_flow()
        if res.get("ok") and res.get("verification_uri"):
            try:
                webbrowser.open(res["verification_uri"])
            except Exception:
                pass
        return res

    def github_device_poll(self, device_code):
        # Poll once; when authorized, store the token so gh + git use it.
        if ghauth is None:
            return {"status": "error", "error": "auth module unavailable"}
        res = ghauth.poll_device_flow(device_code)
        if res.get("status") == "done":
            ok, detail = ghauth.store_token(res.get("token", ""))
            if not ok:
                return {"status": "error", "error": detail}
        return res

    # ---------- Canvas ----------
    def fetch_courses(self, base, token):
        base = (base or "").rstrip("/")
        if not base or not token:
            return {"ok": False, "error": "Enter the Canvas URL and token first."}
        try:
            out, url = [], f"{base}/api/v1/courses"
            params = {"enrollment_state": "active", "per_page": 100}
            while url:
                r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                                 params=params, timeout=30)
                if r.status_code == 401:
                    return {"ok": False, "error": "Token rejected (401)."}
                r.raise_for_status()
                out.extend(r.json())
                url = r.links.get("next", {}).get("url")
                params = None
            return {"ok": True, "courses": [{"id": c["id"], "name": c.get("name", "")}
                                            for c in out if c.get("id") and c.get("name")]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Todoist ----------
    def test_todoist(self, token):
        ok, projects = self._projects(token)
        return {"ok": ok, "error": "" if ok else projects}

    def list_todoist_projects(self, token):
        ok, projects = self._projects(token)
        return {"ok": ok, "projects": projects if ok else [], "error":
                "" if ok else projects}

    def _projects(self, token):
        if not token:
            return False, "Enter a Todoist token."
        try:
            r = requests.get("https://api.todoist.com/api/v1/projects",
                             headers=todoist_headers(token), timeout=20)
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            data = r.json()
            items = data.get("results", data) if isinstance(data, dict) else data
            return True, [{"id": str(p["id"]), "name": p["name"]} for p in items]
        except Exception as e:
            return False, str(e)

    def _create_project(self, token, name):
        r = requests.post("https://api.todoist.com/api/v1/projects",
                          headers=todoist_headers(token), json={"name": name}, timeout=20)
        r.raise_for_status()
        return str(r.json()["id"])

    def create_project(self, token, name):
        if not token or not name.strip():
            return {"ok": False, "error": "Enter a project name."}
        try:
            pid = self._create_project(token, name.strip())
            return {"ok": True, "id": pid, "name": name.strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Deploy ----------
    def deploy(self, payload):
        log = []
        def step(m): log.append(m)
        repo = payload.get("repo", "").strip()
        if "/" not in repo:
            return {"ok": False, "log": ["Repo must look like owner/name."]}

        # 1) Prepare the working copy of the repo.
        WORKDIR.mkdir(parents=True, exist_ok=True)
        repo_exists, _ = _run(["gh", "repo", "view", repo], cwd=Path.home())
        if not (WORKDIR / ".git").exists():
            if repo_exists:
                step("Cloning your repo…")
                ok, out = _run(["gh", "repo", "clone", repo, str(WORKDIR)],
                               cwd=Path.home())
                if not ok and "already exists" not in out:
                    return {"ok": False, "log": [f"Clone failed:\n{out}"]}
            else:
                _run(["git", "init", "-b", "main"])
        materialize_program_files()
        _git_identity_ready()
        if not repo_exists:
            step(f"Creating repo {repo}…")
            _run(["git", "add", "-A"])
            committed, cout = _run(["git", "commit", "-m", "Initial canvas-sync deploy"])
            if not committed and "nothing to commit" not in cout.lower():
                return {"ok": False,
                        "log": log + [f"Could not make the initial commit:\n{cout}"]}
            ok, out = _run(["gh", "repo", "create", repo, "--public",
                            "--source", str(WORKDIR), "--remote", "origin", "--push"])
            if not ok:
                return {"ok": False, "log": log + [f"Could not create repo:\n{out}"]}
        step(f"Repo {repo} ready.")

        # 2) Resolve Todoist project routing (create any requested new projects).
        todoist = payload.get("todoist", "")
        project_map = dict(payload.get("project_map", {}))
        new_projects = payload.get("create_projects", {})  # {course_id: name}
        if new_projects and todoist:
            for cid, name in new_projects.items():
                try:
                    project_map[cid] = self._create_project(todoist, name)
                    step(f"Created Todoist project '{name}'.")
                except Exception as e:
                    step(f"Could not create project '{name}': {e}")

        # 3) Build config.json (no secrets inside).
        schools_cfg = []
        for s in payload["schools"]:
            entry = {"key": s["key"], "base": s["base"].rstrip("/"),
                     "token_env": env_name_for(s["key"])}
            if s.get("track_mode") == "only":
                entry["track_mode"] = "only"
                entry["only_course_ids"] = s.get("only_course_ids", [])
            if s.get("expiry"):
                entry["token_expires"] = s["expiry"]
            schools_cfg.append(entry)
        config = {
            "lookahead_days": payload.get("lookahead_days", 14),
            "recent_overdue_days": payload.get("recent_overdue_days", 7),
            "track_mode": "all", "only_course_ids": [], "ignore_course_ids": [],
            "ignore_name_patterns": payload.get("ignore_name_patterns", []),
            "notify_grades": True, "notify_due": True,
            "expiry_warn_days": payload.get("expiry_warn_days", 14),
            "timezone": (payload.get("timezone") or "").strip(),
            "utc_offset_hours": int(payload.get("utc_offset_hours", 0)),
            "todoist_project_map": project_map,
            "schools": schools_cfg,
        }
        (WORKDIR / "config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8")
        step("Wrote config.json.")

        # 3b) Generate the workflow with the chosen run times (local -> UTC cron)
        #     and exactly the secret env vars these schools need.
        crons = local_times_to_cron(payload.get("schedule_times", []),
                                    int(payload.get("utc_offset_hours", 0)))
        secret_names = ["TODOIST_TOKEN", "NTFY_TOPIC"] + \
            [env_name_for(s["key"]) for s in payload["schools"]]
        write_workflow(crons, secret_names)
        step(f"Wrote schedule ({len(crons or [1])} run/day) to the workflow.")

        # 4) Commit & push.
        _run(["git", "add", "-A"])
        ok, _ = _run(["git", "commit", "-m", "Update configuration via setup app"])
        if ok:
            ok, out = _run(["git", "push"])
            step("Pushed to GitHub." if ok else f"Push note:\n{out}")
        else:
            step("No config changes to push.")

        # 5) Secrets.
        def set_secret(name, value):
            if not value:
                return
            ok, out = _run(["gh", "secret", "set", name, "--repo", repo, "--body", value])
            step(f"Secret {name}: {'set' if ok else 'FAILED ' + out}")
        set_secret("TODOIST_TOKEN", todoist)
        set_secret("NTFY_TOPIC", payload.get("ntfy", ""))
        for s in payload["schools"]:
            set_secret(env_name_for(s["key"]), s.get("token", ""))

        # 6) Optional immediate run.
        if payload.get("run_now"):
            ok, out = _run(["gh", "workflow", "run", "canvas-sync.yml", "--repo", repo],
                           cwd=Path.home())
            step("Triggered a test run." if ok else f"Run note:\n{out}")

        step("\nDone! Your sync is live and runs on your schedule on GitHub.")
        return {"ok": True, "log": log}

    def actions_usage(self, repo):
        """Approx GitHub Actions minutes used this calendar month for the repo,
        by summing recent workflow-run durations.

        NOTE: This is an approximation.  (updated_at - run_started_at) is not
        the same as billable time, and only the most recent 100 runs are
        fetched (the API does not expose exact billable seconds)."""
        import datetime as dt
        ok, out = _run(["gh", "api", f"repos/{repo}/actions/runs?per_page=100"],
                       cwd=Path.home())
        if not ok:
            return {"ok": False, "error": (out or "could not read runs")[:200]}
        try:
            runs = json.loads(out).get("workflow_runs", [])
        except Exception as e:
            return {"ok": False, "error": str(e)}
        now = dt.datetime.now(dt.timezone.utc)
        total, count = 0.0, 0
        for r in runs:
            s, u = r.get("run_started_at"), r.get("updated_at")
            if not s or not u:
                continue
            try:
                sd = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                ud = dt.datetime.fromisoformat(u.replace("Z", "+00:00"))
            except Exception:
                continue
            if sd.year == now.year and sd.month == now.month:
                total += max(0.0, (ud - sd).total_seconds())
                count += 1
        # est_minutes: approximate, based on (updated_at - run_started_at)
        # and capped at the most recent 100 runs.
        return {"ok": True, "est_minutes": round(total / 60, 1), "run_count": count}


WINDOW_TITLE = "Canvas Sync — Setup"
_SINGLE_INSTANCE_MUTEX = None  # held for the process lifetime to keep the lock


def _acquire_single_instance() -> bool:
    """True if we're the only instance. Otherwise focus the running window and
    return False so the caller exits. Windows-only; a no-op elsewhere."""
    if os.name != "nt":
        return True
    import ctypes
    global _SINGLE_INSTANCE_MUTEX
    ERROR_ALREADY_EXISTS = 183
    _SINGLE_INSTANCE_MUTEX = ctypes.windll.kernel32.CreateMutexW(
        None, False, "CanvasSyncSetup.singleton")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, WINDOW_TITLE)
            if hwnd:
                user32.ShowWindow(hwnd, 9)        # SW_RESTORE (un-minimize)
                user32.SetForegroundWindow(hwnd)  # bring to front
        except Exception:
            pass
        return False
    return True


def main():
    if not _acquire_single_instance():
        return 0  # another copy is already open; we focused it and exit quietly
    webview.create_window(WINDOW_TITLE, str(UI_FILE),
                          js_api=Api(), width=940, height=860, min_size=(780, 660))
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
