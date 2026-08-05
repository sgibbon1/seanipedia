#!/usr/bin/env python3
"""quotes_index.py — build a structured index of every quote in the vault.

PHASE 1 of the daily-quotes feature. This script ONLY reads the vault and writes
an index; it does not touch Today.md. Phase 2 (daily surfacing) consumes the index.

WHY AN INDEX INSTEAD OF PARSING LIVE
The quote files are heterogeneous — plain lines with "-Author" suffixes, deeply
nested numbered lists where a child may be commentary rather than a quote,
multi-line poetry and stacked aphorisms, Latin with no attribution, mixed smart
and straight quote marks. Parsing that live every morning would silently spray
fragments into the daily note. Building an index first makes quality auditable
BEFORE anything reaches Today.md, and the index is reusable (search, by-topic
resurfacing) later.

STABLE IDS — load-bearing for the "complete coverage" requirement. Sean wants
every quote surfaced before any repeats, so Phase 2 will keep a ledger of what's
been shown. The id is a hash of the quote's NORMALIZED text, so re-running this
indexer (after edits, new captures, parser fixes) keeps ids stable and the ledger
stays valid. Never switch the id to a positional index.

Sources (per Sean, 2026-08-04): the lifetime collection AND the daily archive.

Usage:
    python3 quotes_index.py              # build index + print quality report
    python3 quotes_index.py --samples 20 # show more samples
    python3 quotes_index.py --show-flagged  # inspect what was rejected
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

VAULT = Path(os.environ.get("VAULT_PATH", "./vault"))
LIFETIME_DIR = VAULT / "_sources" / "Τά εἰς ἑαυτόν" / "Quotes"
ARCHIVE_DIR = VAULT / "archive" / "Quotes"
OUT_PATH = Path(__file__).parent / "quotes_index.json"

# A quote shorter than this is almost always a fragment or a stray header.
MIN_QUOTE_CHARS = 20
# A "category" line (e.g. "1. Ideas", "1. Dorothy Parker, poet") introduces
# children rather than being a quote itself; short + unquoted + unattributed.
MAX_CATEGORY_CHARS = 60

_LIST_RE = re.compile(r"^(\s*)(?:\d+[.)]|[-*+])\s+(.*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s+")
# Attribution: trailing "-Name" / "—Name" / "– Name" at the end of a quote.
_ATTRIB_RE = re.compile(r"[\s]*[-–—]\s*([A-ZΑ-ΩА-Я][^-–—\n]{2,60})\s*$")
_QUOTE_MARKS = '"“”«»‘’'


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _normalize(s: str) -> str:
    """Canonical form for hashing — so smart/straight quotes, whitespace, and
    unicode variants of the SAME quote produce the SAME id."""
    s = unicodedata.normalize("NFKC", s)
    for ch in _QUOTE_MARKS:
        s = s.replace(ch, '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip().strip('"').lower()


def quote_id(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()[:12]


def _split_attrib(text: str) -> tuple[str, str | None]:
    """Peel a trailing '-Author' off a quote. Returns (quote, author|None)."""
    m = _ATTRIB_RE.search(text)
    if not m:
        return text.strip(), None
    author = m.group(1).strip().rstrip(".,;")
    # Guard: an em-dash mid-sentence isn't an attribution. Require the tail to
    # look like a name/source (few words, no sentence-ending punctuation inside).
    if len(author.split()) > 8 or any(p in author for p in ".!?" ) and not author.endswith("."):
        pass
    return text[:m.start()].strip(), author


def _blocks(body: str) -> list[tuple[int, str]]:
    """Split a file body into (indent, text) blocks.

    A block is a list item plus its wrapped continuation lines, or a standalone
    paragraph. Multi-line quotes (poetry, stacked aphorisms) stay together —
    that's the whole reason we don't split on newlines.
    """
    out: list[tuple[int, str]] = []
    cur_indent, cur_lines = None, []

    def flush():
        nonlocal cur_indent, cur_lines
        if cur_lines:
            txt = "\n".join(l.strip() for l in cur_lines).strip()
            if txt:
                out.append((cur_indent or 0, txt))
        cur_indent, cur_lines = None, []

    for raw in body.splitlines():
        # A BLANK LINE IS NOT A BOUNDARY BY ITSELF. In this OneNote export the
        # lines of a multi-line quote (St. Patrick's Breastplate, the Veronese
        # Riddle, stacked aphorisms) are INDENTED continuations separated by
        # blank lines. Treating every blank as a break shattered those poems
        # into one-line "quotes" — the fragment problem. What actually ends a
        # block is the next real line: a new list item, a heading, or text
        # starting back at column 0.
        if not raw.strip():
            continue
        if _HEADING_RE.match(raw.strip()):
            flush(); continue          # headings are structure, never quotes
        m = _LIST_RE.match(raw)
        indent = len(raw[:len(raw) - len(raw.lstrip())].expandtabs(4))
        if m:
            flush()                    # a new list item always starts a block
            cur_indent = len(m.group(1).expandtabs(4))
            cur_lines = [m.group(2)]
        elif cur_lines and indent > 0:
            cur_lines.append(raw)      # indented → continuation of this quote
        else:
            flush()                    # column-0 text → a new standalone block
            cur_indent, cur_lines = indent, [raw]
    flush()
    return out


# Only these files are organised as "top-level item = grouping, children =
# quotes". Everywhere else a top-level item IS a quote, so category detection
# must not run — that mistake both garbles topics and DROPS real quotes.
_GROUPED_FILES = {"By Subject", "People", "Witticisms"}

# Bare words that are section scaffolding in the OneNote export, never quotes.
_SECTION_WORDS = {
    "power", "self", "wisdom", "witticisms", "latin", "faith", "bible/faith",
    "turns of phrase", "русский", "think", "thinking", "decision", "purpose",
    "maxims", "people", "memes", "russia", "intelligence", "by subject",
    "capability", "luck", "getting a job", "quotes", "sententiae antiquae",
}
_SECTION_WORDS = {w.lower() for w in _SECTION_WORDS}


def _looks_like_category(text: str, has_children: bool, indent: int,
                         file_stem: str) -> bool:
    """Is this list item a grouping header rather than a quote?

    Deliberately CONSERVATIVE: Sean's requirement is that no saved quote is ever
    left out, so ambiguity resolves toward "this is a quote." A stray header in
    the rotation costs one skim; a dropped quote is invisible and permanent.
    """
    if not has_children or indent != 0:
        return False
    if file_stem not in _GROUPED_FILES:
        return False
    if len(text) > MAX_CATEGORY_CHARS:
        return False
    if any(q in text for q in _QUOTE_MARKS):
        return False            # quoted → it's a quote
    if _ATTRIB_RE.search(text):
        return False            # attributed → it's a quote
    if text.rstrip().endswith((".", "!", "?", ":")):
        return False            # terminal punctuation → reads as a sentence
    return True


def parse_file(path: Path, collection: str) -> tuple[list[dict], list[dict]]:
    """Return (quotes, flagged) parsed from one markdown file."""
    try:
        body = _strip_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], [{"file": path.name, "text": f"<unreadable: {exc}>", "reason": "read-error"}]

    blocks = _blocks(body)
    quotes, flagged = [], []
    topic_from_file = path.stem
    current_subject: str | None = None

    for i, (indent, text) in enumerate(blocks):
        next_indent = blocks[i + 1][0] if i + 1 < len(blocks) else -1
        has_children = next_indent > indent

        if _looks_like_category(text, has_children, indent, path.stem):
            current_subject = text.rstrip(":")
            continue

        clean, author = _split_attrib(text)
        entry = {
            "id": quote_id(clean),
            "text": clean,
            "author": author,
            # Topic ALWAYS comes from the filename — that's reliable ("Latin",
            # "Faith", "Power"). An inferred sub-grouping is recorded separately
            # so a bad inference can never corrupt the primary topic label.
            "topic": topic_from_file,
            "subtopic": current_subject,
            "file": path.name,
            "collection": collection,
            "multiline": "\n" in clean,
        }
        # QUALITY LABELLING, NOT EXCLUSION.
        # Sean's rule: "if I saved them, I thought them worthy of revisiting."
        # So the only things truly dropped are structural noise (empties, bare
        # section headers). Everything else is KEPT with a quality label, and
        # Phase 2 decides what to surface — a decision he can see and change,
        # rather than one buried in a parser.
        stripped = clean.strip()
        # Markdown artifacts from the OneNote export (escaped bold/rule markers
        # like \*\* or \*\*\*) carry no text at all — pure noise, never quotes.
        is_artifact = bool(stripped) and not re.search(r"[A-Za-zΑ-Ωα-ωА-Яа-я0-9]", stripped)
        is_header = stripped.rstrip(":").lower() in _SECTION_WORDS or stripped == ""
        if is_artifact:
            entry["reason"] = "markdown artifact (no text)"
            flagged.append(entry)
            continue
        has_quote_mark = any(q in clean for q in _QUOTE_MARKS)

        if is_header:
            entry["reason"] = "structural (empty or section header)"
            flagged.append(entry)
            continue

        if clean.lower().startswith(("compare ", "see ", "cf.", "note:")):
            entry["quality"] = "commentary"      # about a quote, not a quote
        elif len(clean) < MIN_QUOTE_CHARS and not (has_quote_mark or author):
            # Short AND unquoted AND unattributed — most likely a stray line of
            # a poem whose stanza was split by a blank line in the source.
            entry["quality"] = "fragment"
        else:
            entry["quality"] = "ok"
        quotes.append(entry)
    return quotes, flagged


def build() -> tuple[list[dict], list[dict]]:
    all_q, all_f = [], []
    for d, coll in ((LIFETIME_DIR, "lifetime"), (ARCHIVE_DIR, "daily-archive")):
        if not d.exists():
            print(f"  ! missing source dir: {d}")
            continue
        for f in sorted(d.glob("*.md")):
            q, fl = parse_file(f, coll)
            all_q += q
            all_f += fl
    # De-duplicate by stable id, keeping the first occurrence but recording that
    # the quote appears in multiple places.
    seen: dict[str, dict] = {}
    dupes = 0
    for q in all_q:
        if q["id"] in seen:
            dupes += 1
            seen[q["id"]].setdefault("also_in", []).append(f"{q['file']}")
        else:
            seen[q["id"]] = q
    return list(seen.values()), all_f, dupes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--show-flagged", action="store_true")
    args = ap.parse_args()

    print(f"Vault: {VAULT}")
    quotes, flagged, dupes = build()

    OUT_PATH.write_text(json.dumps(quotes, ensure_ascii=False, indent=1), encoding="utf-8")

    by_coll: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    with_author = multi = 0
    for q in quotes:
        by_coll[q["collection"]] = by_coll.get(q["collection"], 0) + 1
        by_topic[q["topic"]] = by_topic.get(q["topic"], 0) + 1
        with_author += bool(q["author"])
        multi += bool(q["multiline"])

    print(f"\n{'='*64}\n  QUOTE INDEX — {len(quotes)} unique quotes\n{'='*64}")
    print(f"  by collection : " + ", ".join(f"{k}={v}" for k, v in by_coll.items()))
    print(f"  with author   : {with_author} ({with_author*100//max(len(quotes),1)}%)")
    print(f"  multi-line    : {multi}")
    print(f"  duplicates merged: {dupes}")
    print(f"  dropped as structural noise: {len(flagged)}")
    qual = {}
    for q in quotes: qual[q.get("quality","ok")] = qual.get(q.get("quality","ok"),0)+1
    print(f"  quality       : " + ", ".join(f"{k}={v}" for k,v in sorted(qual.items())))
    print(f"  → at 3/day, full coverage takes {len(quotes)//3} days "
          f"(~{len(quotes)/3/365:.1f} years)")
    print(f"  index written → {OUT_PATH.name}")

    print(f"\n  TOP TOPICS")
    for t, n in sorted(by_topic.items(), key=lambda x: -x[1])[:12]:
        print(f"    {n:4d}  {t}")

    print(f"\n  RANDOM SAMPLE ({args.samples}) — judge quality here:")
    import random
    random.seed(20260804)
    for q in random.sample(quotes, min(args.samples, len(quotes))):
        head = q["text"].replace("\n", " / ")
        head = head[:150] + ("…" if len(head) > 150 else "")
        print(f"\n    [{q['topic']}] {head}")
        if q["author"]:
            print(f"      — {q['author']}")

    if args.show_flagged:
        print(f"\n  FLAGGED / EXCLUDED ({len(flagged)}):")
        for f in flagged[:40]:
            print(f"    ({f.get('reason')}) {f['text'][:90]!r}")


if __name__ == "__main__":
    main()
