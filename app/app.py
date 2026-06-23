#!/usr/bin/env python3
"""
Canvas Sync — desktop setup app.

A friendly front-end for configuring the Canvas → Todoist sync. You enter your
tokens, pick your courses from live dropdowns, and click "Connect GitHub" — the
app deploys your settings to GitHub so the sync runs in the cloud (laptop off).

Run:  python app/app.py
Requires:  pip install pywebview   (and the `gh` GitHub CLI, logged in)
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

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_FILE = Path(__file__).resolve().parent / "index.html"


def _augmented_env():
    """Make sure git/gh/node are findable regardless of how the app launched."""
    env = dict(os.environ)
    extra = [
        r"C:\Program Files\nodejs",
        str(Path(os.environ.get("APPDATA", "")) / "npm"),
        r"C:\Program Files\GitHub CLI",
        r"C:\Program Files\Git\cmd",
        r"D:\Git\cmd",
    ]
    # Keep only paths that exist, plus anything already on PATH.
    extra = [p for p in extra if p and os.path.isdir(p)]
    env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def _run(args, **kw):
    """Run a command, return (ok, combined_output)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           env=_augmented_env(), cwd=str(REPO_ROOT), **kw)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


def env_name_for(key: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", key.upper()).strip("_") or "SCHOOL"
    return f"CANVAS_{slug}_TOKEN"


class Api:
    """Methods here are callable from the UI as window.pywebview.api.<name>()."""

    # ---- GitHub ----
    def github_status(self):
        ok, out = _run(["gh", "auth", "status"])
        if not ok:
            return {"connected": False, "detail": "Not logged in to GitHub CLI."}
        m = re.search(r"account (\S+)", out)
        scopes_ok = "workflow" in out
        return {"connected": True, "user": m.group(1) if m else "?",
                "workflow_scope": scopes_ok}

    def github_connect(self):
        # Opens the device-code flow in the user's browser.
        ok, out = _run(["gh", "auth", "login", "--web", "--git-protocol", "https",
                        "--scopes", "repo,workflow"], timeout=5)
        return {"ok": ok, "detail": out or "Follow the browser prompt, then click "
                                            "'Check connection' again."}

    # ---- Canvas ----
    def fetch_courses(self, base, token):
        base = (base or "").rstrip("/")
        if not base or not token:
            return {"ok": False, "error": "Enter the Canvas URL and token first."}
        try:
            out, url = [], f"{base}/api/v1/courses"
            params = {"enrollment_state": "active", "per_page": 100}
            headers = {"Authorization": f"Bearer {token}"}
            while url:
                r = requests.get(url, headers=headers, params=params, timeout=30)
                if r.status_code == 401:
                    return {"ok": False, "error": "Token rejected (401). Check it."}
                r.raise_for_status()
                out.extend(r.json())
                url = r.links.get("next", {}).get("url")
                params = None
            courses = [{"id": c["id"], "name": c.get("name", "Course")}
                       for c in out if c.get("id") and c.get("name")]
            return {"ok": True, "courses": courses}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def test_todoist(self, token):
        if not token:
            return {"ok": False, "error": "Enter a Todoist token."}
        try:
            r = requests.get("https://api.todoist.com/api/v1/projects",
                             headers={"Authorization": f"Bearer {token}"}, timeout=20)
            return {"ok": r.status_code == 200,
                    "error": "" if r.status_code == 200 else f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- Deploy ----
    def deploy(self, payload):
        """payload = {repo, todoist, ntfy, expiry_warn_days, run_now,
                      schools:[{key,base,token,expiry,track_mode,only_course_ids}]}"""
        log = []
        def step(msg): log.append(msg)

        repo = payload.get("repo", "").strip()
        if "/" not in repo:
            return {"ok": False, "log": ["Repo must look like owner/name."]}

        # 1) Ensure the repo exists (create from this folder if missing).
        ok, _ = _run(["gh", "repo", "view", repo])
        if not ok:
            step(f"Creating repo {repo}…")
            ok, out = _run(["gh", "repo", "create", repo, "--public",
                            "--source", ".", "--remote", "origin", "--push"])
            if not ok:
                return {"ok": False, "log": [f"Could not create repo:\n{out}"]}
        step(f"Repo {repo} ready.")

        # 2) Build and write config.json (no secrets inside).
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
            "track_mode": "all",
            "only_course_ids": [],
            "ignore_course_ids": [],
            "ignore_name_patterns": payload.get("ignore_name_patterns", []),
            "notify_grades": True,
            "expiry_warn_days": payload.get("expiry_warn_days", 14),
            "schools": schools_cfg,
        }
        (REPO_ROOT / "config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8")
        step("Wrote config.json.")

        # 3) Commit & push config.
        _run(["git", "add", "config.json"])
        ok, out = _run(["git", "commit", "-m", "Update configuration via setup app"])
        if ok:
            ok, out = _run(["git", "push"])
            step("Pushed config to GitHub." if ok else f"Push note:\n{out}")
        else:
            step("Config unchanged (nothing to commit).")

        # 4) Set secrets.
        def set_secret(name, value):
            if not value:
                return
            ok, out = _run(["gh", "secret", "set", name, "--repo", repo,
                            "--body", value])
            step(f"Secret {name}: {'set' if ok else 'FAILED ' + out}")

        set_secret("TODOIST_TOKEN", payload.get("todoist", ""))
        set_secret("NTFY_TOPIC", payload.get("ntfy", ""))
        for s in payload["schools"]:
            set_secret(env_name_for(s["key"]), s.get("token", ""))

        # 5) Optional immediate run.
        if payload.get("run_now"):
            ok, out = _run(["gh", "workflow", "run", "canvas-sync.yml", "--repo", repo])
            step("Triggered a test run." if ok else f"Run trigger note:\n{out}")

        step("\nDone! Your sync is live and runs 3×/day on GitHub.")
        return {"ok": True, "log": log}


def main():
    api = Api()
    webview.create_window("Canvas Sync — Setup", str(UI_FILE),
                          js_api=api, width=900, height=820, min_size=(760, 640))
    webview.start()


if __name__ == "__main__":
    sys.exit(main())
