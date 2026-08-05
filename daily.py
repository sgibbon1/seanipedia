"""
daily.py — Daily note generation and end-of-day parsing.

Usage:
  python3 daily.py --generate   # 6am: create today's Obsidian daily note
  python3 daily.py --parse      # 10pm: route today's content into _outbox/

Sections in the daily note (in order):
  ## A Calendar of Wisdom  ─┐
  ## Al-Anon                ├─ → _journal/MM-DD.md
  ## Sententiae Antiquae    │
  ## Words                 ─┘  (+ new words → _words.md)
  ## Quotes                    → _outbox/Quotes/YYYYMMDD.md
  ## Daily Study — [topic]     → _outbox/Daily Study/YYYYMMDD.md
  ## Notes                     → _outbox/Notes/YYYYMMDD.md
  ## Reflections               → _outbox/Reflections/YYYYMMDD.md (verbatim)
  ## Therapy                   → _outbox/Therapy/YYYYMMDD.md
  ## Daily Intelligence Brief  → archive/Daily Intelligence Brief/YYYYMMDD.md
                                  (saved permanently, but NOT _outbox — never
                                  feeds the wiki or weekly reports)

Empty sections are skipped. _outbox pages use YYYYMMDD filenames;
_journal pages use MM-DD.md filenames.
"""

import argparse
import os
import re
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH        = Path(os.environ.get("VAULT_PATH", "./vault"))
NATSEC_DB_PATH    = os.environ.get("NATSEC_DB_PATH", "")
OUTBOX_DIR        = VAULT_PATH / "_outbox"
JOURNAL_DIR       = VAULT_PATH / "_journal"
STUDY_DIR         = OUTBOX_DIR / "Daily Study"
THERAPY_DIR       = OUTBOX_DIR / "Therapy"
QUOTES_DIR        = OUTBOX_DIR / "Quotes"
NOTES_DIR         = OUTBOX_DIR / "Notes"
REFLECTIONS_DIR   = OUTBOX_DIR / "Reflections"
WORDS_FILE        = VAULT_PATH / "_words.md"
ARCHIVE_DIR       = VAULT_PATH / "archive"
BRIEF_ARCHIVE_DIR = ARCHIVE_DIR / "Daily Intelligence Brief"
WEEKLY_REPORTS_DIR = VAULT_PATH / "_weekly reports"
INBOX_DIR         = VAULT_PATH / "_inbox"
# Set STUDY_CALENDAR in .env to the macOS Calendar name to check for
# Study/Output/Career events (see README).
CALENDAR_NAME    = os.environ.get("STUDY_CALENDAR", "")

SCRIPT_DIR = Path(__file__).parent


# ── Calendar helpers ───────────────────────────────────────────────────────

def get_study_topic(for_date: date) -> tuple[str, str]:
    """
    Query Mac Calendar via AppleScript for a Study/Output/Career event today.
    Returns (short_title, description). Falls back to empty strings if not found
    (including when CALENDAR_NAME/STUDY_CALENDAR isn't set — see README).
    """
    if not CALENDAR_NAME:
        return "", ""
    # AppleScript string literal — CALENDAR_NAME comes from our own .env, not
    # untrusted input, but escape embedded quotes defensively anyway.
    calendar_name_escaped = CALENDAR_NAME.replace('"', '\\"')
    script = f"""
    tell application "Calendar"
        set theDate to date "{for_date.strftime('%A, %B %d, %Y')}"
        set dayStart to theDate - (time of theDate)
        set dayEnd to dayStart + (86400)
        set output to ""
        repeat with cal in calendars
            if name of cal is "{calendar_name_escaped}" then
                set evts to every event of cal whose start date >= dayStart and start date <= dayEnd
                repeat with evt in evts
                    set t to summary of evt
                    set tStr to t as string
                    if tStr contains "Study:" or tStr contains "Output:" or tStr contains "Career:" or tStr contains "Writing:" then
                        set d to description of evt
                        if d is missing value then set d to ""
                        set output to tStr & "|||" & (d as string)
                        exit repeat
                    end if
                end repeat
            end if
            if output is not "" then exit repeat
        end repeat
        return output
    end tell
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=60
        )
        raw = result.stdout.strip()
        if "|||" in raw:
            title, desc = raw.split("|||", 1)
            return title.strip(), desc.strip()
        elif raw:
            return raw.strip(), ""
    except Exception:
        pass
    return "", ""


# ── Generate ───────────────────────────────────────────────────────────────

def _inject_todays_jobs(out_path: Path, today: date) -> None:
    """Pull today's jobs from the natsec DB and append a ## Today's Jobs section."""
    if not NATSEC_DB_PATH:
        return
    db_path = Path(NATSEC_DB_PATH)
    if not db_path.exists():
        return

    today_prefix = today.isoformat()  # "2026-05-28"
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT company, title, location, url, fit_score "
            "FROM jobs WHERE first_seen_utc LIKE ? "
            "ORDER BY COALESCE(fit_score, 0) DESC",
            (f"{today_prefix}%",),
        ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"  Jobs DB error: {exc}")
        return

    if not rows:
        return

    # Group by company, sorted alphabetically
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["company"], []).append(r)

    lines: list[str] = []
    for company in sorted(grouped):
        lines.append(f"### {company}")
        for r in sorted(grouped[company], key=lambda x: x["title"].lower()):
            loc = r["location"] or "Unknown"
            score = r["fit_score"]
            score_str = f" · fit {score}" if score is not None else ""
            lines.append(f"- **{r['title']}** ({loc}){score_str}")
            lines.append(f"  {r['url']}")
        lines.append("")

    section_body = "\n".join(lines).strip()
    new_section = f"\n\n---\n## Today's Jobs\n\n{section_body}\n"

    text = out_path.read_text(encoding="utf-8")
    text = re.sub(r"\n\n---\n## Today's Jobs\n.*?(?=\n## |\Z)", "", text, flags=re.DOTALL)
    # Strip trailing --- so it doesn't double up
    text = re.sub(r"\n---\s*$", "", text.rstrip())
    out_path.write_text(text + new_section + "\n", encoding="utf-8")
    print(f"  Jobs: {len(rows)} job(s) injected from natsec DB.")


def _apply_carryover(out_path: Path, inbox_dir: Path) -> None:
    """If brief_carryover.md exists, inject its unread items into Today.md and delete it."""
    carryover_path = inbox_dir / "brief_carryover.md"
    if not carryover_path.exists():
        return

    raw = carryover_path.read_text(encoding="utf-8")

    date_m = re.search(r"<!-- carryover_date: (.+?) -->", raw)
    date_label = date_m.group(1) if date_m else "previous day"
    raw_body = re.sub(r"<!--.*?-->\n?", "", raw)

    lines = raw_body.splitlines()
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    in_item = False

    for line in lines:
        if re.match(r"^- \[[ x]\] ", line):
            if current:
                segments.append(("other", "\n".join(current)))
                current = []
            in_item = True
            current = [line]
        elif in_item and (line.startswith("  ") or line == ""):
            current.append(line)
        else:
            if in_item:
                segments.append(("item", "\n".join(current).rstrip()))
                current = []
                in_item = False
            current.append(line)
    if current:
        segments.append(("item" if in_item else "other", "\n".join(current).rstrip()))

    kept: list[str] = []
    skip_next = False
    for seg_type, content in segments:
        if seg_type == "item":
            if content.startswith("- [x]"):
                skip_next = True
                continue
            kept.append(content)
            skip_next = False
        else:
            cleaned = re.sub(r"(---\s*\n?){2,}", "---\n", content.strip()).strip()
            if cleaned and not skip_next:
                kept.append(cleaned)
            skip_next = False

    carryover_path.unlink()

    if not kept:
        print("  Brief carryover: all items read — nothing to inject.")
        return

    body = re.sub(r"\n{3,}", "\n\n", "\n\n".join(kept)).strip()
    n_unread = len(re.findall(r"^- \[ \]", body, re.MULTILINE))
    section = f"\n---\n\n## Daily Intelligence Brief\n\n*{n_unread} unread from {date_label}*\n\n{body}\n"

    text = out_path.read_text(encoding="utf-8")
    out_path.write_text(text.rstrip() + "\n" + section, encoding="utf-8")
    print(f"  Brief carryover: {n_unread} unread item(s) injected from {date_label}.")


def refresh_study(today: date):
    """Re-populate the ## Daily Study heading in an existing note from calendar."""
    out_path = INBOX_DIR / "Today.md"
    if not out_path.exists():
        stem = today.strftime("%m-%d")
        out_path = INBOX_DIR / f"{stem}.md"
    if not out_path.exists():
        print(f"No note found for {today}. Run --generate first.")
        return

    study_title, study_desc = get_study_topic(today)
    if not study_title:
        print("No Study/Output/Career event found in calendar for today.")
        return

    clean_title = re.sub(r'^(Study|Output|Career|Writing):\s*', '', study_title)
    new_heading = f"## Daily Study — {clean_title}"
    # Mirror generate(): a populated study day gets the completion checkbox that
    # weekly_report.py reads, with study_desc (if any) following it.
    checkbox = "- [ ] Completed"
    study_context = f"{checkbox}\n{study_desc}\n" if study_desc else f"{checkbox}\n"

    text = out_path.read_text(encoding="utf-8")

    # Replace bare "## Daily Study" heading (no topic suffix) with the populated one
    if re.search(r'^## Daily Study\s*$', text, re.MULTILINE):
        text = re.sub(r'^## Daily Study\s*$', new_heading, text, count=1, flags=re.MULTILINE)
        # Insert the checkbox + study_context after the now-populated heading
        text = text.replace(new_heading, new_heading + "\n" + study_context, 1)
        out_path.write_text(text, encoding="utf-8")
        print(f"Refreshed study heading: {clean_title}")
    else:
        print("Study heading already populated — nothing to do.")


def generate(today: date):
    out_path = INBOX_DIR / "Today.md"

    if out_path.exists():
        # Parse leftover note before creating today's (handles missed 3am run)
        raw = out_path.read_text(encoding="utf-8")
        stale_date = _get_note_date(raw, today - timedelta(days=1))
        print(f"Existing Today.md found (dated {stale_date}) — parsing before generating.")
        parse(stale_date)

    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    month_day    = today.strftime("%B %-d")
    date_display = today.strftime("%B %-d, %Y")
    sort_date    = today.strftime("%m-%d")

    month_slug = today.strftime("%B").lower()
    day_num    = today.day
    # TOC structure on the anarchist library page (verified against multiple anchors):
    #   toc1-3: front matter
    #   January:   header at toc4, days toc5-35
    #   February:  NO header,      days toc36-64  (always includes Feb 29)
    #   March-Nov: header then days
    #   December:  NO header,      days toc349-379
    _TOC_SECTIONS = [
        # (has_header, days_in_month)  — February always 29; non-leap years skip Feb 29 naturally
        (True,  31),  # January
        (False, 29),  # February
        (True,  31),  # March
        (True,  30),  # April
        (True,  31),  # May
        (True,  30),  # June
        (True,  31),  # July
        (True,  31),  # August
        (True,  30),  # September
        (True,  31),  # October
        (True,  30),  # November
        (False, 31),  # December
    ]
    toc_n = 3  # front matter
    for _m, (has_hdr, _days) in enumerate(_TOC_SECTIONS, start=1):
        if _m == today.month:
            if has_hdr:
                toc_n += 1
            toc_n += today.day
            break
        toc_n += (1 if has_hdr else 0) + _days

    # Off-by-one correction for a defect in the anarchist-library edition:
    # it is MISSING its "June 6" entry — the body jumps straight from "June 5"
    # (toc165) to "June 7" (toc166), so June has only 29 day-anchors, not 30.
    # That pushes every anchor from June 6 onward one too high. The shift would
    # normally run to year-end, but the _TOC_SECTIONS table above models December
    # as having no header (it actually does), and that single undercount happens
    # to cancel the June overcount from Dec 1 on. Net result: only Jun 6–Nov 30
    # resolve one day ahead, so we subtract one across exactly that window.
    # June 6 has no anchor at all in this edition, so it falls back to the nearest
    # prior day (June 5). Verified against the live page's body headings (2026-06);
    # remove this block if the site ever restores the missing June 6 entry.
    if (6, 6) <= (today.month, today.day) <= (11, 30):
        toc_n -= 1

    tolstoy_url = (
        f"https://theanarchistlibrary.org/library/leo-tolstoy-a-calendar-of-wisdom"
        f"#toc{toc_n}"
    )
    alanon_url = (
        f"https://dailyponderables.com/{month_slug}-{day_num}"
        f"-courage-to-change-{today.year}/"
    )

    # Resurfaced quotes from the lifetime collection (quotes_index.json, dealt by
    # quotes_daily as a shuffled deck so every saved quote comes back around).
    # These are for REREADING and are NOT re-archived: --parse only routes quotes
    # marked "(new)", which is how a genuinely new capture is flagged. Without
    # that split, injected quotes would be re-archived, re-indexed, and resurface
    # in a feedback loop that also corrupts the daily-capture record.
    quotes_block = "\n\n"
    try:
        import quotes_daily
        picked, ledger = quotes_daily.pick(
            quotes_daily.load_index(), quotes_daily.load_ledger(),
            quotes_daily.DEFAULT_COUNT)
        if picked:
            quotes_daily.save_ledger(ledger)
            quotes_block = ("\n*From the archive — mark anything new you add with "
                            "(new).*\n\n" + quotes_daily.render(picked) + "\n\n")
    except SystemExit:
        pass          # index not built yet — leave the section empty
    except Exception as exc:
        print(f"  (quotes: skipped — {exc})")

    study_title, study_desc = get_study_topic(today)
    if study_title:
        # Strip the "Study:" / "Output:" / "Career:" prefix from the calendar title
        clean_title = re.sub(r'^(Study|Output|Career|Writing):\s*', '', study_title)
        study_heading = f"## Daily Study — {clean_title}"
        # Completion checkbox: check this off in the daily note once you've actually
        # done the study. weekly_report.py only credits CHECKED days, so leaving it
        # unchecked means the topic was assigned but skipped. Only added when there
        # IS an assigned topic — a bare "## Daily Study" with nothing scheduled gets
        # no box (nothing to complete).
        # With a description, the box hugs the description text; without one, a blank
        # line keeps the section's spacing in line with every other heading.
        checkbox = "- [ ] Completed\n"
        study_context = f"{checkbox}{study_desc}\n" if study_desc else f"{checkbox}\n"
    else:
        study_heading = "## Daily Study"
        study_context = "\n\n"

    content = f"""---
date_sort: "{sort_date}"
date_display: "{month_day}"
date_full: "{date_display}"
type: journal
sections: [tolstoy, alanon, sententiae, words, quotes, study, notes, reflections, therapy]
---
# {month_day}
## [A Calendar of Wisdom]({tolstoy_url})


---
## [Al-Anon]({alanon_url})


---
## Sententiae Antiquae


---
## Words


---
## Quotes
{quotes_block}
---
{study_heading}
{study_context}---
## Notes


---
## Reflections


---
## Therapy

"""

    out_path.write_text(content, encoding="utf-8")
    print(f"Created: Today.md ({date_display})")
    if study_title:
        print(f"  Study topic: {study_title}")
    _apply_carryover(out_path, INBOX_DIR)
    _inject_todays_jobs(out_path, today)


# ── Parse ──────────────────────────────────────────────────────────────────

def _get_note_date(text: str, fallback: date) -> date:
    """Read date_sort (MM-DD) from frontmatter; return fallback if missing."""
    m = re.search(r'^date_sort:\s*"(\d{2}-\d{2})"', text, re.MULTILINE)
    if m:
        mm, dd = m.group(1).split("-")
        try:
            return date(fallback.year, int(mm), int(dd))
        except ValueError:
            pass
    return fallback


SECTION_MAP = {
    "A Calendar of Wisdom": "tolstoy",
    "Al-Anon":              "alanon",
    "Sententiae Antiquae":  "sententiae",
    "Words":                "words",
}

def _plain_heading(h: str) -> str:
    """Strip markdown link syntax from a heading: [text](url) → text"""
    m = re.match(r'^\[(.+?)\]\(.+?\)$', h.strip())
    return m.group(1) if m else h


def extract_sections(text: str) -> dict[str, str]:
    """Return {heading_text: body} for every ## section in the note body."""
    sections = {}
    pattern = r"^## (.+?)\n(.*?)(?=^## |\Z)"
    for m in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
        heading = m.group(1).strip()
        body    = m.group(2).strip()
        sections[heading] = body
    return sections


def _has_content(body: str) -> bool:
    """Return True if body has meaningful content beyond bare --- dividers."""
    return bool(re.sub(r"^---\s*$", "", body, flags=re.MULTILINE).strip())


def _write_outbox_page(directory: Path, ymd: str, date_full: str, body: str,
                       topic: str = "") -> Path:
    """Write or append a section's content to a dated _outbox page."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ymd}.md"
    title = f"{date_full}{' — ' + topic if topic else ''}"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing.rstrip() + f"\n\n---\n\n{body.strip()}\n", encoding="utf-8")
    else:
        path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
    return path


def _extract_new_quotes(quotes_text: str) -> str:
    """Return only the quote lines marked '(new)', with the marker stripped.

    Mirrors the ## Words convention: mark a genuinely new capture with (new)
    anywhere on its line and it gets archived to _outbox/Quotes. Everything else
    in the section (the morning's resurfaced quotes, the instruction line) is
    left alone — see the call site for why re-archiving them would be harmful.
    A multi-line quote should carry (new) on its first line; following indented
    or blockquote lines are kept with it.
    """
    out: list[str] = []
    capturing = False
    for line in quotes_text.splitlines():
        stripped = line.strip()
        # Skip the injected instruction line — it mentions "(new)" itself and
        # would otherwise archive itself every single day.
        if stripped.startswith("*") and stripped.endswith("*") and "archive" in stripped.lower():
            capturing = False
            continue
        if "(new)" in line.lower():
            cleaned = re.sub(r"\(new\)", "", line, flags=re.IGNORECASE).rstrip()
            if cleaned.strip():
                out.append(cleaned)
            capturing = True
            continue
        if capturing:
            # Keep continuation lines of a multi-line new quote; a blank line or
            # a new top-level line ends it.
            if line.strip() and (line.startswith((" ", "\t", ">"))):
                out.append(line.rstrip())
            else:
                capturing = False
    return "\n".join(out).strip()


def _append_new_words(words_text: str, today: date) -> None:
    """Append words marked '(new)' to _words.md with continuing numbering.

    Mark a word entry as new by including '(new)' anywhere in the line, e.g.:
      **Sop**: A thing given as a concession of little value. (new)
    """
    new_entries = []
    for line in words_text.splitlines():
        if "(new)" in line.lower():
            cleaned = re.sub(r"\(new\)", "", line, flags=re.IGNORECASE).strip()
            # Drop any leading enumeration the source line carried (e.g. "714. "),
            # so we don't double up like "962. 714. word" — _words.md supplies its
            # own running number below. Require a space after the dot/paren so we
            # don't clip a real value like a "1.5x" entry that has no space.
            cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned).strip()
            if cleaned:
                new_entries.append(cleaned)

    if not new_entries:
        return

    WORDS_FILE.touch()
    existing = WORDS_FILE.read_text(encoding="utf-8").rstrip()

    # Find current highest word number
    nums = re.findall(r"^\d+\.", existing, re.MULTILINE)
    next_num = int(nums[-1].rstrip(".")) + 1 if nums else 1

    lines = [existing] if existing else []
    for entry in new_entries:
        lines.append(f"{next_num}. {entry}")
        next_num += 1

    WORDS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Words: appended {len(new_entries)} new word(s) to _words.md")


def parse(today: date):
    today_path = INBOX_DIR / "Today.md"
    stem       = today.strftime("%m-%d")
    dated_path = INBOX_DIR / f"{stem}.md"

    if today_path.exists():
        note_path = today_path
    elif dated_path.exists():
        note_path = dated_path
    else:
        print(f"No daily note found for {today} (Today.md or {stem}.md). Nothing to parse.")
        return

    raw = note_path.read_text(encoding="utf-8")

    # Use date from frontmatter so outbox filenames are always correct
    note_date = _get_note_date(raw, today)
    ymd       = note_date.strftime("%Y%m%d")
    date_full = note_date.strftime("%B %-d, %Y")

    fm_match    = re.match(r"^---\n.*?\n---\n", raw, re.DOTALL)
    frontmatter = raw[:fm_match.end()] if fm_match else ""
    body        = raw[fm_match.end():] if fm_match else raw

    sections = extract_sections(body)

    # ── 1. Journal: Calendar of Wisdom, Al-Anon, Sententiae, Words ────────
    journal_sections = {}
    for heading, key in SECTION_MAP.items():
        match = next((v for k, v in sections.items()
                      if _plain_heading(k) == heading or _plain_heading(k).startswith(heading)), None)
        if match is not None:
            journal_sections[key] = match

    if any(journal_sections.values()):
        month_day = note_date.strftime("%B %-d")
        parts = [f"# {month_day}\n"]
        for heading, key in SECTION_MAP.items():
            body_text = journal_sections.get(key, "").strip().rstrip("---").strip()
            if body_text:
                parts.append(f"## {heading}\n\n{body_text}\n\n---")

        journal_body = "\n".join(parts).rstrip() + "\n"
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        mm_dd = note_date.strftime("%m-%d")
        journal_path = JOURNAL_DIR / f"{mm_dd}.md"
        journal_path.write_text(frontmatter + journal_body, encoding="utf-8")
        print(f"  Journal: {journal_path.name}")

        words_text = journal_sections.get("words", "")
        if words_text and "(new)" in words_text.lower():
            _append_new_words(words_text, note_date)
    else:
        print("  No journal content — skipping.")

    # ── 2. Quotes — archive ONLY newly-captured ones ───────────────────────
    # The ## Quotes section now contains BOTH resurfaced quotes (injected each
    # morning from the lifetime archive for rereading) and anything Sean adds
    # that day. Archiving the whole section would re-archive the resurfaced ones,
    # which then get re-indexed and resurface again — a feedback loop that also
    # corrupts the record of what was actually captured when. So, mirroring the
    # ## Words → _words.md convention, only lines marked "(new)" are routed out.
    quotes_body = sections.get("Quotes", "")
    new_quotes = _extract_new_quotes(quotes_body)
    if new_quotes:
        _write_outbox_page(QUOTES_DIR, ymd, date_full, new_quotes)
        print(f"  Quotes: {ymd}.md ({new_quotes.count(chr(10)) + 1} new line(s))")

    # ── 3. Daily Study ─────────────────────────────────────────────────────
    study_heading = next((k for k in sections if k.startswith("Daily Study")), None)
    study_body    = sections.get(study_heading, "") if study_heading else ""
    if _has_content(study_body):
        topic = study_heading.replace("Daily Study", "").lstrip(" —").strip() if study_heading else ""
        _write_outbox_page(STUDY_DIR, ymd, date_full, study_body, topic=topic)
        print(f"  Daily Study: {ymd}.md")

    # ── 4. Notes ───────────────────────────────────────────────────────────
    notes_body = sections.get("Notes", "")
    if _has_content(notes_body):
        _write_outbox_page(NOTES_DIR, ymd, date_full, notes_body)
        print(f"  Notes: {ymd}.md")

    # ── 5. Reflections (verbatim — secretary role) ─────────────────────────
    reflections_body = sections.get("Reflections", "")
    if _has_content(reflections_body):
        _write_outbox_page(REFLECTIONS_DIR, ymd, date_full, reflections_body)
        print(f"  Reflections: {ymd}.md")

    # ── 6. Therapy ─────────────────────────────────────────────────────────
    therapy_body = sections.get("Therapy", "")
    if _has_content(therapy_body):
        _write_outbox_page(THERAPY_DIR, ymd, date_full, therapy_body)
        print(f"  Therapy: {ymd}.md")

    # ── 7. Brief archive — keep a permanent copy, outside _outbox/wiki flow ─
    brief_body = sections.get("Daily Intelligence Brief", "")
    if _has_content(brief_body):
        _write_outbox_page(BRIEF_ARCHIVE_DIR, ymd, date_full, brief_body)
        print(f"  Brief archive: {ymd}.md")

    # ── 8. Brief carryover — save unchecked items for tomorrow ────────────
    if brief_body:
        # Each email is a "- [ ]" or "- [x]" block; split on those markers
        blocks = re.split(r"(?=^- \[[ x]\] )", brief_body, flags=re.MULTILINE)
        unchecked = [b.strip() for b in blocks if b.strip().startswith("- [ ]")]
        if unchecked:
            carryover_path = INBOX_DIR / "brief_carryover.md"
            date_label = note_date.strftime("%B %-d")
            carryover_path.write_text(
                f"<!-- carryover_date: {date_label} -->\n"
                + "\n\n---\n\n".join(unchecked)
                + "\n",
                encoding="utf-8",
            )
            print(f"  Brief: {len(unchecked)} unread item(s) carried forward to tomorrow.")
        else:
            print("  Brief: all items marked read — nothing carried forward.")

    # ── Remove daily note from inbox ───────────────────────────────────────
    note_path.unlink()
    print(f"  Removed from inbox: {note_path.name}")
    print("\nParse complete.")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true",
                        help="Create today's daily note (run at 6am)")
    parser.add_argument("--parse", action="store_true",
                        help="Route today's note content into the vault (run at 10pm)")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-populate the study heading from calendar (if 6am sync lagged)")
    parser.add_argument("--date", type=str, default=None,
                        help="Override date (YYYY-MM-DD), for testing")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()

    if args.generate:
        generate(today)
    elif args.parse:
        parse(today)
    elif args.refresh:
        refresh_study(today)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
