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

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

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
    "notify_grades": True,         # push when a grade is posted or changes
    "notify_due": True,            # push the day before and day of a due date
    "expiry_warn_days": 14,        # start warning this many days before a token expires
    "todoist_project_map": {},     # {"<course_id>": "<todoist_project_id>"}
    "timezone": "",                # IANA name, e.g. "America/Los_Angeles" (DST-correct)
    "utc_offset_hours": 0,         # fallback when "timezone" is unset, e.g. -7 for US Pacific
    "schools": [],                 # [{"key","base","token_env","token_expires"}]
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

TODOIST_API = "https://api.todoist.com/api/v1"
TODOIST_LABEL = "canvas"
MARKER_PREFIX = "canvas-sync-id:"

NOW = dt.datetime.now(dt.timezone.utc)
UTC_OFFSET_HOURS = 0  # fallback when no IANA timezone is configured
LOCAL_TZ: Optional[dt.tzinfo] = None  # set from config in main(); preferred over the fixed offset


def local_date(when: dt.datetime) -> dt.date:
    """The calendar date of a UTC datetime in the user's local timezone.

    Prefers a configured IANA timezone (which tracks daylight saving correctly);
    falls back to a fixed UTC offset when none is set or it can't be loaded."""
    if LOCAL_TZ is not None:
        return when.astimezone(LOCAL_TZ).date()
    return (when + dt.timedelta(hours=UTC_OFFSET_HOURS)).date()


# --------------------------------------------------------------------------
# HTTP helpers (retry + pagination)
# --------------------------------------------------------------------------

def _get(url: str, headers: dict, params: Optional[dict] = None) -> requests.Response:
    for attempt in range(4):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 429 and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()  # on the final attempt a 429 surfaces here too
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def canvas_paginated(base: str, path: str, token: str, params: Optional[dict] = None,
                     max_pages: int = 200):
    headers = {"Authorization": f"Bearer {token}"}
    params = dict(params or {})
    params.setdefault("per_page", 100)
    url = f"{base}/api/v1{path}"
    page = 0
    while url:
        page += 1
        if page > max_pages:
            print(f"WARNING: canvas_paginated hit max_pages ({max_pages}) for {path}")
            break
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

def effective_selection(school: dict, cfg: dict) -> dict:
    """Course-selection settings for a school, with per-school overrides
    falling back to the global config."""
    return {
        k: school.get(k, cfg[k])
        for k in ("track_mode", "only_course_ids",
                  "ignore_course_ids", "ignore_name_patterns")
    }


def is_tracked(course: dict, sel: dict) -> bool:
    cid = str(course.get("id"))
    name = course.get("name", "") or ""
    if sel["track_mode"] == "only":
        return cid in {str(x) for x in sel["only_course_ids"]}
    if cid in {str(x) for x in sel["ignore_course_ids"]}:
        return False
    low = name.lower()
    return not any(pat.lower() in low for pat in sel["ignore_name_patterns"])


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

    sel = effective_selection(school, cfg)
    courses = [c for c in canvas_paginated(base, "/courses", token,
                                           {"enrollment_state": "active"})
               if is_tracked(c, sel)]

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
        assignments_by_id = {a.get("id"): a for a in assignments}

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
                "school": key, "course": cname, "course_id": str(cid),
                "title": a.get("name", "Assignment"), "due": due,
            })

        # Discussion post/reply tracking.
        for t in topics:
            # A graded discussion's due date lives on its linked assignment,
            # which the discussion_topics list endpoint does NOT embed. Pull it
            # from the assignments we already fetched for this course (falling
            # back to any embedded assignment, then the topic's own lock date).
            twin = assignments_by_id.get(t.get("assignment_id"))
            due = parse_ts((twin or {}).get("due_at")
                           or (t.get("assignment") or {}).get("due_at")
                           or t.get("lock_at"))
            if not due or not (overdue_floor <= due <= lookahead):
                continue
            has_post, has_reply = discussion_status(base, cid, t["id"], token, my_id)
            if has_post and has_reply:
                continue
            need = "Post + reply" if not has_post else "Reply to a classmate"
            outstanding.append({
                "key": f"{key}-d{t['id']}",
                "school": key, "course": cname, "course_id": str(cid),
                "title": f"{need}: {t.get('title', 'Discussion')}", "due": due,
            })

    print(f"[{key}] {len(outstanding)} outstanding, {len(graded)} graded total")
    return outstanding, graded


# --------------------------------------------------------------------------
# Todoist
# --------------------------------------------------------------------------

def todoist_existing_markers() -> set:
    """All sync-markers already present on our Todoist tasks (paginated v1 API)."""
    headers = {"Authorization": f"Bearer {TODOIST_TOKEN}"}
    keys = set()
    cursor = None
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _get(f"{TODOIST_API}/tasks", headers, params).json()
        # Warn if the response structure is unexpected.
        if not isinstance(data, (dict, list)):
            print(f"WARNING: todoist_existing_markers got unexpected response "
                  f"type {type(data).__name__}; returning empty set")
            break
        # v1 returns {"results": [...], "next_cursor": ...}; tolerate a bare list too
        tasks = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(tasks, list):
            print(f"WARNING: todoist_existing_markers expected a list of tasks "
                  f"but got {type(tasks).__name__}; returning empty set")
            break
        for task in tasks:
            for line in (task.get("description") or "").splitlines():
                if line.startswith(MARKER_PREFIX):
                    keys.add(line[len(MARKER_PREFIX):].strip())
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return keys


def todoist_create(item: dict, project_id: Optional[str] = None):
    content = f"[{item['school']} - {item['course']}] {item['title']}"
    body = {
        "content": content,
        "description": f"{MARKER_PREFIX}{item['key']}",
        "due_date": local_date(item["due"]).strftime("%Y-%m-%d"),
        "labels": [TODOIST_LABEL],
    }
    if project_id:
        body["project_id"] = str(project_id)
    if DRY_RUN:
        tag = f" -> project {project_id}" if project_id else ""
        print(f"  DRY_RUN create: {content}  (due {body['due_date']}){tag}")
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

def notify_due_soon(outstanding: list, state: dict):
    """Push the day before and the day of each outstanding item's due date.
    Each (item, bucket) fires at most once, tracked in state."""
    fired = set(state.get("due_fired", []))
    today = local_date(NOW)
    for item in sorted(outstanding, key=lambda i: i["due"]):
        days = (local_date(item["due"]) - today).days
        bucket = "today" if days == 0 else ("tomorrow" if days == 1 else None)
        if not bucket:
            continue
        tok = f"{item['key']}:{bucket}"
        if tok in fired:
            continue
        when = "due TODAY" if bucket == "today" else "due tomorrow"
        ntfy_push(f"Assignment {when}",
                  f"{item['title']}  ({item['school']} · {item['course']})")
        fired.add(tok)
    state["due_fired"] = sorted(fired)


def check_token_expiries(cfg: dict, state: dict):
    """Push a warning as each Canvas token nears its configured expiry date.
    Fires at most once per threshold (14/7/3/1 days) per school, tracked in state."""
    warn = int(cfg.get("expiry_warn_days", 14))
    ladder = [d for d in (14, 7, 3, 1) if d <= warn] or [warn]
    today = local_date(NOW)
    fired = state.setdefault("expiry_fired", {})

    for school in cfg["schools"]:
        exp = school.get("token_expires")
        if not exp:
            continue
        try:
            exp_date = dt.date.fromisoformat(exp)
        except ValueError:
            print(f"[{school['key']}] bad token_expires '{exp}' (use YYYY-MM-DD)")
            continue

        days_left = (exp_date - today).days
        # Key the fired-state by (school, expiry date) so that rotating the
        # token and setting a new token_expires re-arms the reminders instead
        # of staying suppressed by the previous date's already-fired thresholds.
        fkey = f"{school['key']}@{exp}"
        done = set(fired.get(fkey, []))

        if days_left < 0:
            if "expired" not in done:
                ntfy_push("Canvas token EXPIRED",
                          f"{school['key']} Canvas token expired on {exp}. "
                          f"Generate a new one and update the GitHub secret.")
                done.add("expired")
        else:
            applicable = [t for t in ladder if days_left <= t and t not in done]
            if applicable:
                ntfy_push("Canvas token expiring soon",
                          f"{school['key']} Canvas token expires in {days_left} "
                          f"day(s) on {exp}. Regenerate it and update the secret.")
                done.update(applicable)  # collapse backlog; one ping, not several

        # Drop stale entries from earlier expiry dates for this school so the
        # state file doesn't accumulate dead keys after each token rotation.
        for k in [k for k in fired
                  if k.startswith(school["key"] + "@") and k != fkey]:
            del fired[k]
        fired[fkey] = sorted(done, key=str)


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
    global UTC_OFFSET_HOURS, LOCAL_TZ
    UTC_OFFSET_HOURS = int(cfg.get("utc_offset_hours", 0))
    tz_name = (cfg.get("timezone") or "").strip()
    if tz_name and ZoneInfo is not None:
        try:
            LOCAL_TZ = ZoneInfo(tz_name)
        except Exception:
            print(f"WARNING: unknown timezone '{tz_name}'; falling back to "
                  f"utc_offset_hours={UTC_OFFSET_HOURS}", file=sys.stderr)
            LOCAL_TZ = None
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
    project_map = cfg.get("todoist_project_map", {}) or {}
    existing = set() if DRY_RUN else todoist_existing_markers()
    created = 0
    for item in sorted(outstanding, key=lambda i: i["due"]):
        if item["key"] in existing:
            continue
        todoist_create(item, project_map.get(item.get("course_id")))
        created += 1
    print(f"{created} new Todoist task(s)"
          f"{' (dry run)' if DRY_RUN else ''}.")

    # ---- Notifications (grades + token expiry) ----
    state = load_state()

    if cfg["notify_grades"]:
        prev = state.get("grades")            # {key: score}
        first_run = prev is None
        prev = prev or {}
        pushes = 0
        def fmt_score(s):
            try: return f"{s:g}"
            except (ValueError, TypeError): return str(s)
        for g in sorted(graded, key=lambda x: x.get("graded_at") or ""):
            score = g["score"]
            pts = f"/{fmt_score(g['points'])}" if g.get("points") else ""
            where = f"({g['school']} · {g['course']})"
            if first_run:
                continue                       # baseline only, no spam
            if g["key"] not in prev:
                ntfy_push("New grade posted",
                          f"{g['title']} — {fmt_score(score)}{pts}  {where}")
                pushes += 1
            elif prev[g["key"]] != score:
                ntfy_push("Grade changed",
                          f"{g['title']} — {fmt_score(prev[g['key']])} → {fmt_score(score)}{pts}  {where}")
                pushes += 1
        # record current scores (keep any past keys no longer returned)
        merged = dict(prev)
        merged.update({g["key"]: g["score"] for g in graded})
        state["grades"] = merged
        if first_run:
            print(f"\nGrade baseline set ({len(graded)} recorded; no first-run alerts).")
        else:
            print(f"\n{pushes} grade notification(s)"
                  f"{' (dry run)' if DRY_RUN else ''}.")

    if cfg.get("notify_due", True):
        notify_due_soon(outstanding, state)

    check_token_expiries(cfg, state)

    if not DRY_RUN:
        save_state(state)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
