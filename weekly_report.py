"""
weekly_report.py — Weekly synthesis and report.

Reporting model: a week runs Sunday → Saturday. Each report covers a *completed*
Sun–Sat week and excludes the upcoming Sunday, so work done on Sean's Sunday
review/planning day counts toward the next week. Weeks are pinned to the
calendar, so a late run (laptop asleep on Sunday) still produces the same window
rather than a drifted one. No week is ever skipped — missed weeks are caught up,
one report each — and even a quiet week with no activity gets a placeholder
report so the cadence stays continuous.

For each week needing a report it:
  1. Collects _outbox/ content dated within that Sun–Sat window
  2. Updates relevant __wiki/ pages with new knowledge (and archives those files)
  3. Generates a weekly report: themes, study topics, personal insights
  4. Saves report to vault/_weekly reports/YYYYMMDD.md (named by the Sunday AFTER
     the week — the review day; e.g. the May 31–Jun 6 week is saved as 20260607.md)

State: the last reported week (a Sunday) is recorded in logs/.last_reported_week,
which makes the script idempotent — safe to run any day, any number of times; it
generates only the weeks that are still missing.

Usage:
  python3 weekly_report.py                      # report all completed-but-missing weeks
  python3 weekly_report.py --week 2026-05-31    # force one specific week (any date within it)
  python3 weekly_report.py --dry-run            # preview without writing
  python3 weekly_report.py --no-wiki            # skip wiki updates, report only
  python3 weekly_report.py --no-archive         # skip archiving processed files
  python3 weekly_report.py --therapy-bootstrap  # one-time: synthesize all therapy → __wiki/Therapy.md
  python3 weekly_report.py --catchup            # one-time: synthesize + archive pre-window outbox backlog
"""

from __future__ import annotations  # defer annotation eval — supports `X | None` on Python 3.9

import argparse
import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Provider-agnostic completion (Anthropic or Gemini via AI_PROVIDER in .env)
# with built-in token-usage logging.
from llm import complete, AI_PROVIDER

load_dotenv(override=True)

VAULT_PATH        = Path(os.environ.get("VAULT_PATH", "./vault"))
OUTBOX_DIR        = VAULT_PATH / "_outbox"
ARCHIVE_DIR       = VAULT_PATH / "archive"
WIKI_DIR          = VAULT_PATH / "__wiki"
WEEKLY_DIR        = VAULT_PATH / "_weekly reports"
JOURNAL_DIR       = VAULT_PATH / "_journal"


# Anthropic model used when AI_PROVIDER=anthropic; provider/client handled by llm.complete.
MODEL = "claude-opus-4-7"

OUTBOX_SECTIONS = ["Quotes", "Daily Study", "Notes", "Reflections", "Therapy"]

# Journal sections (the perennial "Calendar of Wisdom" daily practice). These
# live in _journal/MM-DD.md — one file per calendar day, year in the frontmatter.
# They are read INTO the weekly report for context but are NEVER archived or fed
# to wiki synthesis: _journal/ is a permanent personal record, not a staging layer.
JOURNAL_SECTIONS = ["A Calendar of Wisdom", "Al-Anon", "Sententiae Antiquae", "Words"]


# ── Collect _outbox content ────────────────────────────────────────────────

def collect_outbox(days: int = 7) -> dict[str, list[dict]]:
    """Return {section: [{date, filename, content}]} for files modified in the last N days."""
    cutoff = date.today() - timedelta(days=days)
    collected = {}
    for section in OUTBOX_SECTIONS:
        section_dir = OUTBOX_DIR / section
        if not section_dir.exists():
            continue
        entries = []
        for path in sorted(section_dir.glob("*.md")):
            if path.stem == "_misc":
                continue
            try:
                file_date = date(int(path.stem[:4]),
                                 int(path.stem[4:6]),
                                 int(path.stem[6:8]))
            except (ValueError, IndexError):
                continue
            if file_date < cutoff:
                continue
            content = path.read_text(encoding="utf-8").strip()
            if content:
                entries.append({"date": file_date.isoformat(),
                                "filename": path.name,
                                "content": content})
        if entries:
            collected[section] = entries
    return collected


# ── Calendar-week helpers ──────────────────────────────────────────────────
# A "report week" runs Sunday → Saturday.  A report always covers a *completed*
# Sun–Sat week and never the upcoming Sunday, so any work Sean does on his
# Sunday review/planning day counts toward the next week, not the one he's
# reviewing.  Weeks are fixed to the calendar, so a late run (laptop asleep on
# Sunday) still produces the same window rather than a drifted one.

# State file (a tiny marker recording the last week we reported) lives next to
# the script in logs/.  It makes the run idempotent — safe to run repeatedly —
# and drives catch-up when one or more Sundays were missed.
STATE_FILE = Path(__file__).resolve().parent / "logs" / ".last_reported_week"


def most_recent_sunday(d: date) -> date:
    """Return the Sunday on or before d (Sunday is the start of the week).

    Python's date.weekday() is Mon=0 … Sun=6, so (weekday + 1) % 7 is the
    number of days back to the most recent Sunday (0 if d is itself a Sunday).
    """
    return d - timedelta(days=(d.weekday() + 1) % 7)


def latest_completed_week_start(today: date) -> date:
    """Sunday that begins the most recent *fully completed* Sun–Sat week.

    On Sunday June 7 (or any day in the following week) this returns Sunday
    May 31 — i.e. the week May 31 → June 6, which has fully elapsed.
    """
    return most_recent_sunday(today) - timedelta(days=7)


def read_last_reported_week() -> date | None:
    """The week_start (a Sunday) of the last week we successfully reported."""
    try:
        return date.fromisoformat(STATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_last_reported_week(week_start: date) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(week_start.isoformat() + "\n", encoding="utf-8")


def weeks_to_report(today: date) -> list[date]:
    """Return the list of week_start Sundays that still need a report.

    - First run ever (no state file): just the most recent completed week, so
      we don't backfill the entire history of the vault.
    - Otherwise: every completed week after the last one we reported, in order,
      so multiple missed Sundays each get their own report (never skipped,
      never merged).
    """
    latest = latest_completed_week_start(today)
    last = read_last_reported_week()
    if last is None:
        return [latest]
    weeks: list[date] = []
    w = last + timedelta(days=7)
    while w <= latest:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks


def collect_outbox_for_week(week_start: date) -> dict[str, list[dict]]:
    """Collect _outbox files dated within the Sun–Sat week beginning week_start.

    Only files whose date falls in [week_start, week_start + 6 days] inclusive
    are returned; anything outside the window (e.g. the upcoming Sunday) is left
    in place for its own week's run.
    """
    week_end = week_start + timedelta(days=6)
    collected: dict[str, list[dict]] = {}
    for section in OUTBOX_SECTIONS:
        section_dir = OUTBOX_DIR / section
        if not section_dir.exists():
            continue
        entries = []
        for path in sorted(section_dir.glob("*.md")):
            if path.stem == "_misc":
                continue
            try:
                file_date = date(int(path.stem[:4]),
                                 int(path.stem[4:6]),
                                 int(path.stem[6:8]))
            except (ValueError, IndexError):
                continue
            if not (week_start <= file_date <= week_end):
                continue
            content = path.read_text(encoding="utf-8").strip()
            if content:
                entries.append({"date": file_date.isoformat(),
                                "filename": path.name,
                                "content": content})
        if entries:
            collected[section] = entries
    return collected


def _study_is_complete(content: str) -> bool:
    """Whether a Daily Study entry counts as completed for report/wiki purposes.

    daily.py writes "- [ ] Completed" under a study day's heading; checking it off
    in Obsidian ("- [x] Completed") marks the day done. We match that LABELLED box
    specifically (not any checkbox) so an unrelated task list in the notes can't be
    mistaken for completion. Entries with no completion marker at all — legacy files
    from before this feature, or study notes written under a bare "## Daily Study"
    with no scheduled topic — are included by default so nothing is silently dropped.
    """
    if re.search(r"^\s*- \[[xX]\]\s*Completed\b", content, re.MULTILINE):
        return True   # explicitly checked off
    if re.search(r"^\s*- \[ \]\s*Completed\b", content, re.MULTILINE):
        return False  # marker present but unchecked → assigned then skipped
    return True       # no marker → legacy/manual content, keep it


def _filter_completed_study(collected: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Shallow copy of `collected` with Daily Study pruned to completed days only.

    Used for the report + wiki PROMPT text so skipped study days aren't credited.
    Archiving still operates on the full `collected`, so skipped days are preserved
    in archive/ as a record that the topic was assigned — they just don't synthesise.
    """
    if "Daily Study" not in collected:
        return collected
    kept = [e for e in collected["Daily Study"] if _study_is_complete(e["content"])]
    out = dict(collected)
    if kept:
        out["Daily Study"] = kept
    else:
        out.pop("Daily Study", None)
    return out


def _split_journal_sections(body: str) -> dict[str, str]:
    """Split a journal day's body into {section_heading: content}.

    Journal files use level-2 headings (## A Calendar of Wisdom, ## Al-Anon, …)
    separated by '---' rules. Returns only the JOURNAL_SECTIONS that have content.
    """
    out: dict[str, str] = {}
    # Match each "## Heading\n...content..." up to the next "## " or end of text.
    for m in re.finditer(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s+|\Z)", body, re.MULTILINE | re.DOTALL):
        heading = m.group(1).strip()
        if heading not in JOURNAL_SECTIONS:
            continue
        # Drop trailing horizontal rules and whitespace left by the section split
        content = re.sub(r"\n-{3,}\s*$", "", m.group(2)).strip()
        if content:
            out[heading] = content
    return out


def collect_journal_for_week(week_start: date) -> dict[str, list[dict]]:
    """Collect the perennial-journal entries for the Sun–Sat week beginning week_start.

    Journal files are _journal/MM-DD.md (one per calendar day, year in the
    frontmatter date_full). We read each day in the week, confirm the entry's
    year matches the week (defensive — the files are perennial and may hold
    other years in future), and return {section: [{date, content}]} keyed by the
    four JOURNAL_SECTIONS. These feed the report ONLY — never wiki or archive.
    """
    week_end = week_start + timedelta(days=6)
    collected: dict[str, list[dict]] = {}
    d = week_start
    while d <= week_end:
        path = JOURNAL_DIR / f"{d:%m-%d}.md"
        d_iso = d.isoformat()
        d += timedelta(days=1)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Confirm this file's entry is for the week's year (frontmatter date_full).
        ym = re.search(r"date_full:\s*\"?[A-Za-z]+ \d{1,2},\s*(\d{4})", text)
        if ym and int(ym.group(1)) != week_start.year:
            continue
        body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
        for section, content in _split_journal_sections(body).items():
            collected.setdefault(section, []).append(
                {"date": d_iso, "filename": path.name, "content": content})
    # Preserve a stable, human-sensible section order
    return {s: collected[s] for s in JOURNAL_SECTIONS if s in collected}


def collect_outbox_before(days: int = 7, sections=None) -> dict:
    """Return outbox content OLDER than the normal lookback window (for catch-up runs)."""
    cutoff = date.today() - timedelta(days=days)
    target_sections = sections if sections is not None else OUTBOX_SECTIONS
    collected = {}
    for section in target_sections:
        section_dir = OUTBOX_DIR / section
        if not section_dir.exists():
            continue
        entries = []
        for path in sorted(section_dir.glob("*.md")):
            if path.stem == "_misc":
                continue
            try:
                file_date = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
            except (ValueError, IndexError):
                continue
            if file_date >= cutoff:
                continue
            content = path.read_text(encoding="utf-8").strip()
            if content:
                entries.append({"date": file_date.isoformat(),
                                "filename": path.name,
                                "content": content})
        if entries:
            collected[section] = entries
    return collected


def archive_processed(collected: dict, dry_run: bool) -> int:
    """Move processed outbox files to vault/archive/<section>/ mirroring _outbox structure."""
    count = 0
    for section, entries in collected.items():
        dest_dir = ARCHIVE_DIR / section
        for e in entries:
            src = OUTBOX_DIR / section / e["filename"]
            if not src.exists():
                continue
            if dry_run:
                print(f"  [DRY RUN] Would archive: _outbox/{section}/{e['filename']}")
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                src.rename(dest_dir / e["filename"])
            count += 1
    return count


def format_outbox_for_prompt(collected: dict) -> str:
    parts = []
    for section, entries in collected.items():
        parts.append(f"## {section}")
        for e in entries:
            parts.append(f"### {e['date']}\n{e['content']}")
    return "\n\n".join(parts)


# ── Wiki index ─────────────────────────────────────────────────────────────

def build_wiki_index() -> list[dict]:
    """Return [{rel_path, title, snippet}] for all __wiki/ pages."""
    index = []
    for path in sorted(WIKI_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        title_m = re.search(r'^title:\s*"?([^"\n]+)"?', text, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else path.stem
        body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL).strip()
        snippet = body[:200].replace("\n", " ").strip()
        index.append({"path": path, "rel": str(path.relative_to(WIKI_DIR)),
                      "title": title, "snippet": snippet})
    return index


def format_wiki_index(index: list[dict]) -> str:
    return "\n".join(f"- [{e['rel']}] {e['title']}: {e['snippet'][:120]}…"
                     for e in index)


# ── Wiki synthesis ─────────────────────────────────────────────────────────

WIKI_SYSTEM = """\
You are maintaining a personal knowledge wiki for a researcher and analyst named Sean.
The wiki (in __wiki/) contains articles on topics he studies and cares about.

Your task: given new content from _outbox/, update the wiki.

Rules:
1. For each existing wiki page relevant to the new content, write an updated version
   incorporating the new knowledge. Preserve all existing content unless directly
   contradicted or superseded.
2. If new content introduces a topic not yet in the wiki, create a new article.
3. For Reflections content: append verbatim (clean formatting only) under a
   "## Reflections" heading at the bottom of the most relevant wiki page.
4. Do not address Sean directly. Do not use "I" as though speaking as Sean.
   If you must refer to the note-taker, use "you."
5. Return ONLY the delimiter-formatted blocks below — no JSON, no preamble, no commentary.

Format (repeat for each page to update or create):

<<< UPDATE: relative/path/PageName.md >>>
[full markdown content of the page]
<<< END >>>

<<< CREATE: relative/path/NewPage.md >>>
[full markdown content of the new page]
<<< END >>>
"""


def run_wiki_synthesis(outbox_text: str, wiki_index: list[dict], dry_run: bool) -> int:
    """Update __wiki/ pages based on new outbox content. Returns number of pages updated."""
    index_str = format_wiki_index(wiki_index)
    prompt = (
        f"New content from the past week:\n\n{outbox_text}\n\n"
        f"Current wiki index:\n{index_str}\n\n"
        "Which wiki pages need updating? Write the updated content for each."
    )

    raw = complete(
        system=WIKI_SYSTEM, user=prompt, max_tokens=8192,
        anthropic_model=MODEL,
        project="seanipedia", script="weekly_report.py", label="wiki",
    ).strip()
    pattern = re.compile(
        r"<<<\s*(UPDATE|CREATE):\s*(.+?)\s*>>>\n(.*?)<<<\s*END\s*>>>",
        re.DOTALL,
    )
    matches = pattern.findall(raw)

    if not matches:
        print(f"  Wiki synthesis: no page blocks found in response.")
        return 0

    count = 0
    for action, rel_path, content in matches:
        rel_path = rel_path.strip().removeprefix("__wiki/").removeprefix("__wiki\\")
        content  = content.strip()
        if not rel_path or not content:
            continue
        dest = WIKI_DIR / rel_path
        if dry_run:
            verb = "create" if action == "CREATE" else "update"
            print(f"  [DRY RUN] Would {verb}: __wiki/{rel_path}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content + "\n", encoding="utf-8")
            verb = "Created" if action == "CREATE" else "Updated"
            print(f"  {verb}: __wiki/{rel_path}")
        count += 1

    return count


# ── Weekly report ──────────────────────────────────────────────────────────

REPORT_SYSTEM = """\
You are generating a weekly report for Sean — a researcher, analyst, and student
working at the intersection of national security, AI policy, and personal growth.

Write a clean, well-formatted Markdown report covering the past week. Sections:

1. **What You Did** — a brief narrative of the week's main activities and focus areas
2. **Study Themes** — key topics from Daily Study, with the most important takeaways
3. **Quotes & Reading** — notable quotes or passages encountered this week. Include
   the week's Sententiae Antiquae (ancient Greek/Latin quotes) and any new Words
   (vocabulary) from the daily journal practice here.
4. **Personal Insights** — themes from the journal, therapy, or reflections (treat
   sensitively). Draw on the daily "A Calendar of Wisdom" and "Al-Anon" readings
   where they connect to the week's thinking — surface a throughline, don't just
   list them.

The input includes both the week's OUTBOX sections (Quotes, Daily Study, Notes,
Reflections, Therapy) and the daily JOURNAL practice (A Calendar of Wisdom, Al-Anon,
Sententiae Antiquae, Words). Weave the journal in naturally rather than cataloguing it.

Tone: warm, direct, like a thoughtful assistant reviewing the week with him.
Do not be sycophantic. If a section has no content, omit it.
Do not include a title or date header — those are added automatically.
Return only the Markdown report body — no preamble, no JSON.
"""


def generate_report(outbox_text: str, week_start: date, week_end: date) -> str:
    label = f"{week_start.strftime('%B %-d')} – {week_end.strftime('%B %-d, %Y')}"
    prompt = (f"Week of {label} "
              f"(Sunday {week_start.isoformat()} through Saturday {week_end.isoformat()}):"
              f"\n\n{outbox_text}")

    return complete(
        system=REPORT_SYSTEM, user=prompt, max_tokens=4096,
        anthropic_model=MODEL,
        project="seanipedia", script="weekly_report.py", label="report",
    ).strip()


# ── Therapy bootstrap ──────────────────────────────────────────────────────

THERAPY_SYSTEM = """\
You are synthesizing a longitudinal record of Sean's therapy sessions for his personal wiki.
Sean is a researcher and analyst who values honest, unsentimental self-knowledge.

Your task: read all therapy session notes chronologically and write a single wiki page
(__wiki/Therapy.md) that captures the full arc across time.

The page should include:

1. **Overview** — a brief orienting paragraph: when therapy started, approximate cadence,
   and the broadest shape of the journey.
2. **Recurring Themes** — patterns, concerns, or tensions that appear repeatedly across
   sessions. Note when they first appeared and whether they evolved or persisted.
3. **Arc of Growth** — areas where Sean demonstrably changed, made progress, or resolved
   something over the course of the record.
4. **Persistent Struggles** — things that remain unresolved, recur without resolution,
   or show regression. Be honest — this is for Sean's own reflection, not a progress report.
5. **Notable Sessions** — specific sessions that marked a turning point, breakthrough,
   or significant moment. Cite by date (YYYY-MM-DD).
6. **Recent State** (last ~3 months) — where things stand as of the most recent sessions.

Rules:
- Treat all content with discretion but without euphemism. Say what is actually there.
- Do not address Sean directly. Write in third person ("the sessions show…", "a recurring theme is…").
- Use the session dates as anchors throughout — this is a longitudinal document, not a summary.
- If a session note is very sparse (a few words), incorporate it lightly or skip it.
- Return only the Markdown content of the wiki page — no preamble.
"""


def collect_all_therapy() -> list[dict]:
    """Return all therapy session files sorted chronologically."""
    therapy_dir = OUTBOX_DIR / "Therapy"
    if not therapy_dir.exists():
        return []
    entries = []
    for path in sorted(therapy_dir.glob("*.md")):
        if path.stem == "_misc":
            continue
        try:
            file_date = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
        except (ValueError, IndexError):
            continue
        content = path.read_text(encoding="utf-8").strip()
        if content:
            entries.append({"date": file_date.isoformat(), "content": content})
    return entries


def run_therapy_bootstrap(dry_run: bool) -> None:
    """Synthesize all therapy sessions into __wiki/Therapy.md."""
    print("Collecting all therapy sessions…")
    sessions = collect_all_therapy()
    if not sessions:
        print("  No therapy sessions found in _outbox/Therapy/.")
        return
    print(f"  {len(sessions)} session(s) found ({sessions[0]['date']} → {sessions[-1]['date']}).")

    parts = []
    for s in sessions:
        parts.append(f"### {s['date']}\n{s['content']}")
    prompt = "Therapy session notes (chronological):\n\n" + "\n\n".join(parts)

    total_chars = sum(len(s["content"]) for s in sessions)
    approx_tokens = total_chars // 4
    print(f"  Approx input size: {approx_tokens:,} tokens.")

    print("Synthesizing therapy notes…")
    page_content = complete(
        system=THERAPY_SYSTEM, user=prompt, max_tokens=4096,
        anthropic_model=MODEL,
        project="seanipedia", script="weekly_report.py", label="therapy",
    ).strip()

    today = date.today()
    header = (
        f"---\ntitle: Therapy\ntype: wiki\nupdated: {today.isoformat()}\n---\n\n"
        f"# Therapy\n\n"
        f"*Bootstrapped {today.isoformat()} from {len(sessions)} sessions "
        f"({sessions[0]['date']} – {sessions[-1]['date']}). "
        f"Updated weekly by weekly_report.py.*\n\n"
    )
    full_content = header + page_content + "\n"

    dest = WIKI_DIR / "Therapy.md"
    if dry_run:
        print(f"\n  [DRY RUN] Would write: __wiki/Therapy.md")
        print(page_content[:600] + "…")
    else:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(full_content, encoding="utf-8")
        print(f"  Written: __wiki/Therapy.md")
        send_macos_notification("Therapy bootstrap complete",
                                f"{len(sessions)} sessions synthesised into __wiki/Therapy.md")


# ── Catch-up synthesis ─────────────────────────────────────────────────────

CATCHUP_SECTIONS = ["Quotes", "Daily Study", "Notes", "Reflections"]  # Therapy handled separately


def run_catchup(days: int, dry_run: bool) -> None:
    """Synthesize outbox content older than the normal window, then archive it."""
    print(f"Collecting _outbox/ content older than {days} days (excluding Therapy)…")
    collected = collect_outbox_before(days=days, sections=CATCHUP_SECTIONS)

    if not collected:
        print("  Nothing in the backlog. All caught up.")
        return

    total = sum(len(v) for v in collected.values())
    print(f"  {total} file(s) across {len(collected)} section(s).")
    for section, entries in collected.items():
        print(f"    {section}: {entries[0]['date']} → {entries[-1]['date']} ({len(entries)} files)")

    outbox_text = format_outbox_for_prompt(collected)

    print("\nBuilding wiki index…")
    wiki_index = build_wiki_index()
    print(f"  {len(wiki_index)} wiki pages indexed.")

    # Process one section at a time to keep each API call small and focused
    wiki_pages_updated = 0
    any_error = False
    for section, entries in collected.items():
        section_text = format_outbox_for_prompt({section: entries})
        print(f"Running wiki synthesis for {section} ({len(entries)} files)…")
        n = run_wiki_synthesis(section_text, wiki_index, dry_run=dry_run)
        print(f"  {n} wiki page(s) updated.")
        if n == 0 and not dry_run:
            print(f"  Warning: {section} synthesis returned 0 updates — check for errors above.")
            any_error = True
        wiki_pages_updated += n

    if any_error and not dry_run:
        print("\nOne or more sections had errors — skipping archive to avoid data loss.")
        print("Fix the errors above, then re-run.")
        return

    print("\nArchiving backlog files…")
    # Also archive Therapy backlog — already synthesised via --therapy-bootstrap
    therapy_collected = collect_outbox_before(days=days, sections=["Therapy"])
    all_collected = {**collected, **therapy_collected}
    archived = archive_processed(all_collected, dry_run=dry_run)
    if dry_run:
        print(f"  [DRY RUN] Would archive {archived} file(s).")
    else:
        print(f"  {archived} file(s) archived to archive/.")
        send_macos_notification("Catch-up complete",
                                f"{wiki_pages_updated} wiki page(s) updated, {archived} file(s) archived.")

    print("\nCatch-up complete.")


# ── Notifications ──────────────────────────────────────────────────────────

def send_macos_notification(title: str, message: str) -> None:
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
    except Exception as exc:
        print(f"  macOS notification failed: {exc}")


# ── Per-week processing ────────────────────────────────────────────────────

def process_week(week_start: date, args) -> None:
    """Generate the report (and wiki updates) for one Sun–Sat week.

    Always writes a report — even for a quiet week with no logged activity —
    so the weekly cadence is never broken.  Records the week in the state file
    on success so it isn't reported again.
    """
    week_end = week_start + timedelta(days=6)
    label = f"{week_start.strftime('%B %-d')} – {week_end.strftime('%B %-d, %Y')}"
    # Filename = the Sunday AFTER the week's Saturday (the review/publish day),
    # e.g. the May 31–Jun 6 week is saved as 20260607.md. The H1 label still
    # shows the period summarised; only the filename uses the following Sunday.
    report_sunday = week_start + timedelta(days=7)
    fname = f"{report_sunday.strftime('%Y%m%d')}.md"
    report_path = WEEKLY_DIR / fname

    print(f"\n=== Week of {label}  (Sun {week_start.isoformat()} → Sat {week_end.isoformat()}) ===")
    collected = collect_outbox_for_week(week_start)
    journal_collected = collect_journal_for_week(week_start)

    report_header = (
        f"---\ndate: {date.today().isoformat()}\ntype: weekly-report\n"
        f"week_start: {week_start.isoformat()}\nweek_end: {week_end.isoformat()}\n---\n"
        f"# Weekly Report — {label}\n\n"
    )

    # ── Quiet week: nothing in outbox OR journal — still emit a placeholder ──
    if not collected and not journal_collected:
        print("  No logged activity this week — writing a brief placeholder.")
        placeholder = (
            "_No activity was logged in the vault for this week._\n\n"
            "Travel, leave, or simply a quiet stretch. The weekly cadence is "
            "preserved so the record stays continuous."
        )
        if args.dry_run:
            print(f"  [DRY RUN] Would write placeholder: _weekly reports/{fname}")
            return
        WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_header + placeholder + "\n", encoding="utf-8")
        write_last_reported_week(week_start)
        print(f"  Placeholder saved: _weekly reports/{fname}")
        return

    n_outbox  = sum(len(v) for v in collected.values())
    n_journal = sum(len(v) for v in journal_collected.values())
    print(f"  {n_outbox} outbox file(s)/{len(collected)} section(s); "
          f"{n_journal} journal entr(ies)/{len(journal_collected)} section(s).")

    # Wiki + archive operate on OUTBOX ONLY. The report draws on both the
    # outbox (the week's output) and the journal (the perennial daily practice).
    # Daily Study only feeds synthesis for days checked off as completed; skipped
    # days are still archived (below) but don't get credited in the wiki or report.
    collected_for_synthesis = _filter_completed_study(collected)
    outbox_text = format_outbox_for_prompt(collected_for_synthesis)
    report_text = format_outbox_for_prompt({**collected_for_synthesis, **journal_collected})

    # ── 1. Wiki synthesis + archive — outbox only; journal is never archived ─
    wiki_pages_updated = 0
    if collected and not args.no_wiki:
        print("  Building wiki index…")
        wiki_index = build_wiki_index()
        print(f"  {len(wiki_index)} wiki pages indexed.")
        print("  Running wiki synthesis…")
        wiki_pages_updated = run_wiki_synthesis(outbox_text, wiki_index, dry_run=args.dry_run)
        print(f"  {wiki_pages_updated} wiki page(s) updated.")

        if not args.no_archive:
            archived = archive_processed(collected, dry_run=args.dry_run)
            verb = "[DRY RUN] Would archive" if args.dry_run else "Archived"
            print(f"  {verb} {archived} file(s).")

    # ── 2. Weekly report ────────────────────────────────────────────────────
    print("  Generating weekly report…")
    report_md = generate_report(report_text, week_start, week_end)
    full_report = report_header + report_md

    if args.dry_run:
        print(f"  [DRY RUN] Would write: _weekly reports/{fname}")
        print("  " + report_md[:400].replace("\n", "\n  ") + "…")
        return

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    report_path.write_text(full_report + "\n", encoding="utf-8")
    write_last_reported_week(week_start)
    print(f"  Report saved: _weekly reports/{fname}")

    # ── 3. macOS notification ───────────────────────────────────────────────
    wiki_str = (f"{wiki_pages_updated} wiki page{'s' if wiki_pages_updated != 1 else ''} updated. "
                if not args.no_wiki else "")
    teaser = next((l.strip(" #") for l in report_md.splitlines()
                   if l.strip() and not l.startswith("#")), "")
    teaser = teaser[:80] + ("…" if len(teaser) > 80 else "")
    send_macos_notification(f"Weekly Report — {week_start.strftime('%b %-d')}",
                            f"{wiki_str}{teaser}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Weekly synthesis and report")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing")
    parser.add_argument("--no-wiki", action="store_true",
                        help="Skip wiki updates, generate report only")
    parser.add_argument("--no-archive", action="store_true",
                        help="Skip archiving processed outbox files")
    parser.add_argument("--days", type=int, default=7,
                        help="Number of days to look back (default: 7)")
    parser.add_argument("--therapy-bootstrap", action="store_true",
                        help="One-time: synthesize all therapy sessions into __wiki/Therapy.md, then exit")
    parser.add_argument("--catchup", action="store_true",
                        help="One-time: synthesize + archive outbox backlog older than --days window, then exit")
    parser.add_argument("--week", type=str, default=None,
                        help="Force a specific week by any date within it (YYYY-MM-DD); "
                             "normalised to that week's Sunday. Bypasses catch-up logic.")
    args = parser.parse_args()

    today = date.today()

    if args.therapy_bootstrap:
        run_therapy_bootstrap(dry_run=args.dry_run)
        return

    if args.catchup:
        run_catchup(days=args.days, dry_run=args.dry_run)
        return

    # ── Explicit single-week override (manual / testing) ────────────────────
    if args.week:
        try:
            anchor = date.fromisoformat(args.week)
        except ValueError:
            print(f"Invalid --week date '{args.week}'. Use YYYY-MM-DD.")
            return
        week_start = most_recent_sunday(anchor)
        print(f"Forced single week starting Sunday {week_start.isoformat()}.")
        process_week(week_start, args)
        print("\nDone.")
        return

    # ── Scheduled path: report every completed-but-unreported Sun–Sat week ──
    # Idempotent — generates only what's missing, so it's safe to run any day
    # and any number of times.  Catches up one report per missed week.
    targets = weeks_to_report(today)
    if not targets:
        print("All completed weeks already reported. Nothing to do.")
        return

    print(f"{len(targets)} week(s) to report: "
          + ", ".join(w.isoformat() for w in targets))
    for week_start in targets:
        process_week(week_start, args)

    print("\nWeekly report run complete.")


if __name__ == "__main__":
    main()
