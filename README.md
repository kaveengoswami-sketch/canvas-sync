# Canvas Sync → Todoist (+ grade alerts)

Keep on top of your coursework automatically. This tool runs on **GitHub's
servers** on a schedule (your computer can be off), reads your **Canvas / bCourses**
account(s), and:

- 📝 Creates **Todoist tasks** for assignments due soon or recently overdue.
- 💬 Tracks **discussion posts *and* replies** — so it knows if you posted but
  still owe a reply to a classmate.
- 🔔 Sends a **phone push notification** (via [ntfy.sh](https://ntfy.sh)) when a
  new grade is posted.
- 🏫 Works with **multiple schools** at once.

**No AI runs at runtime.** It's plain Python hitting REST APIs:

```
Canvas (read) ──▶ this script (on GitHub) ──▶ Todoist (write) + ntfy (push)
```

Your data only ever travels between Canvas, GitHub's runner, Todoist, and ntfy.
No model, no third party sees your grades.

---

## How it works

| Component | Role |
|-----------|------|
| `canvas_sync.py` | All the logic — read Canvas, write Todoist, push grades |
| `config.json` | Non-secret settings: school URLs, course selection, timing |
| `.github/workflows/canvas-sync.yml` | The schedule (3×/day) + how to run it |
| GitHub **Secrets** | Your API tokens, encrypted — never in the code |
| GitHub **Actions cache** | Remembers which grades it already alerted you about |

Each Todoist task carries a hidden marker + the `canvas` label, so repeat runs
**update instead of duplicating**.

---

## Setup (about 15 minutes)

### 1. Use this repo
Click **Use this template** (or fork). A **private** fork is fine too — the code
contains no secrets either way.

### 2. Get your tokens
- **Canvas token (per school):** Canvas → profile picture → **Settings** →
  **Approved Integrations** → **+ New Access Token**. Set an expiry. Copy it.
- **Todoist token:** Todoist → **Settings → Integrations → Developer** → copy the
  **API token**.
- **(Optional) ntfy topic:** install the **ntfy** app (iOS/Android), and pick a
  long, random, secret topic name like `kg-grades-7f3q9z`. Subscribe to it in the
  app. That string *is* the password — anyone who knows it can see your alerts.

### 3. Add secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|--------|-------|
| `CANVAS_<KEY>_TOKEN` | one per school; the name must match `token_env` in `config.json` |
| `TODOIST_TOKEN` | your Todoist API token |
| `NTFY_TOPIC` | (optional) your ntfy topic for grade alerts |

> Example: the bundled `config.json` defines schools with `token_env` of
> `CANVAS_WVM_TOKEN` and `CANVAS_BERK_TOKEN`, so it expects two secrets by those
> names. Rename to match your own schools.

### 4. Edit `config.json`
```jsonc
{
  "lookahead_days": 14,        // how far ahead to pull upcoming work
  "recent_overdue_days": 7,    // how far back to still flag overdue work

  "track_mode": "all",         // "all" courses, or "only" a chosen list
  "only_course_ids": [],       // used when track_mode = "only"
  "ignore_course_ids": [],     // always skip these (by Canvas course id)
  "ignore_name_patterns": [    // skip courses whose name contains any of these
    "Financial Aid", "Orientation", "Advising"
  ],

  "notify_grades": true,

  "schools": [
    { "key": "WVM",      "base": "https://wvm.instructure.com",     "token_env": "CANVAS_WVM_TOKEN" },
    { "key": "Berkeley", "base": "https://bcourses.berkeley.edu",   "token_env": "CANVAS_BERK_TOKEN" }
  ]
}
```

**Choosing courses two ways:**
- *Subtractive* (default): `track_mode: "all"` and list junk courses (resource
  centers, orientation, advising) under `ignore_name_patterns`.
- *Additive*: `track_mode: "only"` and put the exact Canvas course IDs you care
  about in `only_course_ids`. (Find IDs in the course URL: `…/courses/12345`.)

### 5. Confirm the schedule
`.github/workflows/canvas-sync.yml` runs **3× daily**. The cron lines are in
**UTC** (GitHub doesn't do timezones or daylight saving), preset to roughly
**9 AM / 5 PM / 12 AM US Pacific** during summer. Adjust the numbers for your
timezone — `M H * * *`, where `H` is the UTC hour.

### 6. Test it
Repo → **Actions** → **Canvas sync** → **Run workflow**. Watch the log, check
Todoist. The first run sets a **grade baseline** (records existing grades but
sends no alerts), so you won't get spammed for old grades — alerts start on the
next newly-posted grade.

---

## Local testing (optional)

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your tokens
cp config.example.json config.json   # then edit it
DRY_RUN=1 python canvas_sync.py       # reads everything, writes nothing
```

---

## Privacy & safety

- **Tokens** live only in GitHub's encrypted secret store (write-only — not even
  you can read them back). The code is safe to make public.
- **Grades / course data** are never committed. Grade-alert state is kept in the
  Actions cache, not the repo.
- **Canvas tokens are powerful** (they can submit assignments). This tool only
  *reads* Canvas. Still, set an expiry and **revoke anytime** via Canvas →
  Settings → Approved Integrations.
- **ntfy topics are unauthenticated** — treat the topic name as a secret, or
  self-host ntfy / use [Pushover](https://pushover.net) if you want auth.

---

## Limitations
- Reply detection is *structural* — it confirms you replied to a classmate, but
  can't judge whether the reply is substantive enough for a rubric.
- GitHub cron can fire a few minutes late and doesn't track daylight saving.
- Group/section discussions may need per-group handling (best effort).

## Contributing
Issues and PRs welcome — more LMS providers, other notifiers (email, Slack,
Discord), or smarter scheduling would all be useful.

## License
[MIT](LICENSE)
