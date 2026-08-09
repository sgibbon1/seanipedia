#!/usr/bin/env python3
"""clean_quote_sources.py — repair OneNote-port damage in the quote source files.

THE DAMAGE
The OneNote → Markdown port inserted a blank line between every line of a
multi-line quote and dropped the indentation, so a poem like St. Patrick's
Breastplate reads as a dozen unrelated one-line entries:

    1. Christ with me,
                          <- extraneous blank
    Christ before me,
                          <- extraneous blank
    Christ behind me,

THE REPAIR
Rejoin each quote's continuation lines under its numbered item, indented, with
the extraneous blank lines removed. The LINE BREAKS THEMSELVES ARE KEPT — in
poetry they carry meaning; only the blank lines between them were spurious.

    1. Christ with me,
       Christ before me,
       Christ behind me,

Only files that follow Sean's numbered convention are touched (measured per
file); files that don't use it are left completely alone. Always writes a
backup first. Dry-run by default — pass --apply to actually write.

Usage:
    python3 clean_quote_sources.py                 # preview (default)
    python3 clean_quote_sources.py --file Faith.md # preview just one
    python3 clean_quote_sources.py --apply         # write, after backing up
"""
from __future__ import annotations

import argparse
import os
import shutil
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

import quotes_index as qi   # reuse the exact parser the index uses

load_dotenv(override=True)

VAULT = Path(os.environ.get("VAULT_PATH", "./vault"))
SRC = VAULT / "_sources" / "Τά εἰς ἑαυτόν" / "Quotes"
BACKUP = Path.home() / "scripts" / f"quote-source-backup-{date.today().isoformat()}"


def split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end + 4], text[end + 4:]
    return "", text


def rebuild(body: str) -> str:
    """Re-emit the body with each quote as one numbered item, continuations
    indented beneath it and the spurious blank lines gone."""
    # Preserve the leading "# Title" heading if present.
    head_lines, rest = [], body
    for line in body.splitlines():
        if line.strip().startswith("#"):
            head_lines.append(line)
        elif line.strip():
            break
    if head_lines:
        idx = body.find(head_lines[-1]) + len(head_lines[-1])
        rest = body[idx:]

    blocks = qi._blocks_numbered(rest)
    out: list[str] = []
    # PRESERVE THE NESTING. By Subject.md and People.md group quotes under a
    # subject/person heading; an earlier version of this script re-emitted every
    # block flat, which destroyed those groupings and turned headings like
    # "Ideas" and "War" into entries. Number within each indent level instead.
    counters: dict[int, int] = {}
    for indent, text in blocks:
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        counters[indent] = counters.get(indent, 0) + 1
        for deeper in [k for k in counters if k > indent]:
            del counters[deeper]          # restart numbering inside a new parent
        pad = " " * indent
        out.append(f"{pad}{counters[indent]}. {lines[0].strip()}")
        for cont in lines[1:]:
            out.append(f"{pad}   {cont.strip()}")   # continuation, aligned under text
    return ("\n".join(head_lines) + "\n\n" if head_lines else "") + "\n".join(out).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: preview)")
    ap.add_argument("--file", help="limit to one filename")
    args = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"source dir not found: {SRC}")

    files = sorted(SRC.glob("*.md"))
    if args.file:
        files = [f for f in files if f.name == args.file]

    if args.apply:
        BACKUP.mkdir(parents=True, exist_ok=True)

    touched = skipped = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if not qi._uses_numbered_convention(body):
            skipped += 1
            print(f"  SKIP  {f.name}  (doesn't use the numbered convention)")
            continue

        new_body = rebuild(body)
        new_text = (fm + "\n" if fm else "") + new_body
        if new_text == text:
            print(f"  ok    {f.name}  (already clean)")
            continue

        before_lines = len([l for l in body.splitlines() if l.strip()])
        after_lines = len([l for l in new_body.splitlines() if l.strip()])
        blanks_removed = body.count("\n\n") - new_body.count("\n\n")
        touched += 1
        print(f"  FIX   {f.name}  (~{blanks_removed} spurious blank lines removed; "
              f"{before_lines}→{after_lines} content lines)")

        if args.apply:
            shutil.copy(f, BACKUP / f.name)
            f.write_text(new_text, encoding="utf-8")

    print(f"\n{'APPLIED' if args.apply else 'PREVIEW'}: "
          f"{touched} file(s) to change, {skipped} skipped.")
    if args.apply:
        print(f"Backup: {BACKUP}")
    else:
        print("Re-run with --apply to write. Then re-run quotes_index.py.")


if __name__ == "__main__":
    main()
