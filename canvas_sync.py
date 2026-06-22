#!/usr/bin/env python3
"""
Canvas -> Todoist sync (+ grade-posted push notifications).

Reads assignments and discussion post/reply status from one or more Canvas
(Instructure / bCourses) accounts and:

  1. Creates/updates Todoist tasks for anything still outstanding.
  2. Pushes a phone notification (via ntfy.sh) when new grades are posted.

No LLM is involved at runtime -- it's plain Python calling REST APIs:

    Canvas (read) --> this script --> Todoist (write) + ntfy (push)

Configuration lives in `config.json` (non-secret: school URLs, course
selection, timing). Secrets come from environment variables, which in GitHub
Actions are supplied from encrypted repository secrets:

    CANVAS_<KEY>_TOKEN   one per school, name set by config (e.g. CANVAS_WVM_TOKEN)
    TODOIST_TOKEN        Todoist API token (Settings -> Integrations -> Developer)
    NTFY_TOPIC           (optional) ntfy.sh topic for grade push notifications

See config.example.json and the README for details.
"""

import os
import re
import sys
import json
import time
import datetime as dt
from pathlib import Path
from typing import Optional

import requests

# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config.json"))
STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))

DEFAULT_CONFIG = {
    "lookahead_days": 14,
    "recent_overdue_days": 7,
    "track_mode": "all",          # "all" or "only"
    "only_course_ids": [],         # used when track_mode == "only"
    "ignore_course_ids": [],       # always excluded
    "ignore_name_patterns": [],    # course names containing any of these are skipped
    "notify_grades": True,
    "schools": [],                 # [{"key","base","token_env"}]
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if not cfg["schools"]:
        print("ERROR: no schools configured in config.json", file=sys.stderr)
        sys.exit(1)
    return cfg


DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

TODOIST_API = "https://api.todoist.com/rest/v2"
TODOIST_LABEL = "canvas"
MARKER_PREFIX = "canvas-sync-id:"

NOW = dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# HTTP helpers (retry + pagination)
# --------------------------------------------------------------------------

def _get(url: str, headers: dict, params: Optional[dict] = None) -> requests.Response:
    for attempt in range(4):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def canvas_paginated(base: str, path: str, token: str, params: Optional[dict] = None):
    headers = {"Authorization": f"Bearer {token}"}
    params = dict(params or {})
    params.setdefault("per_page", 100)
    url = f"{base}/api/v1{path}"
    while url:
        r = _get(url, headers, params)
        for item in r.json():
            yield item
        url = r.links.get("next", {}).get("url")
        params = None


def parse_ts(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def clean_course_name(name: str) -> str:
    return re.sub(r"\s*\(.*\)\s*$", "", name or "").strip()


# --------------------------------------------------------------------------
# Course selection
# --------------------------------------------------------------------------

def is_tracked(course: dict, cfg: dict) -> bool:
    cid = str(course.get("id"))
    name = course.get("name", "") or ""
    if cfg["track_mode"] == "only":
        return cid in {str(x) for x in cfg["only_course_ids"]}
    if cid in {str(x) for x in cfg["ignore_course_ids"]}:
        return False
    low = name.lower()
    return not any(pat.lower() in low for pat in cfg["ignore_name_patterns"])


# --------------------------------------------------------------------------
# Canvas reads
# --------------------------------------------------------------------------

def discussion_status(base, cid, tid, token, my_id):
    """Return (has_post, has_reply) for the current user in a discussion."""
    try:
        view = _get(f"{base}/api/v1/courses/{cid}/discussion_topics/{tid}/view",
                    {"Authorization": f"Bearer {token}"}).json()
    except Exception:
        return (False, False)

    has_post = has_reply = False

    def walk(entries):
        nonlocal has_post, has_reply
        for e in entries or []:
            if e.get("deleted"):
                continue
            if str(e.get("user_id")) == str(my_id):
                if e.get("parent_id"):
                    has_reply = True
                else:
                    has_post = True
            walk(e.get("replies"))

    walk(view.get("view"))
    return (has_post, has_reply)


def collect_school(school: dict, cfg: dict):
    """Return (outstanding_items, graded_items) for one school."""
    token = os.environ.get(school["token_env"], "")
    key = school["key"]
    if not token:
        print(f"[{key}] no token ({school['token_env']}); skipping")
        return [], []

    base = school["base"]
    outstanding, graded = [], []

    try:
        me = _get(f"{base}/api/v1/users/self",
                  {"Authorization": f"Bearer {token}"}).json()
        my_id = me["id"]
    except Exception as e:
        print(f"[{key}] auth failed: {e}; skipping")
        return [], []

    lookahead = NOW + dt.timedelta(days=cfg["lookahead_days"])
    overdue_floor = NOW - dt.timedelta(days=cfg["recent_overdue_days"])

    courses = [c for c in canvas_paginated(base, "/courses", token,
                                           {"enrollment_state": "active"})
               if is_tracked(c, cfg)]

    for c in courses:
        cid = c.get("id")
        cname = clean_course_name(c.get("name", "Course"))
        if not cid:
            continue

        # Discussions first, so we can skip their assignment "twins" below.
        try:
            topics = list(canvas_paginated(
                base, f"/courses/{cid}/discussion_topics", token))
        except Exception:
            topics = []
        discussion_assignment_ids = {
            t.get("assignment_id") for t in topics if t.get("assignment_id")
        }

        # Assignments (also the source for grade detection).
        try:
            assignments = list(canvas_paginated(
                base, f"/courses/{cid}/assignments", token,
                {"include[]": "submission"}))
        except Exception:
            assignments = []

        for a in assignments:
            sub = a.get("submission") or {}

            # --- grade detection (any graded assignment, any date) ---
            if (sub.get("workflow_state") == "graded"
                    and sub.get("score") is not None
                    and sub.get("graded_at")):
                graded.append({
                    "key": f"{key}-g{a['id']}",
                    "school": key,
                    "course": cname,
                    "title": a.get("name", "Assignment"),
                    "score": sub.get("score"),
                    "points": a.get("points_possible"),
                    "graded_at": sub.get("graded_at"),
                })

            # --- outstanding (assignment twin of a discussion handled below) ---
            if a.get("id") in discussion_assignment_ids:
                continue
            due = parse_ts(a.get("due_at"))
            if not due or not (overdue_floor <= due <= lookahead):
                continue
            submitted = bool(
                sub.get("submitted_at")
                or sub.get("workflow_state") in ("submitted", "graded",
                                                 "complete", "pending_review"))
            if submitted:
                continue
            outstanding.append({
                "key": f"{key}-a{a['id']}",
                "school": key, "course": cname,
                "title": a.get("name", "Assignment"), "due": due,
            })

        # Discussion post/reply tracking.
        for t in topics:
            due = parse_ts((t.get("assignment") or {}).get("due_at")
                           or t.get("lock_at"))
            if not due or not (overdue_floor <= due <= lookahead):
                continue
            has_post, has_reply = discussion_status(base, cid, t["id"], token, my_id)
            if has_post and has_reply:
                continue
            need = "Post + reply" if not has_post else "Reply to a classmate"
            outstanding.append({
                "key": f"{key}-d{t['id']}",
                "school": key, "course": cname,
                "title": f"{need}: {t.get('title', 'Discussion')}", "due": due,
            })

    print(f"[{key}] {len(outstanding)} outstanding, {len(graded)} graded total")
    return outstanding, graded


# --------------------------------------------------------------------------
# Todoist
# --------------------------------------------------------------------------

def todoist_existing_markers() -> set:
    headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
    r = _get(f"{TODOIST_API}/tasks", headers, {"label": TODOIST_LABEL})
    keys = set()
    for task in r.json():
        for line in (task.get("description") or "").splitlines():
            if line.startswith(MARKER_PREFIX):
                keys.add(line[len(MARKER_PREFIX):].strip())
    return keys


def todoist_create(item: dict):
    content = f"[{item['school']} - {item['course']}] {item['title']}"
    body = {
        "content": content,
        "description": f"{MARKER_PREFIX}{item['key']}",
        "due_date": item["due"].astimezone().strftime("%Y-%m-%d"),
        "labels": [TODOIST_LABEL],
    }
    if DRY_RUN:
        print(f"  DRY_RUN create: {content}  (due {body['due_date']})")
        return
    r = requests.post(f"{TODOIST_API}/tasks", json=body,
                      headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}, timeout=30)
    r.raise_for_status()
    print(f"  created: {content}")


# --------------------------------------------------------------------------
# ntfy push
# --------------------------------------------------------------------------

def ntfy_push(title: str, message: str):
    if DRY_RUN or not NTFY_TOPIC:
        print(f"  (push) {title} :: {message}")
        return
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                      data=message.encode("utf-8"),
                      headers={"Title": title, "Tags": "mortar_board"}, timeout=30)
    except requests.RequestException as e:
        print(f"  push failed: {e}")


# --------------------------------------------------------------------------
# State (grade-notification dedupe) -- persisted via Actions cache, not committed
# --------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    cfg = load_config()
    if not DRY_RUN and not TODOIST_TOKEN:
        print("ERROR: TODOIST_TOKEN not set (and DRY_RUN is off).", file=sys.stderr)
        return 1

    outstanding, graded = [], []
    for school in cfg["schools"]:
        o, g = collect_school(school, cfg)
        outstanding.extend(o)
        graded.extend(g)

    # ---- Todoist sync ----
    print(f"\nOutstanding total: {len(outstanding)}")
    existing = set() if DRY_RUN else todoist_existing_markers()
    created = 0
    for item in sorted(outstanding, key=lambda i: i["due"]):
        if item["key"] in existing:
            continue
        todoist_create(item)
        created += 1
    print(f"{created} new Todoist task(s)"
          f"{' (dry run)' if DRY_RUN else ''}.")

    # ---- Grade notifications ----
    if cfg["notify_grades"]:
        state = load_state()
        seen = set(state.get("graded_seen", []))
        first_run = "graded_seen" not in state
        new_grades = [g for g in graded if g["key"] not in seen]

        if first_run:
            # Baseline: record everything already graded, notify nothing.
            print(f"\nGrade baseline set ({len(graded)} already graded; "
                  f"no notifications on first run).")
        else:
            for g in sorted(new_grades, key=lambda x: x.get("graded_at") or ""):
                pts = f"/{g['points']:g}" if g.get("points") else ""
                msg = f"{g['title']} — {g['score']:g}{pts}  ({g['school']} · {g['course']})"
                ntfy_push("New grade posted", msg)
            print(f"\n{len(new_grades)} new grade notification(s)"
                  f"{' (dry run)' if DRY_RUN else ''}.")

        state["graded_seen"] = sorted(seen | {g["key"] for g in graded})
        if not DRY_RUN:
            save_state(state)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
