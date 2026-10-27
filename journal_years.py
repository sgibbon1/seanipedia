"""Year-stacked perennial journal files — the shared format for _journal/MM-DD.md.

WHY THIS EXISTS
---------------
_journal/ is keyed by calendar day (MM-DD), not by full date, because the
practice is tied to *A Calendar of Wisdom* — Tolstoy assigns a fixed reading to
each day of the year, so "October 27" means the same passage every year. That
was the right key, but daily.py used to `write_text()` the file outright, which
worked only for as long as the collection covered less than one year.

The practice lapped on 2026-10-27: the parser-written files covered
2026-04-21 → 2026-10-26, and the OneNote-era import covered
2025-10-27 → 2026-04-20. From that day on, every nightly parse landed on a file
that already held a previous year's entry, and the bare write would have
destroyed it silently — six months of entries, one night at a time.

So a day's file now STACKS years, newest first:

    ---
    date_sort: "10-27"
    date_display: "October 27"
    type: journal
    sections: [tolstoy, alanon, sententiae, words]
    years: [2026, 2025]
    ---
    # October 27

    ## 2026

    ### A Calendar of Wisdom

    ...this year's response...

    ---

    ## 2025

    ### A Calendar of Wisdom

    ...last year's response to the same passage...

Opening the day then shows how the reading landed in each successive year,
which is the whole point of journaling against a perennial calendar.

LOAD-BEARING: the year heading is the only thing separating one year's entry
from another's. `upsert_year_block` replaces a year in place and never touches
its neighbours, and it REFUSES to write a file it cannot parse rather than
overwriting content it doesn't understand (see JournalFormatError). Both the
nightly parse (daily.py) and the one-time migration go through this module so
the two can never disagree about the format.
"""

from __future__ import annotations

import re

# The four perennial sections, in reading order. Keys match daily.py's
# SECTION_MAP values; labels are the canonical headings.
SECTION_LABELS: list[tuple[str, str]] = [
    ("tolstoy",    "A Calendar of Wisdom"),
    ("alanon",     "Al-Anon"),
    ("sententiae", "Sententiae Antiquae"),
    ("words",      "Words"),
]

# The OneNote-era import used slightly different names for two of the four.
# Normalising them means a stacked day shows the SAME ### label in every year,
# which is what makes the year-over-year read legible.
LEGACY_HEADING_ALIASES = {
    "Calendar of Wisdom": "A Calendar of Wisdom",
    "Vocabulary":         "Words",
}

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_YEAR_BLOCK_RE  = re.compile(r"^##\s+(\d{4})\s*$\n(.*?)(?=^##\s+\d{4}\s*$|\Z)",
                             re.MULTILINE | re.DOTALL)


class JournalFormatError(Exception):
    """A journal file holds content that isn't year-stacked.

    Raised instead of writing, so an unrecognised file is left intact for a
    human to look at. This is the guardrail that the old `write_text()` lacked.
    """


def strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


def parse_year_blocks(text: str) -> dict[int, str]:
    """Return {year: block body} for a year-stacked journal file.

    The body keeps its ### section headings; the trailing '---' rule that
    separates one year from the next is stripped.
    """
    blocks: dict[int, str] = {}
    for m in _YEAR_BLOCK_RE.finditer(strip_frontmatter(text)):
        body = re.sub(r"\n-{3,}\s*$", "", m.group(2)).strip()
        blocks[int(m.group(1))] = body
    return blocks


def render_sections(sections: dict[str, str]) -> str:
    """Render one year's body: a ### heading per non-empty perennial section."""
    parts = []
    for key, label in SECTION_LABELS:
        body = sections.get(key, "").strip()
        # Daily-note sections arrive with the '---' rule that divides them in
        # Today.md still attached; drop it, the stacked file supplies its own.
        body = re.sub(r"\n*-{3,}\s*$", "", body).strip()
        if body:
            parts.append(f"### {label}\n\n{body}")
    return "\n\n".join(parts)


def render_file(month_day: str, blocks: dict[int, str]) -> str:
    """Render a whole journal file from {year: body}, newest year first."""
    years = sorted(blocks, reverse=True)
    mm_dd = _month_day_to_sort(month_day)
    fm = ("---\n"
          f'date_sort: "{mm_dd}"\n'
          f'date_display: "{month_day}"\n'
          "type: journal\n"
          "sections: [tolstoy, alanon, sententiae, words]\n"
          f"years: [{', '.join(str(y) for y in years)}]\n"
          "---\n")
    body = f"# {month_day}\n"
    for i, year in enumerate(years):
        # A horizontal rule before every year but the first keeps the boundary
        # visible in Obsidian's reading view, where ## alone is easy to miss.
        rule = "---\n\n" if i else ""
        body += "\n" + rule + f"## {year}\n\n{blocks[year].strip()}\n"
    return fm + body


_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _month_day_to_sort(month_day: str) -> str:
    """"October 27" → "10-27" (the date_sort key the vault already uses)."""
    name, day = month_day.rsplit(" ", 1)
    return f"{_MONTHS.index(name) + 1:02d}-{int(day):02d}"


def upsert_year_block(existing: str | None, year: int, month_day: str,
                      sections: dict[str, str]) -> str:
    """Return the full text of the day's file with `year`'s entry set.

    Replaces that year's block if it's already there (so re-parsing the same day
    is idempotent, which matters because the parse job has backup triggers) and
    leaves every other year untouched.

    Raises JournalFormatError if `existing` has content but no year headings —
    better to fail loudly than to overwrite an entry we can't read.
    """
    blocks: dict[int, str] = {}
    if existing:
        blocks = parse_year_blocks(existing)
        if not blocks:
            stray = strip_frontmatter(existing)
            stray = re.sub(r"^#\s+.+$", "", stray, count=1, flags=re.MULTILINE)
            if stray.strip():
                raise JournalFormatError(
                    "existing journal file is not year-stacked; refusing to "
                    "overwrite it — run migrate_journal_years.py first")
    body = render_sections(sections)
    if body:
        blocks[year] = body
    return render_file(month_day, blocks)
