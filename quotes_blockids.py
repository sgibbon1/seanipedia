#!/usr/bin/env python3
"""quotes_blockids.py — stamp Obsidian block IDs onto quotes in the source pages.

WHY THIS EXISTS
The daily brief surfaces three quotes a day; each should link back to the exact
spot it came from. Obsidian offers only two link targets inside a note: a
HEADING (`[[file#Heading]]`) or a BLOCK (`[[file#^id]]`). The quote pages carry
exactly ONE heading each (`# By Subject`) — subjects like "Ideas" are numbered
list items, not headings — so heading links cannot reach a quote. Block refs are
the only mechanism that can, and they require an `^id` marker in the source.

WHY IT DOESN'T TOUCH THE PARSERS
quotes_index.py's three parsers (numbered / table / paragraph) are delicate — a
past bulk edit to these files destroyed nesting and doubled the fragment count.
Rather than refactor them to emit line numbers, this script locates each quote
INDEPENDENTLY by matching its final line against the file, so indexing behaviour
is bit-for-bit unchanged. Anything it cannot place unambiguously is SKIPPED and
reported, never guessed — an unlinked quote is a small loss, a mislinked one is
a lie about a source.

SAFETY
  - --dry-run is the default; --apply writes.
  - Backs up every file it touches first.
  - Idempotent: a line that already carries a `^id` is left alone, so re-running
    after a OneNote re-import (which rewrites the file and wipes markers) simply
    restores them.
  - Markers are invisible in Obsidian's reading view.

Usage:
    python3 quotes_blockids.py                 # dry run, prints coverage
    python3 quotes_blockids.py --apply
    python3 quotes_blockids.py --file "By Subject.md" --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
# Secrets: LOCAL disk first (~/.config/seanipedia/.env), Drive copy as fallback.
# See daily_brief/daily_brief.py for why.
_LOCAL_ENV = Path.home() / ".config" / "seanipedia" / ".env"
load_dotenv(dotenv_path=_LOCAL_ENV if _LOCAL_ENV.exists() else HERE / ".env",
            override=True)

VAULT = Path(os.environ.get("VAULT_PATH", "./vault"))
INDEX_PATH = HERE / "quotes_index.json"
# id -> vault-relative note path, for quotes_daily.py to link against.
MAP_PATH = HERE / "quotes_blockids.json"
LIFETIME_DIR = VAULT / "_sources" / "Τά εἰς ἑαυτόν" / "Quotes"
ARCHIVE_DIR = VAULT / "archive" / "Quotes"
BACKUP = Path.home() / "scripts" / f"quotes-blockid-backup-{date.today().isoformat()}"

_NUM_RE = re.compile(r"^\s*\d+[.)]\s+")          # same convention as quotes_index
_BLOCKID_RE = re.compile(r"\s\^[A-Za-z0-9-]+\s*$")


def _dir_for(collection: str) -> Path:
    return LIFETIME_DIR if collection == "lifetime" else ARCHIVE_DIR


def _note_path(collection: str, fname: str) -> str:
    """Vault-relative path WITHOUT .md, for an unambiguous wikilink.

    Date-named quote archives collide with journal and study notes of the same
    name, so `[[20260425#^id]]` would resolve arbitrarily. Qualifying the path
    pins it."""
    stem = fname[:-3] if fname.endswith(".md") else fname
    if collection == "lifetime":
        return f"_sources/\u03a4\u03ac \u03b5\u1f30\u03c2 \u1f11\u03b1\u03c5\u03c4\u03cc\u03bd/Quotes/{stem}"
    return f"archive/Quotes/{stem}"


# A quote's text as INDEXED is not what sits on the source line: quotes_index
# splits the attribution off ("… -T.E. Lawrence" -> text + author), the table
# file wraps cells in pipes, and some lines carry ** emphasis. So an exact line
# match finds only ~58% of the corpus. We match by CONTAINMENT instead, guarded
# by length: a 25-character-plus verbatim run is specific enough that a false
# positive is implausible, while short lines still demand exact equality.
_MIN_CONTAINMENT = 25


def _key(s: str) -> str:
    """Comparison key: drop a leading list number, collapse whitespace.

    Deliberately does NOT normalise quote marks or case — the index stores text
    verbatim, so matching on the raw characters is what makes a hit trustworthy.
    """
    # Strip any marker ALREADY on the line, or a stamped line stops matching its
    # own quote on a re-run — which would send the tool hunting for another line
    # and stamp the wrong one. This is what makes the script safely idempotent.
    s = _BLOCKID_RE.sub("", s)
    s = _NUM_RE.sub("", s.strip())
    return re.sub(r"\s+", " ", s).strip()


_ATTRIB_TAIL_RE = re.compile(r"^\s*[-–—]\s*\S")


def _matches(line_key: str, want: str) -> bool:
    """Is `want` (an indexed quote's last line) the content of this source line?"""
    if not want:
        return False
    if len(want) >= _MIN_CONTAINMENT:
        return want in line_key
    # Short quotes ("Ultima Ratio Regum", "All politics is local.") are too brief
    # to trust anywhere in a line, but they're safe when the line STARTS with
    # them and the only thing following is an attribution — which is exactly the
    # shape the indexer split off in the first place.
    if line_key == want:
        return True
    if line_key.startswith(want):
        rest = line_key[len(want):]
        return not rest.strip() or bool(_ATTRIB_TAIL_RE.match(rest))
    return False


def place(lines: list[str], quotes: list[dict]) -> tuple[dict[int, str], list[dict]]:
    """Map file-line-index -> quote id, plus the quotes that couldn't be placed.

    Quotes appear in the index in document order, so we scan forward and never
    reuse a line. That ordering is also what disambiguates a quote whose closing
    line is repeated elsewhere in the page.
    """
    assigned: dict[int, str] = {}
    unplaced: list[dict] = []
    keys = [_key(l) for l in lines]
    # NEVER stamp inside YAML frontmatter. quotes_index parses the body AFTER
    # frontmatter is stripped, so its line numbering and ours disagree there —
    # and a fallback scan once appended a marker to the opening `---`, which
    # corrupts the frontmatter block Obsidian reads for title/type/section.
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
    for i in range(body_start):
        keys[i] = "\x00"          # unmatchable sentinel
    cursor = body_start
    for q in quotes:
        tail = q["text"].splitlines()[-1] if q["text"].splitlines() else ""
        want = _key(tail)
        if not want:
            unplaced.append(q)
            continue
        hit = None
        for i in range(cursor, len(lines)):
            if i in assigned:
                continue
            if _matches(keys[i], want):
                hit = i
                break
        if hit is None:
            # Fall back to a full-file scan: the index de-duplicates across
            # files, so a quote's first occurrence may sit earlier than cursor.
            for i in range(body_start, len(lines)):
                if i not in assigned and _matches(keys[i], want):
                    hit = i
                    break
        if hit is None:
            unplaced.append(q)
            continue
        assigned[hit] = q["id"]
        cursor = hit + 1
    return assigned, unplaced


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the markers")
    ap.add_argument("--file", help="limit to one source file, e.g. 'By Subject.md'")
    args = ap.parse_args()

    if not INDEX_PATH.exists():
        raise SystemExit("quotes_index.json not found — run quotes_index.py first.")
    quotes = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    by_file: dict[tuple[str, str], list[dict]] = {}
    for q in quotes:
        if args.file and q["file"] != args.file:
            continue
        by_file.setdefault((q["collection"], q["file"]), []).append(q)

    tot_placed = tot_unplaced = tot_existing = files_changed = 0
    placed_map: dict[str, str] = {}
    problems: list[str] = []

    for (coll, fname), qs in sorted(by_file.items(), key=lambda kv: kv[0][1]):
        path = _dir_for(coll) / fname
        if not path.exists():
            problems.append(f"{fname}: source file not found ({path})")
            tot_unplaced += len(qs)
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        assigned, unplaced = place(lines, qs)

        new_lines = list(lines)
        changed = 0
        for i, qid in assigned.items():
            if _BLOCKID_RE.search(new_lines[i]):
                tot_existing += 1
                continue                      # idempotent: already stamped
            new_lines[i] = new_lines[i].rstrip() + f" ^{qid}"
            changed += 1

        for _i, _qid in assigned.items():
            placed_map[_qid] = _note_path(coll, fname)
        tot_placed += len(assigned)
        tot_unplaced += len(unplaced)
        status = f"{len(assigned)}/{len(qs)} placed"
        if unplaced:
            status += f", {len(unplaced)} UNPLACED"
        print(f"  {fname:<34} {status}{'  (+%d markers)' % changed if changed else ''}")
        for q in unplaced[:2]:
            problems.append(f"{fname}: unplaced — {q['text'][:70]!r}")

        if changed and args.apply:
            BACKUP.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, BACKUP / fname)
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            files_changed += 1

    # Record what was ACTUALLY stamped, so quotes_daily.py only emits a block
    # link for quotes that really carry a marker — an unplaced quote gets a
    # plain file link rather than a dead `#^id` anchor.
    if args.apply and not args.file:
        MAP_PATH.write_text(json.dumps(placed_map, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"wrote {MAP_PATH.name} ({len(placed_map)} entries)")

    total = tot_placed + tot_unplaced
    pct = (tot_placed / total * 100) if total else 0
    print(f"\nplaced {tot_placed}/{total} ({pct:.1f}%)"
          f" · already stamped {tot_existing} · unplaced {tot_unplaced}")
    if problems:
        print("\nproblems (first 12):")
        for p in problems[:12]:
            print(f"  - {p}")
    if args.apply:
        print(f"\nwrote {files_changed} file(s). Backup: {BACKUP}")
        print("Now re-run: python3 quotes_index.py   (verify the corpus is unchanged)")
    else:
        print("\n[DRY RUN] nothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
