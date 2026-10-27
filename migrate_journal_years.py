"""One-time migration: nest every flat _journal/MM-DD.md under a ## <year> heading.

Run once, on 2026-10-27, the day the perennial journal lapped. See
journal_years.py for why the format changed.

Each existing file holds exactly one year's entry with its sections at ##.
This rewrites it as a single ## <year> block whose sections sit at ###, which is
the shape upsert_year_block() appends to from then on.

Which year a file belongs to:
  * parser-written files state it outright in frontmatter (date_full).
  * OneNote-era files don't, so it's inferred from the practice's own span —
    it ran 2025-10-27 → 2026-04-20 without a break longer than the holidays,
    and there is no file for 04-21 → 10-26 in that set. So Oct–Dec is 2025 and
    Jan–Apr is 2026. Nothing in the files themselves contradicts this (they
    carry no internal dates at all).

Usage:
  python3 migrate_journal_years.py             # dry run — prints a plan, writes nothing
  python3 migrate_journal_years.py --apply     # rewrite the files
  python3 migrate_journal_years.py --apply --only 10-27
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

import journal_years as jy

# Local-first .env (the Drive copy is a fallback — see the other scripts).
_LOCAL_ENV = Path.home() / ".config" / "seanipedia" / ".env"
load_dotenv(_LOCAL_ENV if _LOCAL_ENV.exists() else Path(__file__).parent / ".env")

VAULT_PATH  = Path(os.environ.get("VAULT_PATH", "./vault"))
JOURNAL_DIR = VAULT_PATH / "_journal"

# The import's own span — Oct 27 through Dec 31 is the first calendar year of
# the practice, Jan 1 through Apr 20 the second.
LEGACY_FIRST_YEAR  = 2025
LEGACY_SECOND_YEAR = 2026


def infer_year(path: Path, text: str) -> int:
    """The year a flat journal file belongs to."""
    m = re.search(r'date_full:\s*"?[A-Za-z]+ \d{1,2},\s*(\d{4})', text)
    if m:
        return int(m.group(1))
    month = int(path.stem.split("-")[0])
    return LEGACY_FIRST_YEAR if month >= 10 else LEGACY_SECOND_YEAR


def split_flat_sections(body: str) -> list[tuple[str, str]]:
    """[(canonical heading, content)] from a flat (##-headed) journal body."""
    out = []
    for m in re.finditer(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s+|\Z)", body,
                         re.MULTILINE | re.DOTALL):
        heading = m.group(1).strip()
        # A few days carry the daily note's linked heading form,
        # "## [A Calendar of Wisdom](https://…)" — take the link text.
        heading = re.sub(r"^\[(.+?)\]\(.+?\)$", r"\1", heading)
        heading = jy.LEGACY_HEADING_ALIASES.get(heading, heading)
        content = re.sub(r"\n-{3,}\s*$", "", m.group(2)).strip()
        if content:
            out.append((heading, content))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    ap.add_argument("--only", help="migrate a single day, e.g. 10-27")
    args = ap.parse_args()

    if not JOURNAL_DIR.exists():
        print(f"No journal directory at {JOURNAL_DIR}", file=sys.stderr)
        return 1

    label_to_key = {label: key for key, label in jy.SECTION_LABELS}
    paths = sorted(JOURNAL_DIR.glob("*.md"))
    if args.only:
        paths = [p for p in paths if p.stem == args.only]

    stats = {"already": 0, "migrated": 0, "empty": 0, "unknown": 0}
    unknown_headings: dict[str, int] = {}

    for path in paths:
        text = path.read_text(encoding="utf-8")

        if jy.parse_year_blocks(text):
            stats["already"] += 1
            continue

        body = jy.strip_frontmatter(text)
        title = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        month_day = title.group(1).strip() if title else path.stem

        flat = split_flat_sections(body)
        sections: dict[str, str] = {}
        for heading, content in flat:
            key = label_to_key.get(heading)
            if key is None:
                # An unrecognised heading would be dropped by render_sections,
                # so stop rather than quietly lose it.
                unknown_headings[heading] = unknown_headings.get(heading, 0) + 1
                stats["unknown"] += 1
                sections = {}
                break
            # Same heading twice in one file: two days in April were parsed
            # twice in the early days, so the sections repeat verbatim. Keep one
            # copy of an identical repeat, and both if they actually differ —
            # never drop text Sean wrote.
            if key not in sections:
                sections[key] = content
            elif content.strip() not in sections[key]:
                sections[key] += "\n\n" + content
        else:
            year = infer_year(path, text)
            if not sections:
                # Shell file — the day's note was parsed with nothing written in
                # it. Harmless, and rewriting it costs nothing, so stack it too
                # (an empty year block simply doesn't render).
                stats["empty"] += 1
            new_text = jy.upsert_year_block(None, year, month_day, sections)
            if args.apply:
                path.write_text(new_text, encoding="utf-8")
            stats["migrated"] += 1
            kept = ", ".join(label for key, label in jy.SECTION_LABELS if key in sections)
            print(f"  {path.name}: → ## {year}" + (f"  [{kept}]" if kept else "  [empty]"))
            continue

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: "
          f"{stats['migrated']} to migrate ({stats['empty']} of them empty), "
          f"{stats['already']} already stacked, {stats['unknown']} skipped.")
    if unknown_headings:
        print("Unrecognised headings (files skipped, nothing written):")
        for heading, n in sorted(unknown_headings.items(), key=lambda kv: -kv[1]):
            print(f"  {n:3d}  {heading}")
    if not args.apply and stats["migrated"]:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
