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

## How to Use

### Daily note
> **Created by:** `daily.py --generate` (via `com.seang.daily-generate`) at **6:00am daily**

1. **Open today's note** — it lives at `_inbox/Today.md`. Pin a shortcut or use Obsidian's Quick Open to jump to it.
2. **Write throughout the day** — fill in `## Notes`, `## Quotes`, `## Daily Study`, `## Reflections`, etc. as things come up.
3. **Do nothing at 3:00am** — `daily.py --parse` reads `Today.md`, routes each section to `_outbox/` using the date from frontmatter, and deletes the file.

### Email notes on the go
Email yourself at `your.email@alumni.example.edu` with subject `For Notes: <topic>`. The noon `email_scan.py` job picks it up and appends it to today's `_inbox/Today.md` under `## Notes` (which `--parse` routes to `_outbox/Notes/YYYYMMDD.md` at 3am, like any other section). Past-dated emails are written straight to `_outbox/Notes/` since that day's note is gone.

### Weekly report
A report lands in `vault/_weekly reports/YYYYMMDD.md` every Sunday morning. `__wiki/` pages are updated in the same run.

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
  daily_brief.py 🟢 ⏰ (5pm)   email_scan.py ⏰ (noon)   scrape_jobs.py ⏰ (6:15am)   daily.py --generate ⏰ (6am)
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

  ┌─────────────────────────────────────────────────────────────────┐
  │  _outbox/ content only (7 days) — _journal is NOT processed     │
  └──────────────────────┬──────────────────────────────────────────┘
                         │
             weekly_report.py  🟢 ⏰ (Sunday 8am)
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
| ## Daily Study | `_outbox/Daily Study/YYYYMMDD.md` |
| ## Notes | `_outbox/Notes/YYYYMMDD.md` |
| ## Reflections | `_outbox/Reflections/YYYYMMDD.md` (verbatim) |
| ## Therapy | `_outbox/Therapy/YYYYMMDD.md` |
| ## Daily Intelligence Brief | written by `daily_brief.py` at 5 PM — not routed by `--parse` |
| ## Jobs | written by `scrape_jobs.py` at 6:15 AM — not routed by `--parse` |

Note: `_journal/` is **not** processed by `weekly_report.py`. It is a permanent personal archive, separate from the `_outbox/` staging layer.

---

## Scripts

### Daily (automated via launchd)

| Script | Purpose | Schedule |
|---|---|---|
| `daily.py --generate` ⏰ | Creates today's `_inbox/Today.md` with all sections pre-filled from calendar. | 6:00am daily |
| `daily.py --refresh` ⏰ | Re-populates `## Daily Study` if the 6am run fired before Calendar synced. | 8:00am daily |
| `daily.py --parse` ⏰ | Routes each section to `_journal/` or `_outbox/`; appends new Words to `_words.md`; deletes the inbox file. | 3:00am daily |
| `email_scan.py` ⏰ | Scans Mail for unread "For Notes" messages; appends body to today's `Today.md` `## Notes` section (or `_outbox/Notes/YYYYMMDD.md` for past-dated / missing-note cases); marks read. | noon daily |
| `daily_brief.py` 🟢 ⏰ | Fetches ND alumni inbox, filters for AI/natsec/geopolitics topics, summarizes with Claude, saves `output/brief_YYYY-MM-DD.md`, inserts `## Daily Intelligence Brief` into `Today.md`. Catches up missed days automatically (up to 7). | 5:00pm daily |

### Weekly (automated via launchd)

| Script | Purpose | Schedule |
|---|---|---|
| `weekly_report.py` 🟢 ⏰ | Reads 7 days of `_outbox/`; updates `__wiki/`; generates report; saves to `_weekly reports/`; archives processed files. | Sunday 8:00am |

```bash
python3 weekly_report.py --no-wiki       # skip wiki updates, report only
python3 weekly_report.py --days 14       # look back 14 days instead of 7
```

---

## Automation (launchd)

| Job | Script | Schedule | Log |
|---|---|---|---|
| `com.seang.daily-generate` | `daily.py --generate` ⏰ | 6:00am daily | `~/Library/Logs/daily-generate.log` |
| `com.seang.daily-refresh` | `daily.py --refresh` ⏰ | 8:00am daily | `~/Library/Logs/daily-refresh.log` |
| `com.seang.daily-parse` | `daily.py --parse` ⏰ | 3:00am daily | `~/Library/Logs/daily-parse.log` |
| `com.seang.email-scan` | `email_scan.py` ⏰ | noon daily | `~/Library/Logs/email-scan.log` |
| `com.seang.daily-brief` | `~/scripts/run_daily_brief.sh` → `daily_brief.py` 🟢 ⏰ | 5:00pm daily | `~/scripts/daily_brief_launchd.log` |
| `com.seang.weekly-report` | `weekly_report.py` 🟢 ⏰ | Sunday 8:00am | `~/Library/Logs/weekly-report.log` |

All jobs fire on wake if the machine was asleep at the scheduled time.

To reload a job after editing its plist:
```bash
launchctl unload ~/Library/LaunchAgents/com.seang.<name>.plist
launchctl load   ~/Library/LaunchAgents/com.seang.<name>.plist
```

---

## Daily Brief — Setup & Credentials

`daily_brief.py` lives in the sibling project `ai_code/daily_brief/` (its own `.env`, `credentials/`, and `output/`). It fetches both alumni inboxes, filters for AI / national security / Russia-Ukraine / China / geopolitics, summarizes with Claude, saves `output/brief_YYYY-MM-DD.md`, and injects `## Daily Intelligence Brief` into `Today.md`. Sample output:

```
# Daily Intelligence Brief — May 24, 2026
*Generated 08:15 | 7 relevant emails*

## Artificial Intelligence & Emerging Technology
### [Subject line]
**From:** Sender Name | **Account:** ND Alumni | **Date:** May 24, 2026
*Why this matters: [one strategic sentence]*
[4–7 sentence analyst-quality summary]
[Open email →](https://mail.google.com/...)
```

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

**Run / behavior:** `python3 daily_brief.py`. The brief is inserted before `## Jobs` (or after `## Therapy`); re-running the same day replaces rather than duplicates the section. If the script missed one or more days it auto-catches-up (up to 7 days back; catch-up briefs are written to `output/` only, not `Today.md`). launchd runs it at 5pm via `~/scripts/run_daily_brief.sh`, which first runs `email_scan.py` (serializing Today.md writes) then the brief — a bash wrapper is required because `/bin/bash` needs Full Disk Access to reach the Google Drive path.

**Troubleshooting:**
- **Gmail auth fails / token expired:** delete `credentials/gmail_token.json`, re-run `setup_auth.py`.
- **JHU auth fails:** delete `credentials/jhu_token.json`, re-run `setup_auth.py`. If sign-in is blocked, the Azure app may need consent — open `https://login.microsoftonline.com/common/adminconsent?client_id=YOUR_CLIENT_ID`.
- **JHU emails not appearing:** confirm `alumni.jh.edu` uses Microsoft 365 (log in at [outlook.office.com](https://outlook.office.com)).
- **Nothing summarized despite relevant mail:** check `output/cron.log`; the keyword filter is broad, so it's usually Claude's second-stage relevance filter.
- **A trusted newsletter keeps getting missed:** add its sender substring → topic bucket to the `TRUSTED_SENDERS` dict at the top of `daily_brief.py`, e.g. `"newsletters@e.econo": "Economic Competition & Geopolitics"`. That bypasses the keyword stage (Claude's relevance filter still runs).

---

## Key Design Principles

- **`_inbox/Today.md` is your daily workspace** — always the same filename; `--parse` routes it to `_outbox/` at 3:00am using the date baked into its frontmatter.
- **`_outbox/` is the staging layer** — every section of every day's note lands here as a dated file. Content is never lost.
- **`__wiki/` pages are living documents** — synthesized knowledge rewritten weekly by `weekly_report.py`. Not append-only logs.
- **Reflections are verbatim** — `_outbox/Reflections/` content is never synthesized or paraphrased, only cleaned up and appended.
- **Email "For Notes"** — send from `your.email@alumni.example.edu` or `your.email@example.mil` with subject `For Notes: <topic>`.
- **`_words.md`** — vocabulary list for the weekly Word of the Week. Mark an entry with `(new)` to add it automatically.
- **🟢 API spend** — `weekly_report.py` calls Claude twice (wiki synthesis + report generation). Everything else is local.
