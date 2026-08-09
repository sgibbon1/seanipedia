#!/usr/bin/env python3
"""import_quotes_docx.py — import a OneNote page exported as .docx into the vault.

WHY THIS PATH EXISTS
The Graph API route (fetch_onenote_page.py) is the tidiest, but it depends on a
live token and gets throttled hard after a full export. Exporting the page from
OneNote to Word and dropping it in ~/Downloads is a reliable manual fallback that
needs no auth at all.

WHAT THE .DOCX GIVES US
Word flattens OneNote's outline: every paragraph comes through as "Normal" with
no list numbering. What survives — and what this relies on — is INDENTATION:
  - a paragraph at indent 0 starts a new entry (a quote, or a subject heading)
  - an indented paragraph continues the previous entry (wrapped/verse lines)
Subject headings are then separated from quotes the same way the indexer does it:
short, unquoted, unattributed, and followed by other entries.

Output matches the rest of the collection: YAML frontmatter, "# Title", then a
numbered list with continuation lines indented — i.e. already in the repaired
shape, so clean_quote_sources.py has nothing left to fix.

Usage:
    python3 import_quotes_docx.py --docx ~/Downloads/"By Subject.docx"
    python3 import_quotes_docx.py --docx ~/Downloads/"By Subject.docx" --apply
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
from datetime import date
from pathlib import Path

import docx
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(dotenv_path=HERE / ".env", override=True)

VAULT = Path(os.environ.get("VAULT_PATH", "./vault"))
QUOTES_DIR = VAULT / "_sources" / "Τά εἰς ἑαυτόν" / "Quotes"
BACKUP = Path.home() / "scripts" / f"docx-import-backup-{date.today().isoformat()}"

_QUOTE_MARKS = '"“”«»‘’'
MAX_HEADING_CHARS = 60


def read_blocks(path: Path) -> list[str]:
    """Group the docx's flat paragraphs into entries using indentation."""
    d = docx.Document(str(path))
    blocks: list[list[str]] = []
    for p in d.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        ind = p.paragraph_format.left_indent
        indented = bool(ind and ind.inches > 0.1)
        if indented and blocks:
            blocks[-1].append(text)      # continuation of the previous entry
        else:
            blocks.append([text])
    return ["\n".join(b) for b in blocks]


def is_heading(text: str, nxt: str | None) -> bool:
    """A subject heading ("Ideas", "Music", "War") rather than a quote.

    Conservative on purpose — same rule the indexer uses. Misfiling a heading as
    a quote costs one skim; swallowing a real quote loses it silently.
    """
    if "\n" in text or len(text) > MAX_HEADING_CHARS:
        return False
    if any(q in text for q in _QUOTE_MARKS):
        return False
    if re.search(r"[-–—]\s*[A-ZΑ-Ω]", text):     # has an attribution
        return False
    if text.rstrip().endswith((".", "!", "?", ":", ";", ",")):
        return False
    return nxt is not None                        # headings introduce something


def build_markdown(blocks: list[str], title: str) -> tuple[str, int, int]:
    """Render entries as a numbered list, quotes nested under their heading."""
    out: list[str] = [f"# {title}", ""]
    n_top = n_quote = n_head = 0
    sub = 0
    have_heading = False

    for i, text in enumerate(blocks):
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        lines = text.splitlines()
        if is_heading(text, nxt):
            n_top += 1; n_head += 1; sub = 0; have_heading = True
            out.append(f"{n_top}. {lines[0]}")
            continue
        n_quote += 1
        if have_heading:
            sub += 1
            out.append(f"   {sub}. {lines[0]}")
            for cont in lines[1:]:
                out.append(f"      {cont}")
        else:
            n_top += 1
            out.append(f"{n_top}. {lines[0]}")
            for cont in lines[1:]:
                out.append(f"   {cont}")
    return "\n".join(out) + "\n", n_quote, n_head


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", required=True)
    ap.add_argument("--title", default=None, help="defaults to the .docx filename")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src = Path(os.path.expanduser(args.docx))
    if not src.exists():
        raise SystemExit(f"not found: {src}")
    title = args.title or src.stem

    blocks = read_blocks(src)
    body, n_quote, n_head = build_markdown(blocks, title)

    fm = (f"---\ntitle: \"{title}\"\ntype: reflection\n"
          f"notebook: \"Τά εἰς ἑαυτόν\"\nsection: \"Quotes\"\n"
          f"location: \"Τά εἰς ἑαυτόν > Quotes\"\n"
          f"source: \"imported from OneNote .docx export\"\n"
          f"imported: {date.today().isoformat()}\n---\n\n")
    new_text = fm + body

    target = QUOTES_DIR / f"{title}.md"
    print(f"docx      : {src}")
    print(f"entries   : {len(blocks)}  ->  {n_quote} quotes, {n_head} subject headings")
    print(f"target    : {target}")
    if target.exists():
        old = target.read_text(encoding="utf-8")
        print(f"current   : {len(old.splitlines())} lines")
        print(f"incoming  : {len(new_text.splitlines())} lines")
        for probe in ("Morrison", "Chant of Love", "Gerontion"):
            print(f"   {probe:<14} current={old.count(probe)}  incoming={body.count(probe)}")

    preview = Path("/tmp") / f"{title}_from_docx.md"
    preview.write_text(new_text, encoding="utf-8")
    print(f"preview   : {preview}")

    if not args.apply:
        print("\n[DRY RUN] vault not modified. Re-run with --apply.")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy(target, BACKUP / target.name)
    target.write_text(new_text, encoding="utf-8")
    print(f"\nWrote {target}\nBackup: {BACKUP / target.name}")
    print("Next: python3 quotes_index.py")


if __name__ == "__main__":
    main()
