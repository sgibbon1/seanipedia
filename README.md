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

🟢 = calls an external LLM API (Gemini via REST, `AI_PROVIDER=gemini`; `llm.py` can switch back to Claude) — costs money / requires internet
⛓ = runs as a step of the **morning chain** (`morning_chain.sh`, 5:30am): in dependency order, each step only after the one it needs has succeeded
🔁 = has backup triggers: launchd fires it three times a morning and `~/scripts/resilient_run.sh` makes finished steps no-ops
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
> **Created by:** `daily.py --generate`, step 2 of the morning chain (`com.seang.morning-chain`, **5:30am**). The brief is added a few minutes later, by step 4.

1. **Open today's note** — it lives at `_inbox/Today.md`. Pin a shortcut or use Obsidian's Quick Open to jump to it. If you journalled this calendar day in an earlier year, the note opens with `## On This Day` — last year's response to the same Calendar of Wisdom reading (see below).
2. **Write throughout the day** — fill in `## Notes`, `## Quotes`, `## Daily Study`, `## Reflections`, etc. as things come up. When a study topic is scheduled, `## Daily Study` has a `- [ ] Completed` checkbox — tick it off once you've actually done the work so it counts in the weekly report.
3. **Do nothing overnight** — at 5:30am, step 1 of the morning chain (`daily.py --parse`) reads yesterday's `Today.md`, routes each section to `_journal/` or `_outbox/` using the date from frontmatter, saves the brief's unreviewed sections to `_inbox/brief_carryover.md` for the morning, and deletes the file.

### Email notes on the go
Email yourself at your configured `MAIL_ACCOUNT` (see **Setup** below) with subject `For Notes: <topic>`. The noon `email_scan.py` job picks it up and appends it to today's `_inbox/Today.md` under `## Notes` (which `--parse` routes to `_outbox/Notes/YYYYMMDD.md` the next morning, like any other section). Past-dated emails are written straight to `_outbox/Notes/` since that day's note is gone.

### Weekly report
A report covering one completed **Sunday→Saturday** week lands in `vault/_weekly reports/YYYYMMDD.md`, named by the **Sunday after** that week — i.e. your review day (the May 31–Jun 6 week is saved as `20260607.md`). It runs on your Sunday review day for the week that just ended, and **excludes the current Sunday** so work you do on review day counts toward the next week. The report draws on both the week's `_outbox/` output and the daily `_journal/` practice (Calendar of Wisdom, Al-Anon, Sententiae Antiquae, Words). `__wiki/` pages are updated in the same run (from `_outbox/` only). No week is ever skipped: if the laptop was asleep the report is caught up later (one report per missed week), and even a quiet week with no activity gets a short placeholder so the cadence stays continuous.

### If something went wrong
```bash
cd ~/Library/CloudStorage/GoogleDrive-.../Sean/Code/ai_code/seanipedia/

python3 daily.py --parse --date 2026-04-28   # re-parse a specific date's inbox file
python3 daily.py --generate                  # manually generate today's note (no-op if it exists)
python3 daily.py --parse --allow-today       # deliberately close out TODAY's note early
python3 daily.py --refresh                   # re-populate Daily Study if Calendar was slow
python3 email_scan.py --dry-run              # preview email scan without writing
python3 weekly_report.py --dry-run           # preview weekly report without writing

# Test against a COPY of the vault, never the real one. An explicit VAULT_PATH
# in the environment wins over the .env (it didn't before 2026-10-27, and a
# "sandbox" --parse consumed the real Today.md).
VAULT_PATH=/tmp/vault-copy python3 daily.py --parse
```

---

## Data Flow

```
  ⛓ 1. daily.py --parse                          ⛓ 2. daily.py --generate
  (morning chain, 5:30am — steps run in order, each only if the previous succeeded)
  consumes YESTERDAY's Today.md                  creates TODAY's Today.md from:
          │                                        ▲
          ├──► _inbox/brief_carryover.md ──────────┤  yesterday's unreviewed brief topics
          │     (consumed + deleted by step 2)     │
          │                                        │
          ├──► _journal/MM-DD.md ──────────────────┤  earlier years' block → ## On This Day
          │    (one ## YYYY block per year)        │
          │                                        │
          │    quotes_index + quotes_ledger ───────┤  3 quotes below *From the archive*
          │    Calendar (STUDY_CALENDAR) ──────────┤  ## Daily Study topic
          │    natsec_jobs SQLite ─────────────────┘  ## Today's Jobs
          │
          │    the rest of the chain, then the day's standalone jobs:
          │      ⛓ 3. scrape_jobs.py              ## Today's Jobs (natsec_jobs)
          │      ⛓ 4. daily_brief_v2.py 🟢        ## Daily Intelligence Brief
          │                                       (runs email_scan first)
          │      ⛓ 5. weekly_report.py 🟢         (see below)
          │      ⛓ 6. quotes_index + blockids     rebuild the quote deck
          │      daily.py --refresh ⏰🔁 (8am)    ## Daily Study, if Calendar was slow
          │      email_scan.py ⏰🔁 (noon)        ## Notes ("For Notes" mail)
          │
          └─ routes each section of yesterday's note:
                ┌──────────┬──────────┬──────────┬──────────┬──────────┐
                ▼          ▼          ▼          ▼          ▼          ▼
           _journal/   _outbox/   _outbox/   _outbox/   _outbox/   _outbox/
           MM-DD.md    Quotes/    Daily      Therapy/   Reflec-    Notes/
           ## <year>   YYYYMMDD   Study/     YYYYMMDD   tions/     YYYYMMDD
           block                  YYYYMMDD              YYYYMMDD
             │
             └──► _words.md   (Words entries marked "(new)")

  ## Daily Intelligence Brief ──► archive/Daily Intelligence Brief/YYYYMMDD.md
                                  (full brief incl. run report; NOT _outbox —
                                  never feeds __wiki/ or _weekly reports/)

  ┌──────────────────────────────────────────────────────────────────────┐
  │ _outbox/ (synthesized, then archived) + _journal/ (read for the       │
  │ report — the week's own year block only)                              │
  └───────────────────────────────┬──────────────────────────────────────┘
                                  │
             ⛓ 5. weekly_report.py 🟢 (every morning, after parse; reports completed Sun–Sat weeks)
                    │           │           │
                    ▼           ▼           ▼
                 __wiki/    _weekly       macOS
               (updated)    reports/   notification 🔔
                            YYYYMMDD     │
                                         └─ moves processed _outbox/ files to archive/

  archive/Quotes/ ──► ⛓ 6. quotes_index.py + quotes_blockids.py ──► back into tomorrow's quote deal
```

### Daily note sections → destinations

| Section | Destination |
|---|---|
| ## A Calendar of Wisdom | `_journal/MM-DD.md` → `## <year>` block |
| ## Al-Anon | `_journal/MM-DD.md` → `## <year>` block |
| ## Sententiae Antiquae | `_journal/MM-DD.md` → `## <year>` block |
| ## Words | `_journal/MM-DD.md` → `## <year>` block (+ new words → `_words.md`) |
| ## Quotes | `_outbox/Quotes/YYYYMMDD.md` |
| ## Daily Study | `_outbox/Daily Study/YYYYMMDD.md` — when a topic is scheduled, the section carries a `- [ ] Completed` box; check it off once done. Every day is archived, but `weekly_report.py` only credits **checked** days in the wiki/report (unchecked = assigned-but-skipped; legacy days with no box still count). |
| ## Notes | `_outbox/Notes/YYYYMMDD.md` |
| ## Reflections | `_outbox/Reflections/YYYYMMDD.md` (verbatim) |
| ## Therapy | `_outbox/Therapy/YYYYMMDD.md` |
| ## Daily Intelligence Brief | written by `daily_brief_v2.py`, step 4 of the morning chain. `--parse` copies the WHOLE section to `archive/Daily Intelligence Brief/YYYYMMDD.md` (not `_outbox`, never feeds wiki/weekly reports), and saves each topic subsection whose `Reviewed` box is still unticked to `_inbox/brief_carryover.md`. The run-report sections at the end — Upcoming Events, Coverage, Read Before This Run — are **not** carried: they describe that one run, and tomorrow's brief writes its own. |
| ## Today's Jobs | written by `scrape_jobs.py` (natsec_jobs), step 3 of the morning chain — not routed by `--parse` |
| ## On This Day | written by `--generate` from earlier years' `_journal/` blocks — a read-only copy, **not routed** by `--parse` |

`## Quotes` holds two things: whatever you write during the day, and the quotes resurfaced each morning from the lifetime collection below a `*From the archive*` divider. Only what sits **above** that divider is archived — no marker needed, just put it above the line. Everything from the divider down is there for rereading and is left untouched.

#### `_journal/` stacks years

`_journal/` is keyed by **calendar day** (`MM-DD.md`), not by full date, because the practice follows *A Calendar of Wisdom*'s fixed day-by-day readings — October 27 is the same passage every year. The collection lapped on **2026-10-27** (parser-written entries covered Apr 21–Oct 26 2026; the OneNote-era import covered Oct 27 2025–Apr 20 2026), so each day's file now holds **one `## YYYY` block per year, newest first**, with the four sections at `###` inside it:

```
# October 27
## 2026
### A Calendar of Wisdom      ← this year
### Al-Anon
---
## 2025
### A Calendar of Wisdom      ← last year, same passage
```

Open the day and you see every year's response to one reading side by side. `--parse` **sets this year's block and leaves the others alone** (`journal_years.upsert_year_block`), and refuses to write at all if it can't parse the file — the old code wrote the whole file, which would have erased last year's entry one night at a time. `weekly_report.py` reads only the block for the week's own year. The one-time conversion of the 312 pre-existing flat files was `migrate_journal_years.py` (run 2026-10-27; `--apply` to write, dry run by default).

#### `## On This Day` — the lapped years, prepended

Since the journal laps (above), `--generate` now opens the daily note with what you wrote on this same calendar day in earlier years, newest first, under `## On This Day` — against the same Calendar of Wisdom reading, so last year's response is in front of you before you write this year's. The journal's `###` sections nest at `####` under their year.

It is a READ-ONLY copy of text that already lives in `_journal/`. The heading is deliberately not one of the four routed sections, so `--parse` ignores it instead of routing it back and duplicating the entry a year at a time.

Note: `_journal/` (the four perennial Calendar-of-Wisdom sections) is **read into** the weekly report for context — the week's Sententiae Antiquae and new Words land in *Quotes & Reading*, and the Calendar-of-Wisdom/Al-Anon readings inform *Personal Insights*. But `_journal/` is **never archived or fed to wiki synthesis**: it remains a permanent personal record, separate from the `_outbox/` staging layer. Only `_outbox/` content is synthesized into `__wiki/` and moved to `archive/`.

---

## Scripts

### Daily (automated via launchd)

| Script | Purpose | Schedule |
|---|---|---|
| `daily.py --generate` ⏰🔁 | Creates today's `_inbox/Today.md`: `## On This Day` from earlier years' journal, the section skeleton with today's Calendar of Wisdom / Al-Anon links, three resurfaced quotes, the Daily Study topic from Calendar, yesterday's unreviewed brief items (from `brief_carryover.md`), and today's jobs. If yesterday's note is still there it parses it first; if **today's** note already exists it does nothing — regenerating would wipe what you've written and burn a fresh deal of quotes. | ⛓ step 2 |
| `daily.py --refresh` ⏰🔁 | Re-populates `## Daily Study` if the morning run fired before Calendar synced. | 8:00am daily |
| `daily.py --parse` ⏰🔁 | Routes each section to `_journal/` (this year's block) or `_outbox/`; appends new Words to `_words.md`; archives the brief and writes `brief_carryover.md`; deletes the inbox file. A verbatim repeat of an outbox page is skipped, so a double parse can't file anything twice. **Refuses a note dated today** (exit 0 — that note is still in use; `--allow-today` overrides). | ⛓ step 1 |
| `email_scan.py` ⏰🔁 | Scans Mail for unread "For Notes" messages; appends body to today's `Today.md` `## Notes` section (or `_outbox/Notes/YYYYMMDD.md` for past-dated / missing-note cases); marks read. | noon daily |
| `daily_brief_v2.py` 🟢⏰🔁 | Fetches the ND alumni inbox (everything unread since the last brief — a time window, not a keyword pre-filter), routes each email with the LLM, writes ONE trend-synthesis narrative per topic, folds in yesterday's unreviewed sections, compresses the whole to ~3,500 words, saves `output/brief_YYYY-MM-DD.md`, inserts `## Daily Intelligence Brief` into `Today.md`. Marks read only what it verifiably summarized; off-domain and personal mail is left unread on purpose. See **Daily Brief** below. | ⛓ step 4 |
| `scrape_jobs.py` ⏰🔁 | (sibling `natsec_jobs/`) scrapes the job boards, dedupes in SQLite, injects `## Today's Jobs`, sends a 🔔 notification. | ⛓ step 3 |

### Weekly (step 5 of the morning chain)

| Script | Purpose | Schedule |
|---|---|---|
| `weekly_report.py` 🟢⏰🔁 | Reports each completed **Sun–Sat** week that doesn't yet have a report: reads that week's `_outbox/`, updates `__wiki/`, generates the report, saves to `_weekly reports/`, archives processed files. Idempotent — tracks the last reported week in `logs/.last_reported_week`, so it's safe to run any day, any number of times, and catches up missed weeks (one report each). | ⛓ step 5, every morning |

```bash
python3 weekly_report.py                   # report all completed-but-missing weeks (idempotent)
python3 weekly_report.py --week 2026-05-31  # force one specific week (any date within it)
python3 weekly_report.py --no-wiki         # skip wiki updates, report only
python3 weekly_report.py --dry-run         # preview without writing
```

### Supporting modules and one-time scripts

| File | What |
|---|---|
| `journal_years.py` | The year-stacked `_journal/MM-DD.md` format: parse a day's `## YYYY` blocks, set one year's block (idempotent), refuse to write a file it can't parse. Shared by `daily.py`, `weekly_report.py` and the migration so they can't disagree. |
| `migrate_journal_years.py` 👤 | One-time (run 2026-10-27) conversion of the 312 flat journal files into year blocks. Dry run by default; `--apply` to write; `--only 10-27` for one day. Safe to re-run — already-stacked files are skipped. |
| `morning_chain.sh` ⏰ | Runs the morning's dependent jobs in order — see **Automation**. `--dry-run` shows what each step would do; `--force-brief` re-runs just today's brief (a same-day rescan). |
| `quotes_index.py` / `quotes_blockids.py` / `quotes_daily.py` | Build the lifetime quote index (with `^id` block anchors in the source pages), and deal three not-yet-shown quotes into each morning's `## Quotes`, tracked in `quotes_ledger.json` so the deck cycles through everything before repeating. The index rebuild and anchor stamping are ⛓ step 6 — **nothing scheduled them before 2026-10-30**, so the deck had been frozen since Aug 16 and the first scheduled rebuild added 84 quotes. Ids are hashes of the quote text, so editing a quote gives it a new id; nothing in your source files is touched by a rebuild except the invisible `^id` markers. |
| `sync_readme.py` 👤 | Copies this README into the vault twin (`_inbox/README.md`), keeping the two deliberate differences: no Setup section in the vault, and the vault's own personal "For Notes" addresses (the repo is public). Edit the repo copy, then run it; `--check` reports drift. |
| `llm.py` | Provider-agnostic completion (Gemini or Anthropic, chosen by `AI_PROVIDER`) with token-usage logging. |

---

## Automation (launchd)

| Job | What | Triggers 🔁 | Log |
|---|---|---|---|
| `com.seang.morning-chain` | `morning_chain.sh`: parse → generate → jobs → brief → weekly → quote reindex | 5:30, 6:45, 8:00am | `~/Library/Logs/morning-chain.log` (the chain) · each step still logs to its own file below |
| `com.seang.daily-refresh` | `daily.py --refresh` | 8:00, 9:30, 11:00am | `~/Library/Logs/daily-refresh.log` |
| `com.seang.email-scan` | `email_scan.py` | noon, 1:30, 3:00pm | `~/Library/Logs/email-scan.log` |
| `com.seang.git-autocommit` | `git_autocommit.sh` | 11:30, 11:45, 11:55pm | — |

**The morning chain.** These used to be eight independent jobs spaced hours apart, and the spacing was the only thing enforcing their order — which a sleeping laptop, or a scheduler five hours out of step (below), quietly defeats. Now one job runs them in dependency order:

| Step | Runs | Needs | If it fails |
|---|---|---|---|
| 1. parse | `daily.py --parse` | — | **stop the spine**: no generate, no brief, no weekly; yesterday's note stays put |
| 2. generate | `daily.py --generate` | 1 | stop the spine: no note means the brief would land nowhere |
| 3. jobs | `natsec_jobs/run_daily.sh` | — (injects into the note if there is one) | carry on |
| 4. brief | `daily_brief/run_daily_brief.sh` → `email_scan.py`, then `daily_brief_v2.py` 🟢 | 2, and today's note on disk | carry on |
| 5. weekly | `run_weekly.sh` → `weekly_report.py` 🟢 | 1 — Sunday's report must include Saturday's filed note | carry on |
| 6. reindex | `quotes_index.py`, then `quotes_blockids.py --apply` | — (runs after 5, which moves new quotes into `archive/`) | carry on |

Each step runs through `~/scripts/resilient_run.sh` under the **same label** its old standalone job used (`daily-parse`, `daily-generate`, `natsec-jobs`, `daily-brief`, `weekly-report`, plus `quotes-index` / `quotes-blockids`). So each keeps its own retries (2, 10, 30 minutes), its Drive warm-up, its local `.env` sync, its stale-job notification — and its **"already succeeded today" stamp**, which is what makes three triggers safe: on the 6:45 and 8:00 triggers every finished step is a no-op and only unfinished work runs. The chain itself runs under `resilient_run.sh` with `RR_FORCE=1 RR_ATTEMPTS=1` (run on every trigger, no outer retries — the steps have their own) and takes a `lockf` lock so two triggers can't run it at once. Manual use:

```bash
./morning_chain.sh --dry-run       # what each step would do right now; runs nothing
./morning_chain.sh                 # run whatever is unfinished today
./morning_chain.sh --force-brief   # re-run today's brief: a same-day rescan
RR_FORCE=1 ~/scripts/resilient_run.sh daily-parse "$PWD/daily.py" 2 --parse   # one step, forced
```

Every job runs through `~/scripts/resilient_run.sh` (local disk; a copy is versioned at `launchd/resilient_run.sh`). Once a job succeeds, later triggers that day are no-ops (`RR_FORCE=1` overrides); each attempt warms the project's files off the Drive mount first, retries at 2, 10 and 30 minutes (`RR_ATTEMPTS` caps the count), and syncs the project's `.env` to `~/.config/<project>/.env` — scripts read that local copy first, because reading `.env` off an unhydrated Drive placeholder is what killed the 2026-08-12 run. launchd also fires a missed trigger once on wake.

**If every job seems to run at the wrong time:** since 2026-10-26 launchd's scheduler has been firing every job **exactly five hours early** (the brief at noon, generate at 1am, parse at 10pm), even though the system clock and `/etc/localtime` say America/New_York — the timezone file was rewritten on Oct 26 at 08:40 and the scheduler never caught up. The chain's ordering makes this harmless, but it would still run the chain at 12:30am. **Restarting the Mac resyncs the scheduler.** To check: `grep "morning-chain start" ~/Library/Logs/morning-chain.log | tail -3` should show 05:30.

**`daily.py` jobs never overlap.** `--parse`, `--generate` and `--refresh` all rewrite `Today.md`, and launchd fires every missed trigger at once when the Mac wakes. So each takes an exclusive lock on `_inbox/.daily.lock` (released automatically if the process dies; waits up to 15 minutes), and the second job in waits for the first. That's only safe *because* of the two guards above: parse declines a note dated today, and generate declines to overwrite one. On 2026-10-30, before these existed, the 6:00 parse backup fired in the same second as generate; both parsed yesterday's note, parse crashed, and its retry two minutes later archived and deleted the fresh October 30 note.

Original plists from before the backup triggers are kept in `~/scripts/plist-backup-2026-08-12/`; the five jobs the chain replaced (`daily-parse`, `daily-generate`, `daily-brief`, `natsecjobs`, `weekly-report`) are in `~/scripts/launchagents-retired-2026-10-30/`, and everything as it stood before the chain in `~/scripts/plist-backup-2026-10-30/`.

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
2. **Route** (LLM, batched) — for each email: relevant? which of the six topics? is it announcing an event? is it personal/professional correspondence rather than a subscribed publication? The topics, in order: *Artificial Intelligence & Other Emerging Technologies* (with Ukraine Defense Tech as a standing subsection), *United States National Security Policy*, *China & Indo-Pacific Competition*, *Geopolitics & Geoeconomics*, *Russia, Ukraine & Eastern Europe*, *Other National Security*.
3. **Synthesize** (one call per topic) — ONE trend-focused narrative per topic aggregating everything in the window, merged with that topic's **unreviewed** sections from the previous brief (the carry-forward). Trends emerge only across briefs you haven't checked off — there is no look-back over older archived briefs (`recent_context` was removed 2026-09; a ~40k-word-per-topic look-back is the noted fallback if that ever proves too short). Every relevant email is guaranteed a mention: cited inline or listed under an auto-appended "Also noted". Prompts require real dates ("on Oct 27"), never "today".
4. **Compress** — the topic sections are cut to `BRIEF_WORD_CAP` (2,800 counted words, which lands near ~3,500 readable ones), split half fresh / half carried (`FRESH_SHARE=0.5`), with each topic's budget blended from fixed `TOPIC_WEIGHTS` and its actual volume. A small backlog is topped up with fresh intel to reach the cap. Links are masked as `⟦L#⟧` tokens during compression so the model can't mangle a URL. Guards keep the **uncompressed** section rather than ship damage — if compression thins the citations, or if the output is cut off mid-sentence — and unmasking repairs the two known model habits: wrapping a citation in a code span, and dropping a token's closing bracket.
5. **Assemble** — each topic subsection gets two labelled boxes, `- [ ] Reviewed — <name>` and `- [ ] Flag — <name>`. Labels must be unique because the Obsidian Tasks plugin finds a task by its line text, so identical lines can't be ticked on the phone. **Reviewed** is the carry-forward gate; **Flag** marks something to re-read and doesn't hold a section back. Then the run report: **Upcoming Events** (merged across issues in `output/events_store.json`), **Coverage** (every email routed non-relevant, so the filter itself is checkable), personal/professional mail (never summarized, never marked read), and **Read Before This Run**.
6. **Mark read** — ONLY email that's verifiably in the brief just written. Off-domain (Coverage) and personal mail are deliberately left unread — they're your own inbox to-do list.

**The carry-forward loop:** each morning `--parse` (chain step 1) writes every topic subsection whose `Reviewed` box is still unticked to `_inbox/brief_carryover.md`; `--generate` (step 2) injects it into the new note (headed "*N unread from <date>*", counting sections, not checkboxes) and deletes the file; minutes later the brief (step 4) merges those items into its fresh synthesis. A section keeps coming back until you tick it. On a normal day the file exists only for the seconds between steps 1 and 2.

Two more sections can appear when relevant:
- **"⚠ Flagged — Left Unread (Safety Check)"** — a structural belt-and-suspenders check: if an email was ever judged relevant but somehow didn't make it into a synthesized section, it's listed here and explicitly NOT marked read, rather than trusting the normal-case guarantee blindly.
- **"Read Before This Run (Trusted Sources)"** — reading an email directly in Gmail (opening it yourself) marks it read before the script ever sees it, so `is:unread` silently skips it — it's never routed, never in Coverage, never anywhere. This section catches the curated `TRUSTED_SENDERS` subset of that blind spot (WOTR, TLDR AI, Jamestown, etc.) with a cheap sender-match scan, so a trusted newsletter you happened to open yourself doesn't just vanish with zero trace. Ordinary already-read inbox traffic (shopping receipts, personal correspondence) is expected and not surfaced. The scan is skipped on runs with an explicit `--after`/`--before` window, where it would mostly list mail that earlier briefs marked read. *Example:* FT's "The AI Shift" lands on Thursdays and is usually opened on arrival, so since mid-August it has appeared here rather than in the prose — the router marks it relevant every time it actually sees it.

**Clearing an unread backlog:** `python3 daily_brief_v2.py --after 2026/10/04 --limit 500 --mark-read --no-carry --no-vault --out-name backlog-2026-10-05_10-27 --title "Backlog Brief — October 5–27, 2026"`, then copy `output/brief_<name>.md` into `_inbox/`. Gmail's `after:D` means D 00:00 onward, so use the day *before* the first day you want. Survey the count first — the default `--limit` is 400 — and expect Gmail's per-minute query quota to drop a few messages on a large fetch (they stay unread; the log names them). The 2026-10-27 run: 431 of 444 fetched, 133 briefed and marked read, three of six topics kept uncompressed by the guards (~8,500 words).

**Catching up on already-read mail:** `python3 daily_brief_v2.py --include-read --after 2026/08/11` re-scans a window INCLUDING mail you already read yourself (not just what's still unread) and synthesizes it same as normal — but a retrospective run like this never marks anything read, since it's re-reading mail that's already been handled. Add `--out-name catchup-aug11 --no-vault` to write to a standalone file instead of overwriting today's dated brief / touching `Today.md`.

Sample output (as written to `output/`; inserted into `Today.md` one heading level deeper):

```
# Daily Intelligence Brief — October 27, 2026

*Generated 12:10 | 150 relevant of 270 emails spanning Oct 20–Oct 27 · trend synthesis*

## United States National Security Policy
### Overview
- [ ] Reviewed — United States National Security Policy overview
- [ ] Flag — United States National Security Policy overview

U.S. national security policy increasingly integrates AI and autonomy into
military capabilities [POLITICO's National Security Daily](message://…) …

### Strategic Competition and Intelligence
- [ ] Reviewed — Strategic Competition and Intelligence
- [ ] Flag — Strategic Competition and Intelligence
…
## Upcoming Events (DC Metro & Virtual)
## Coverage
<details><summary>108 email(s) filtered as off-domain (left unread …)</summary>
## Read Before This Run (Trusted Sources)
```

Citation links use the `message://` scheme, which opens the email in Apple Mail.

**Useful flags** (`python3 daily_brief_v2.py --help` for the full list):

| Flag | Use |
|---|---|
| `--dry-run` | Preview only — writes `output/brief_preview_*.md`, marks nothing read. |
| `--limit N` | Safety cap on messages fetched (default 400); the daily window is time-based, not count-based, so this is a backstop, not the normal control. |
| `--backlog` | Big one-time catch-up (raises the cap, sub-batches synthesis); pair with `--mark-read` to actually clear it. |
| `--mark-read` | Mark the briefed mail read even on a `--backlog` run (a normal run always does). |
| `--before` / `--after YYYY/MM/DD` | Explicit Gmail-syntax date window instead of "since the last brief." |
| `--include-read` | Retrospective — includes already-read mail, never marks anything read. |
| `--out-name NAME` | Write to `output/brief_NAME.md` instead of today's date. |
| `--no-vault` | Write the file only; skip the `Today.md` insert. |
| `--no-carry` | Skip carrying forward unreviewed sections from the last brief. |
| `--title` | Override the brief's H1 title (the `# ` is added if you leave it off). |

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

**Run / behavior:** `python3 daily_brief_v2.py`. The brief is inserted before `## Today's Jobs` (or at the end of the note). Re-running on a day that already has a brief switches to a **same-day rescan**: it rebuilds the section from ALL of today's mail, read or unread, so late arrivals get folded in rather than a topic looking "done" after the first run — and boxes you've already ticked today stay ticked. It runs as step 4 of the morning chain via `daily_brief/run_daily_brief.sh` (under `resilient_run.sh`), which waits for DNS, runs `email_scan.py` first (serializing `Today.md` writes), then the brief — a bash wrapper is required because `/bin/bash` needs Full Disk Access to reach the Google Drive path. (`daily_brief.py`, the v1 script this replaced, had its own separate up-to-7-day catch-up loop; v2 doesn't need one — its normal fetch window already covers everything unread since the last brief, however many days that spans.)

**Troubleshooting:**
- **Gmail auth fails / token expired:** delete `credentials/gmail_token.json`, re-run `setup_auth.py`.
- **JHU auth fails:** delete `credentials/jhu_token.json`, re-run `setup_auth.py`. If sign-in is blocked, the Azure app may need consent — open `https://login.microsoftonline.com/common/adminconsent?client_id=YOUR_CLIENT_ID`.
- **JHU emails not appearing:** v2 only fetches the ND Gmail inbox (`fetch_all_unread` in `daily_brief_v2.py`) — the JHU Graph path lives in `daily_brief.py` but v2 never calls it. Confirm this is actually still wanted before troubleshooting JHU auth.
- **Formatting looks off in the brief** (raw `message://` URLs in grey code boxes, a stray `⟦L41`, a section ending mid-sentence): compression artifacts. Since 2026-10-27 unmasking peels code-spans off citations and re-closes cut-off tokens, the compressor has 3.2× token headroom, and a truncated section is replaced by its uncompressed original. If one still gets through, repair the archived copy, `Today.md`, *and* `brief_carryover.md` — the carryover holds its own copy of the same text until 6am.
- **Nothing summarized despite relevant mail:** check `~/Library/Logs/daily-brief-detail.log` (the real log — `output/cron.log` is v1's, stale since the 2026-08-04 Drive-mount flush issue moved logging local). There's no keyword pre-filter in v2 — every fetched email goes to Claude's router, so a miss is either a routing call or something read before the fetch (see "Read Before This Run" above).
- **An email is read but never showed up anywhere in the brief:** check the "Read Before This Run" section first — if you opened it yourself in Gmail before the morning brief ran, that's expected (see the model above), not a bug. If it's not a trusted source and not in Coverage/personal either, that's the "⚠ Flagged" safety net catching something worth investigating.
- **A trusted newsletter keeps getting missed (or never gets a real routing chance because you read it yourself):** add its sender substring → topic bucket to the `TRUSTED_SENDERS` dict at the top of `daily_brief.py` (v2 imports it as `db.TRUSTED_SENDERS`), e.g. `"newsletters@e.econo": "Economic Competition & Geopolitics"`. Trusted sources get a much deeper read at both routing and synthesis, and are also what "Read Before This Run" checks for.

---

## Key Design Principles

- **`_inbox/Today.md` is your daily workspace** — always the same filename; `--parse` routes it to `_outbox/` the next morning, using the date baked into its frontmatter.
- **`_outbox/` is the staging layer** — every section of every day's note lands here as a dated file. Content is never lost.
- **`_journal/` is perennial and year-stacked** — one file per calendar day, one `## YYYY` block per year. Nothing ever overwrites another year's block, and the writer refuses a file it can't parse rather than guessing.
- **Order by dependency, not by clock** — jobs that need each other run as steps of one chain; only genuinely time-of-day jobs (refresh, email-scan, autocommit) keep their own schedules.
- **Test against a copy** — `VAULT_PATH=/path/to/copy python3 daily.py …` really does isolate a run; an explicit `VAULT_PATH` beats the `.env`.
- **`__wiki/` pages are living documents** — synthesized knowledge rewritten weekly by `weekly_report.py`. Not append-only logs.
- **Reflections are verbatim** — `_outbox/Reflections/` content is never synthesized or paraphrased, only cleaned up and appended.
- **Email "For Notes"** — send from any address listed in your `SENDER_ADDRS` (see **Setup**) with subject `For Notes: <topic>`.
- **`_words.md`** — running vocabulary list. Mark an entry in the daily `## Words` section with `(new)` to append it automatically.
- **🟢 API spend** — `daily_brief_v2.py` (routing, per-topic synthesis, compression, citation repair, event extraction) and `weekly_report.py` (wiki synthesis + report). `daily.py`, `email_scan.py` and the quote scripts are local.
