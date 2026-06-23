#!/usr/bin/env python3
"""
Interactive setup helper for canvas-sync.

Run this locally to pick which courses to track (per school) from a live menu,
and to record each token's expiry date -- no hand-editing course IDs.

    python configure.py

It reads your tokens from the environment (or a local .env file), fetches your
active courses from each school in config.json, lets you toggle which to track,
optionally records token expiry dates, and writes the result back to config.json.

This is a local convenience tool; it is never run by GitHub Actions.
"""

import os
import sys
import json
import datetime as dt
from pathlib import Path

import requests

CONFIG_PATH = Path("config.json")
EXAMPLE_PATH = Path("config.example.json")


def load_dotenv():
    """Minimal .env loader (no extra dependency)."""
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def fetch_courses(base, token):
    out, url = [], f"{base}/api/v1/courses"
    params = {"enrollment_state": "active", "per_page": 100}
    headers = {"Authorization": f"Bearer {token}"}
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        out.extend(r.json())
        url = r.links.get("next", {}).get("url")
        params = None
    return [c for c in out if c.get("id") and c.get("name")]


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def select_courses(school):
    token = os.environ.get(school["token_env"], "")
    if not token:
        print(f"\n[{school['key']}] no token in env ({school['token_env']}); "
              f"skipping course selection.")
        return

    print(f"\n=== {school['key']} ({school['base']}) ===")
    try:
        courses = fetch_courses(school["base"], token)
    except Exception as e:
        print(f"  could not fetch courses: {e}")
        return

    already = {str(x) for x in school.get("only_course_ids", [])}
    mode = school.get("track_mode", "all")
    for i, c in enumerate(courses, 1):
        cur = "all" if mode != "only" else ("on" if str(c["id"]) in already else "off")
        mark = "[x]" if (mode != "only" or str(c["id"]) in already) else "[ ]"
        print(f"  {i:2}. {mark} {c['name']}  (id {c['id']})")

    print("\nEnter numbers to TRACK (e.g. '1,3,5'), 'a' for all, or blank to keep current.")
    choice = ask("> ").lower()
    if not choice:
        return
    if choice == "a":
        school.pop("track_mode", None)
        school.pop("only_course_ids", None)
        print(f"  {school['key']}: tracking ALL courses (minus global ignore patterns).")
        return
    try:
        picks = {int(x) for x in choice.replace(" ", "").split(",") if x}
    except ValueError:
        print("  unrecognized input; left unchanged.")
        return
    ids = [courses[i - 1]["id"] for i in sorted(picks) if 1 <= i <= len(courses)]
    school["track_mode"] = "only"
    school["only_course_ids"] = ids
    chosen = [courses[i - 1]["name"] for i in sorted(picks) if 1 <= i <= len(courses)]
    print(f"  {school['key']}: tracking {len(ids)} course(s): " + ", ".join(chosen))


def set_expiry(school):
    cur = school.get("token_expires", "")
    val = ask(f"  {school['key']} token expiry date YYYY-MM-DD "
              f"[{cur or 'none'}] (blank=keep, '-'=clear): ")
    if not val:
        return
    if val == "-":
        school.pop("token_expires", None)
        print("    cleared.")
        return
    try:
        dt.date.fromisoformat(val)
    except ValueError:
        print("    not a valid date; left unchanged.")
        return
    school["token_expires"] = val


def main():
    load_dotenv()
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    elif EXAMPLE_PATH.exists():
        cfg = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        print("No config.json found; starting from config.example.json.")
    else:
        print("No config.json or config.example.json found.", file=sys.stderr)
        return 1

    if not cfg.get("schools"):
        print("config has no schools; add at least one first.", file=sys.stderr)
        return 1

    print("== canvas-sync setup ==")
    print("Pick the courses to track for each school.")
    for school in cfg["schools"]:
        select_courses(school)

    if ask("\nRecord token expiry dates for reminders? [y/N]: ").lower() == "y":
        for school in cfg["schools"]:
            set_expiry(school)

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved {CONFIG_PATH}. Commit & push it so GitHub Actions uses your choices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
