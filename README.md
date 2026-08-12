```
╔══════════════════════════════════════════════════════╗
║                                                      ║
║  ███████╗ ███████╗  █████╗ ███╗   ██╗                ║
║  ██╔════╝ ██╔════╝ ██╔══██╗████╗  ██║                ║
║  ███████╗ █████╗   ███████║██╔██╗ ██║   ████╗        ║
║  ╚════██║ ██╔══╝   ██╔══██║██║╚██╗██║   ╚═══╝        ║
║  ███████║ ███████╗ ██║  ██║██║ ╚████║                ║
║  ╚══════╝ ╚══════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝-               ║
║                                                      ║
║   ██╗ ██████╗  ███████╗ ██████╗  ██╗  █████╗         ║
║   ██║ ██╔══██╗ ██╔════╝ ██╔══██╗ ██║ ██╔══██╗        ║
║   ██║ ██████╔╝ █████╗   ██║  ██║ ██║ ███████║        ║
║   ██║ ██╔═══╝  ██╔══╝   ██║  ██║ ██║ ██╔══██║        ║
║   ██║ ██║      ███████╗ ██████╔╝ ██║ ██║  ██║        ║
║   ╚═╝ ╚═╝      ╚══════╝ ╚═════╝  ╚═╝ ╚═╝  ╚═╝        ║
║                                                      ║
║   personal knowledge base · daily pipeline           ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
```

# Obsidian Vault — Inbox & Pipeline

Scripts live in:
`~/Library/CloudStorage/GoogleDrive-.../Sean/Code/ai_code/seanipedia/`

Vault lives in:
`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/vault/`

🟢 = calls an external API (Claude) — costs money / requires internet
⏰ = runs automatically via launchd
👤 = you run it manually

---

## Setup

```bash
pip3 install -r requirements.txt
cp .env.example .env
```

Fill in `.env` — every value marked "yours" below is personal to you, never
committed:

| Variable | Purpose |
|---|---|
| `VAULT_PATH` | Absolute path to your Obsidian vault (yours) |
| `ANTHROPIC_API_KEY` | console.anthropic.com/settings/keys (yours) |
| `GMAIL_APP_PASSWORD` | For `email_scan.py` — see the comment above it in `.env.example` (yours) |
| `MAIL_ACCOUNT` | The inbox `email_scan.py` scans (yours) |
| `SENDER_ADDRS` | Comma-separated addresses treated as "you" for self-sent notes (yours) |
| `STUDY_CALENDAR` | macOS Calendar name for the Daily Study feature; leave blank to skip it (yours) |
| `AZURE_CLIENT_ID` | Only needed if you use `resynthesize.py`'s Microsoft Graph features |
| `NATSEC_DB_PATH` | Only needed if you also run the sibling `natsec_jobs` project |

`git_autocommit.sh` is personal automation for one specific multi-repo layout — see
the comment at the top of that file before using it as-is.

---

## How to Use

### Daily note
> **Created by:** `daily.py --generate` (via `com.seang.daily-generate`) at **6:00am daily**

1. **Open today's note** — it lives at `_inbox/Today.md`. Pin a shortcut or use Obsidian's Quick Open to jump to it.
2. **Write throughout the day** — fill in `## Notes`, `## Quotes`, `## Daily Study`, `## Reflections`, etc. as things come up. When a study topic is scheduled, `## Daily Study` has a `- [ ] Completed` checkbox — tick it off once you've actually done the work so it counts in the weekly report.
3. **Do nothing at 3:00am** — `daily.py --parse` reads `Today.md`, routes each section to `_outbox/` using the date from frontmatter, and deletes the file.

### Email notes on the go
Email yourself at your configured `MAIL_ACCOUNT` (see **Setup** below) with subject `For Notes: <topic>`. The noon `email_scan.py` job picks it up and appends it to today's `_inbox/Today.md` under `## Notes` (which `--parse` routes to `_outbox/Notes/YYYYMMDD.md` at 3am, like any other section). Past-dated emails are written straight to `_outbox/Notes/` since that day's note is gone.

### Weekly report
A report covering one completed **Sunday→Saturday** week lands in `vault/_weekly reports/YYYYMMDD.md`, named by the **Sunday after** that week — i.e. your review day (the May 31–Jun 6 week is saved as `20260607.md`). It runs on your Sunday review day for the week that just ended, and **excludes the current Sunday** so work you do on review day counts toward the next week. The report draws on both the week's `_outbox/` output and the daily `_journal/` practice (Calendar of Wisdom, Al-Anon, Sententiae Antiquae, Words). `__wiki/` pages are updated in the same run (from `_outbox/` only). No week is ever skipped: if the laptop was asleep the report is caught up later (one report per missed week), and even a quiet week with no activity gets a short placeholder so the cadence stays continuous.

### If something went wrong
```bash
cd ~/Library/CloudStorage/GoogleDrive-.../Sean/Code/ai_code/seanipedia/

python3 daily.py --parse --date 2026-04-28   # re-parse a specific date's inbox file
python3 daily.py --generate                  # manually generate today's note
python3 daily.py --refresh                   # re-populate Daily Study if Calendar was slow
python3 email_scan.py --dry-run              # preview email scan without writing
python3 weekly_report.py --dry-run           # preview weekly report without writing
```

---

## Data Flow

```
  daily_brief_v2.py 🟢 ⏰ (5pm)   email_scan.py ⏰ (noon)   scrape_jobs.py ⏰ (6:15am)   daily.py --generate ⏰ (6am)
         │                            │                         │                               │
         │ ## Daily Intelligence      │ ## Notes                │ ## Jobs                       ▼
         │ Brief                      │ (For Notes emails)      │                     _inbox/Today.md
         └────────────────────────────┴─────────────────────────┴────────────────────────►  (daily workspace)
                                               │
                              daily.py --parse ⏰ (3am)
                                               │
                    ┌──────────┬──────────┬────┴─────┬──────────┬──────────┐
                    ▼          ▼          ▼           ▼          ▼          ▼
               _journal/  _outbox/   _outbox/    _outbox/   _outbox/   _outbox/
               MM-DD.md   Quotes/    Daily Study/ Therapy/  Reflec-    Notes/
             (Calendar,   YYYYMMDD   YYYYMMDD     YYYYMMDD  tions/     YYYYMMDD
              Al-Anon,                                       YYYYMMDD
              Sententiae,
              Words)
                    │
                    └──► _words.md  (Words entries marked "(new)")

  ## Daily Intelligence Brief ──► archive/Daily Intelligence Brief/YYYYMMDD.md
                                   (permanent copy; NOT _outbox — never feeds
                                   __wiki/ or _weekly reports/)

  ┌─────────────────────────────────────────────────────────────────┐
  │  _outbox/ (synthesized+archived) + _journal/ (read for report only)│
  └──────────────────────┬──────────────────────────────────────────┘
                         │
             weekly_report.py  🟢 ⏰ (daily 9am; reports completed Sun–Sat weeks)
                    │           │           │
                    ▼           ▼           ▼
                 __wiki/   _weekly      macOS
               (updated)   reports/   notification
                           YYYYMMDD    🔔

  scrape_jobs.py also sends macOS notification 🔔 after each daily run (6:15am)
```

### Daily note sections → destinations

| Section | Destination |
|---|---|
| ## A Calendar of Wisdom | `_journal/MM-DD.md` |
| ## Al-Anon | `_journal/MM-DD.md` |
| ## Sententiae Antiquae | `_journal/MM-DD.md` |
| ## Words | `_journal/MM-DD.md` (+ new words → `_words.md`) |
| ## Quotes | `_outbox/Quotes/YYYYMMDD.md` |
| ## Daily Study | `_outbox/Daily Study/YYYYMMDD.md` — when a topic is scheduled, the section carries a `- [ ] Completed` box; check it off once done. Every day is archived, but `weekly_report.py` only credits **checked** days in the wiki/report (unchecked = assigned-but-skipped; legacy days with no box still count). |
| ## Notes | `_outbox/Notes/YYYYMMDD.md` |
| ## Reflections | `_outbox/Reflections/YYYYMMDD.md` (verbatim) |
| ## Therapy | `_outbox/Therapy/YYYYMMDD.md` |
| ## Daily Intelligence Brief | written by `daily_brief_v2.py` at 5 PM; `--parse` copies it to `archive/Daily Intelligence Brief/YYYYMMDD.md` — not `_outbox`, never feeds wiki/weekly reports |
| ## Jobs | written by `scrape_jobs.py` at 6:15 AM — not routed by `--parse` |

Note: `_journal/` (the four perennial Calendar-of-Wisdom sections) is **read into** the weekly report for context — the week's Sententiae Antiquae and new Words land in *Quotes & Reading*, and the Calendar-of-Wisdom/Al-Anon readings inform *Personal Insights*. But `_journal/` is **never archived or fed to wiki synthesis**: it remains a permanent personal record, separate from the `_outbox/` staging layer. Only `_outbox/` content is synthesized into `__wiki/` and moved to `archive/`.

---

## Scripts

### Daily (automated via launchd)

| Script | Purpose | Schedule |
|---|---|---|
| `daily.py --generate` ⏰ | Creates today's `_inbox/Today.md` with all sections pre-filled from calendar. | 6:00am daily |
| `daily.py --refresh` ⏰ | Re-populates `## Daily Study` if the 6am run fired before Calendar synced. | 8:00am daily |
| `daily.py --parse` ⏰ | Routes each section to `_journal/` or `_outbox/`; appends new Words to `_words.md`; deletes the inbox file. | 3:00am daily |
| `email_scan.py` ⏰ | Scans Mail for unread "For Notes" messages; appends body to today's `Today.md` `## Notes` section (or `_outbox/Notes/YYYYMMDD.md` for past-dated / missing-note cases); marks read. | noon daily |
| `daily_brief_v2.py` 🟢 ⏰ | Fetches ND alumni inbox (everything unread since the last brief — a time window, not a keyword pre-filter), routes each email with Claude, then writes ONE trend-synthesis narrative per topic (not one block per email), saves `output/brief_YYYY-MM-DD.md`, inserts `## Daily Intelligence Brief` into `Today.md`. Marks read only what it verifiably summarized; off-domain and personal mail is left unread on purpose. See **Daily Brief** below for the full model. | 5:00pm daily |

### Weekly (automated via launchd)

| Script | Purpose | Schedule |
|---|---|---|
| `weekly_report.py` 🟢 ⏰ | Reports each completed **Sun–Sat** week that doesn't yet have a report: reads that week's `_outbox/`, updates `__wiki/`, generates the report, saves to `_weekly reports/`, archives processed files. Idempotent — tracks the last reported week in `logs/.last_reported_week`, so it's safe to run any day, any number of times, and catches up missed weeks (one report each). | daily 9am (+ wake/login) |

```bash
python3 weekly_report.py                   # report all completed-but-missing weeks (idempotent)
python3 weekly_report.py --week 2026-05-31  # force one specific week (any date within it)
python3 weekly_report.py --no-wiki         # skip wiki updates, report only
python3 weekly_report.py --dry-run         # preview without writing
```

---

## Automation (launchd)

| Job | Script | Schedule | Log |
|---|---|---|---|
| `com.seang.daily-generate` | `daily.py --generate` ⏰ | 6:00am daily | `~/Library/Logs/daily-generate.log` |
| `com.seang.daily-refresh` | `daily.py --refresh` ⏰ | 8:00am daily | `~/Library/Logs/daily-refresh.log` |
| `com.seang.daily-parse` | `daily.py --parse` ⏰ | 3:00am daily | `~/Library/Logs/daily-parse.log` |
| `com.seang.email-scan` | `email_scan.py` ⏰ | noon daily | `~/Library/Logs/email-scan.log` |
| `com.seang.daily-brief` | `~/scripts/run_daily_brief.sh` → `daily_brief_v2.py` 🟢 ⏰ | 5:00pm daily | `~/scripts/daily_brief_launchd.log` |
| `com.seang.weekly-report` | `run_weekly.sh` → `weekly_report.py` 🟢 ⏰ | daily 9am + wake + login | `~/Library/Logs/weekly-report.log` |

All jobs fire on wake if the machine was asleep at the scheduled time.

To reload a job after editing its plist:
```bash
launchctl unload ~/Library/LaunchAgents/com.seang.<name>.plist
launchctl load   ~/Library/LaunchAgents/com.seang.<name>.plist
```

---

## Daily Brief — Setup & Credentials

`daily_brief_v2.py` lives in the sibling project `ai_code/daily_brief/` (its own `.env`, `credentials/`, and `output/`) and is what actually runs — `daily_brief.py` (the original, one-summary-block-per-email version) is kept in the same folder only as shared plumbing: v2 does `import daily_brief as db` for the Gmail/Graph auth, fetch, and mark-read helpers, plus the `TRUSTED_SENDERS` list. Don't edit fetch/auth logic in v2 expecting it to be self-contained — check `daily_brief.py` first.

**The model (trend-synthesis, replaced the per-email version 2026-07-28):**
1. **Fetch** — everything unread since the last brief ran (a TIME window, not a fixed count or a keyword pre-filter — a busy day just gets fully covered, however many emails that is).
2. **Route** (Claude, cheap/batched) — for each email: relevant? which of the 5 topics (or "Other")? is it announcing an event? is it personal/professional correspondence rather than a subscribed publication?
3. **Synthesize** (Claude, one call per topic) — ONE trend-focused narrative per topic aggregating all of today's emails on it, plus the last few days' coverage for "building / continuing / fading" context — not a per-email block. Every relevant email is guaranteed a mention: either cited inline or, if the synthesis didn't work it in, listed under an auto-appended "### Also noted".
4. **Assemble** — topic sections (each with a per-subsection `- [ ] Reviewed` checkbox that keeps re-appearing in tomorrow's brief until checked), an events list, and two audit sections: **Coverage** (every email routed non-relevant, so the filter itself is checkable) and **personal/professional** mail (never summarized, never marked read — it's your own correspondence to answer).
5. **Mark read** — ONLY email that's actually verifiable in the brief just written (relevant → synthesized). Off-domain (Coverage) and personal mail are deliberately left unread — they're your own inbox to-do list, the brief must not touch them.

Two more sections can appear when relevant:
- **"⚠ Flagged — Left Unread (Safety Check)"** — a structural belt-and-suspenders check: if an email was ever judged relevant but somehow didn't make it into a synthesized section, it's listed here and explicitly NOT marked read, rather than trusting the normal-case guarantee blindly.
- **"Read Before This Run (Trusted Sources)"** — reading an email directly in Gmail (opening it yourself) marks it read before the script ever sees it, so `is:unread` silently skips it — it's never routed, never in Coverage, never anywhere. This section catches the curated `TRUSTED_SENDERS` subset of that blind spot (WOTR, TLDR AI, Jamestown, etc.) with a cheap sender-match scan, so a trusted newsletter you happened to open yourself doesn't just vanish with zero trace. Ordinary already-read inbox traffic (shopping receipts, personal correspondence) is expected and not surfaced.

**Catching up on already-read mail:** `python3 daily_brief_v2.py --include-read --after 2026/08/11` re-scans a window INCLUDING mail you already read yourself (not just what's still unread) and synthesizes it same as normal — but a retrospective run like this never marks anything read, since it's re-reading mail that's already been handled. Add `--out-name catchup-aug11 --no-vault` to write to a standalone file instead of overwriting today's dated brief / touching `Today.md`.

Sample output:

```
# Daily Intelligence Brief — August 11, 2026
*Generated 17:01 | 25 relevant of 55 emails spanning Aug 10–Aug 11 · trend synthesis*

## Artificial Intelligence & Emerging Technology
The dual trend of rapid AI innovation and escalating concerns regarding safety,
control, and regulatory oversight continues to intensify...

### AI Safety, Autonomy, and Cybersecurity Incidents
- [ ] Reviewed
Concerns over AI models exhibiting unexpected autonomy are building
significantly, continuing a trend from earlier this week... [POLITICO's Digital
Future Daily](https://mail.google.com/...).
...
## Coverage
<details><summary>27 email(s) filtered as off-domain (left unread, not surfaced)</summary>
...
```

**Useful flags** (`python3 daily_brief_v2.py --help` for the full list):

| Flag | Use |
|---|---|
| `--dry-run` | Preview only — writes `output/brief_preview_*.md`, marks nothing read. |
| `--limit N` | Safety cap on messages fetched (default 400); the daily window is time-based, not count-based, so this is a backstop, not the normal control. |
| `--backlog` | Big one-time catch-up (raises the cap, sub-batches synthesis); pair with `--mark-read` to actually clear it. |
| `--before` / `--after YYYY/MM/DD` | Explicit Gmail-syntax date window instead of "since the last brief." |
| `--include-read` | Retrospective — includes already-read mail, never marks anything read. |
| `--out-name NAME` | Write to `output/brief_NAME.md` instead of today's date. |
| `--no-vault` | Write the file only; skip the `Today.md` insert. |
| `--no-carry` | Skip carrying forward unreviewed sections from the last brief. |
| `--title` | Override the brief's H1 title. |

One-time setup configures four things: Python packages, a Google Cloud app (Gmail/ND), a Microsoft Azure app (Graph/JHU), and the Anthropic key.

```bash
cd ~/Library/CloudStorage/GoogleDrive-.../Sean/Code/ai_code/daily_brief
pip3 install -r requirements.txt
```

**Step 1 — Gmail API (Notre Dame, alumni.nd.edu):** at [console.cloud.google.com](https://console.cloud.google.com): New Project `daily-brief` → **APIs & Services → Library** → enable **Gmail API** → **OAuth consent screen** (External; add your `alumni.nd.edu` as a Test User) → **Credentials → Create OAuth client ID → Desktop app**. Open the client and copy **Client ID** (`...apps.googleusercontent.com`) and **Client Secret** (`GOCSPX-...`) into `.env` as `ND_GMAIL_CLIENT_ID` / `ND_GMAIL_CLIENT_SECRET`. No JSON download needed.

**Step 2 — Microsoft Graph API (Johns Hopkins, alumni.jh.edu):** at [portal.azure.com](https://portal.azure.com) (personal Microsoft account, *not* JHU): **App registrations → New registration** — name `daily-brief`, "Accounts in any org directory and personal Microsoft accounts", Redirect URI **Public client/native** = `http://localhost`. Copy the **Application (client) ID**. **API permissions → Add → Microsoft Graph → Delegated → `Mail.ReadWrite`**, then Grant admin consent if the button appears. No client secret needed.

**Step 3 — `.env`:** `cp .env.example .env`, then fill in:
```
ANTHROPIC_API_KEY=sk-ant-...          # console.anthropic.com/settings/keys
ND_EMAIL_ADDRESS=yourname@alumni.nd.edu
JHU_EMAIL_ADDRESS=yourname@alumni.jh.edu
JHU_AZURE_CLIENT_ID=paste-from-step-2
JHU_AZURE_TENANT_ID=common
JHU_AZURE_CLIENT_SECRET=             # leave blank
VAULT_TODAY_PATH=/abs/path/to/vault/_inbox/Today.md
```

**Step 4 — one-time auth:** `python3 setup_auth.py` opens two browser windows (Google + Microsoft); sign into each with the matching alumni account. Tokens are saved to `credentials/` and refresh automatically.

**Run / behavior:** `python3 daily_brief_v2.py`. The brief is inserted before `## Jobs` (or after `## Therapy`); re-running the same day REPLACES the whole `## Daily Intelligence Brief` section rather than merging into it, so a manual re-run the same day overwrites, it doesn't append. launchd runs it at 5pm via `~/scripts/run_daily_brief.sh`, which first runs `email_scan.py` (serializing Today.md writes) then the brief — a bash wrapper is required because `/bin/bash` needs Full Disk Access to reach the Google Drive path. (`daily_brief.py`, the v1 script this replaced, had its own separate up-to-7-day catch-up loop; v2 doesn't need one — its normal fetch window already covers everything unread since the last brief, however many days that spans.)

**Troubleshooting:**
- **Gmail auth fails / token expired:** delete `credentials/gmail_token.json`, re-run `setup_auth.py`.
- **JHU auth fails:** delete `credentials/jhu_token.json`, re-run `setup_auth.py`. If sign-in is blocked, the Azure app may need consent — open `https://login.microsoftonline.com/common/adminconsent?client_id=YOUR_CLIENT_ID`.
- **JHU emails not appearing:** v2 only fetches the ND Gmail inbox (`fetch_all_unread` in `daily_brief_v2.py`) — the JHU Graph path lives in `daily_brief.py` but v2 never calls it. Confirm this is actually still wanted before troubleshooting JHU auth.
- **Nothing summarized despite relevant mail:** check `~/Library/Logs/daily-brief-detail.log` (the real log — `output/cron.log` is v1's, stale since the 2026-08-04 Drive-mount flush issue moved logging local). There's no keyword pre-filter in v2 — every fetched email goes to Claude's router, so a miss is either a routing call or something read before the fetch (see "Read Before This Run" above).
- **An email is read but never showed up anywhere in the brief:** check the "Read Before This Run" section first — if you opened it yourself in Gmail before 5pm, that's expected (see the model above), not a bug. If it's not a trusted source and not in Coverage/personal either, that's the "⚠ Flagged" safety net catching something worth investigating.
- **A trusted newsletter keeps getting missed (or never gets a real routing chance because you read it yourself):** add its sender substring → topic bucket to the `TRUSTED_SENDERS` dict at the top of `daily_brief.py` (v2 imports it as `db.TRUSTED_SENDERS`), e.g. `"newsletters@e.econo": "Economic Competition & Geopolitics"`. Trusted sources get a much deeper read at both routing and synthesis, and are also what "Read Before This Run" checks for.

---

## Key Design Principles

- **`_inbox/Today.md` is your daily workspace** — always the same filename; `--parse` routes it to `_outbox/` at 3:00am using the date baked into its frontmatter.
- **`_outbox/` is the staging layer** — every section of every day's note lands here as a dated file. Content is never lost.
- **`__wiki/` pages are living documents** — synthesized knowledge rewritten weekly by `weekly_report.py`. Not append-only logs.
- **Reflections are verbatim** — `_outbox/Reflections/` content is never synthesized or paraphrased, only cleaned up and appended.
- **Email "For Notes"** — send from any address listed in your `SENDER_ADDRS` (see **Setup**) with subject `For Notes: <topic>`.
- **`_words.md`** — running vocabulary list. Mark an entry in the daily `## Words` section with `(new)` to append it automatically.
- **🟢 API spend** — `weekly_report.py` calls Claude twice (wiki synthesis + report generation). Everything else is local.
