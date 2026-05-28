"""
weekly_report.py — Weekly synthesis and report.

Every Sunday (or on demand):
  1. Collects all _outbox/ content from the past 7 days
  2. Updates relevant __wiki/ pages with new knowledge
  3. Generates a weekly report: themes, study topics, personal insights, random word
  4. Saves report to vault/_weekly reports/YYYYMMDD.md

Usage:
  python3 weekly_report.py                      # run weekly synthesis + report
  python3 weekly_report.py --dry-run            # preview without writing
  python3 weekly_report.py --no-wiki            # skip wiki updates, report only
  python3 weekly_report.py --no-archive         # skip archiving processed files
  python3 weekly_report.py --therapy-bootstrap  # one-time: synthesize all therapy → __wiki/Therapy.md
  python3 weekly_report.py --catchup            # one-time: synthesize + archive pre-window outbox backlog
"""

import argparse
import os
import random
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH        = Path(os.environ.get("VAULT_PATH", "./vault"))
OUTBOX_DIR        = VAULT_PATH / "_outbox"
ARCHIVE_DIR       = VAULT_PATH / "archive"
WIKI_DIR          = VAULT_PATH / "__wiki"
WEEKLY_DIR        = VAULT_PATH / "_weekly reports"
WORDS_FILE        = VAULT_PATH / "_words.md"


client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL  = "claude-opus-4-7"

OUTBOX_SECTIONS = ["Quotes", "Daily Study", "Notes", "Reflections", "Therapy"]


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


def collect_outbox_by_dates(max_dates: int = 7) -> tuple[dict[str, list[dict]], list[date]]:
    """Collect the first max_dates unprocessed day-chunks from _outbox/.

    Rather than a rolling time window, this finds every unique date that
    appears across all sections, sorts them oldest-first, and takes the
    first max_dates.  Only files whose dates fall in that window are
    returned.  Files beyond the window are left in place for the next run.

    Returns:
        collected  — {section: [{date, filename, content}]}
        window     — sorted list of date objects included (≤ max_dates long)
    """
    # Pass 1: union of all dates across every section
    all_dates: set[date] = set()
    for section in OUTBOX_SECTIONS:
        section_dir = OUTBOX_DIR / section
        if not section_dir.exists():
            continue
        for path in section_dir.glob("*.md"):
            if path.stem == "_misc":
                continue
            try:
                all_dates.add(date(int(path.stem[:4]),
                                   int(path.stem[4:6]),
                                   int(path.stem[6:8])))
            except (ValueError, IndexError):
                continue

    if not all_dates:
        return {}, []

    window: list[date] = sorted(all_dates)[:max_dates]
    window_set = set(window)

    # Pass 2: collect files whose dates fall in the window
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
            if file_date not in window_set:
                continue
            content = path.read_text(encoding="utf-8").strip()
            if content:
                entries.append({"date": file_date.isoformat(),
                                "filename": path.name,
                                "content": content})
        if entries:
            collected[section] = entries

    return collected, window


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

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=WIKI_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
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
3. **Quotes & Reading** — notable quotes or passages encountered this week
4. **Personal Insights** — themes from journal, therapy, or reflections (treat sensitively)
5. **Word of the Week** — one randomly selected vocabulary word with its definition

Tone: warm, direct, like a thoughtful assistant reviewing the week with him.
Do not be sycophantic. If a section has no content, omit it.
Do not include a title or date header — those are added automatically.
Return only the Markdown report body — no preamble, no JSON.
"""


def random_word() -> str:
    """Pick a random word from _words.md."""
    if not WORDS_FILE.exists():
        return ""
    lines = [l for l in WORDS_FILE.read_text(encoding="utf-8").splitlines()
             if re.match(r"^\d+\.", l)]
    if not lines:
        return ""
    return random.choice(lines)


def generate_report(outbox_text: str, week_of: date) -> str:
    word = random_word()
    prompt = (
        f"Week of {week_of.strftime('%B %-d, %Y')}:\n\n{outbox_text}\n\n"
        f"Word of the week (randomly selected from vocabulary list):\n{word}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=REPORT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


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

    print("Calling Claude for therapy synthesis…")
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=THERAPY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    page_content = resp.content[0].text.strip()

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
    args = parser.parse_args()

    today = date.today()
    ymd   = today.strftime("%Y%m%d")

    if args.therapy_bootstrap:
        run_therapy_bootstrap(dry_run=args.dry_run)
        return

    if args.catchup:
        run_catchup(days=args.days, dry_run=args.dry_run)
        return

    print("Collecting _outbox/ content (first 7 unprocessed dates)…")
    collected, window = collect_outbox_by_dates(max_dates=7)

    if not collected:
        print("No _outbox/ content found. Nothing to do.")
        return

    total_entries = sum(len(v) for v in collected.values())
    date_range = f"{window[0].isoformat()} → {window[-1].isoformat()}" if window else "?"
    print(f"  {total_entries} file(s) across {len(collected)} section(s) "
          f"({len(window)} date(s): {date_range}).")

    outbox_text = format_outbox_for_prompt(collected)

    # ── 1. Wiki synthesis ──────────────────────────────────────────────────
    wiki_pages_updated = 0
    if not args.no_wiki:
        print("\nBuilding wiki index…")
        wiki_index = build_wiki_index()
        print(f"  {len(wiki_index)} wiki pages indexed.")
        print("Running wiki synthesis…")
        wiki_pages_updated = run_wiki_synthesis(outbox_text, wiki_index, dry_run=args.dry_run)
        n = wiki_pages_updated
        print(f"  {n} wiki page(s) updated.")

        if not args.no_archive:
            print("Archiving processed outbox files…")
            archived = archive_processed(collected, dry_run=args.dry_run)
            if args.dry_run:
                print(f"  [DRY RUN] Would archive {archived} file(s).")
            else:
                print(f"  {archived} file(s) archived to archive/.")

    # ── 2. Weekly report ───────────────────────────────────────────────────
    # Base week_of on the actual dates being processed, not today.
    week_of = window[0] if window else today
    print("\nGenerating weekly report…")
    report_md = generate_report(outbox_text, week_of)

    report_header = (
        f"---\ndate: {today.isoformat()}\ntype: weekly-report\n---\n\n"
        f"# Weekly Report — {week_of.strftime('%B %-d, %Y')}\n\n"
    )
    full_report = report_header + report_md

    report_path = WEEKLY_DIR / f"{ymd}.md"
    if args.dry_run:
        print(f"\n  [DRY RUN] Would write: _weekly reports/{ymd}.md")
        print(report_md[:500] + "…")
    else:
        WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(full_report + "\n", encoding="utf-8")
        print(f"  Report saved: _weekly reports/{ymd}.md")

    # ── 4. macOS notification ──────────────────────────────────────────────
    if not args.dry_run:
        wiki_str = f"{wiki_pages_updated} wiki page{'s' if wiki_pages_updated != 1 else ''} updated. " if not args.no_wiki else ""
        # Pull first non-empty line of report body as a teaser
        teaser = next((l.strip(" #") for l in report_md.splitlines() if l.strip() and not l.startswith("#")), "")
        teaser = teaser[:80] + ("…" if len(teaser) > 80 else "")
        notif_msg = f"{wiki_str}{teaser}"
        send_macos_notification(f"Weekly Report — {week_of.strftime('%b %-d')}", notif_msg)

    print("\nWeekly report complete.")


if __name__ == "__main__":
    main()
