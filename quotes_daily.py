#!/usr/bin/env python3
"""quotes_daily.py — pick the day's resurfaced quotes from the vault index.

PHASE 2 of the daily-quotes feature. Consumes quotes_index.json (built by
quotes_index.py) and deals a few quotes per day into the daily note's ## Quotes
section, so years of saved quotes actually get revisited.

COMPLETE COVERAGE (Sean's requirement: "if I saved them, I thought them worthy
of revisiting"). This is a SHUFFLED DECK, not random sampling:
  - Every quote is dealt exactly once before any repeats.
  - The ledger records ids already shown; ids are content hashes, so editing the
    index, fixing the parser, or adding new captures never loses that history.
  - New quotes added later join the remaining undealt pool automatically.
  - Only when the deck is exhausted does it reshuffle for a fresh cycle.

Usage:
    python3 quotes_daily.py            # print today's quotes as markdown
    python3 quotes_daily.py --count 5  # deal a different number
    python3 quotes_daily.py --peek     # show without recording (no ledger write)
    python3 quotes_daily.py --status   # coverage progress
"""
from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
INDEX_PATH = HERE / "quotes_index.json"
LEDGER_PATH = HERE / "quotes_ledger.json"
DEFAULT_COUNT = 3


def load_index() -> list[dict]:
    if not INDEX_PATH.exists():
        raise SystemExit(f"{INDEX_PATH.name} not found — run quotes_index.py first.")
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def load_ledger() -> dict:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"shown": [], "cycle": 1, "last_date": None}


def save_ledger(ledger: dict) -> None:
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=1),
                           encoding="utf-8")


def pick(quotes: list[dict], ledger: dict, count: int,
         for_date: str | None = None) -> tuple[list[dict], dict]:
    """Deal `count` quotes not yet shown this cycle; reshuffle when exhausted.

    IDEMPOTENT PER DAY: daily.py --generate can legitimately run more than once
    (a re-run, or the catch-up loop after a missed day). Without this guard each
    invocation would deal a fresh set and chew through the deck. If we already
    dealt today, return exactly those same quotes and touch nothing.
    """
    today = for_date or date.today().isoformat()
    if ledger.get("last_date") == today and ledger.get("last_picked"):
        by_id = {q["id"]: q for q in quotes}
        same = [by_id[i] for i in ledger["last_picked"] if i in by_id]
        if same:
            return same, ledger

    shown = set(ledger.get("shown", []))
    pool = [q for q in quotes if q["id"] not in shown]

    if not pool:                      # cycle complete — every quote has been seen
        ledger = {"shown": [], "cycle": ledger.get("cycle", 1) + 1,
                  "last_date": ledger.get("last_date")}
        pool = list(quotes)

    # Seed by date so a same-day re-run is idempotent (re-running --generate
    # must not silently burn through the deck).
    rng = random.Random(f"{date.today().isoformat()}-{ledger.get('cycle',1)}")
    rng.shuffle(pool)
    picked = pool[:count]

    ledger["shown"] = list(shown) + [q["id"] for q in picked]
    ledger["last_date"] = today
    ledger["last_picked"] = [q["id"] for q in picked]
    return picked, ledger


BLOCKID_MAP_PATH = Path(__file__).parent / "quotes_blockids.json"


def load_blockids() -> dict[str, str]:
    """id -> vault-relative note path, written by quotes_blockids.py.

    Absent or incomplete is fine: a quote without a marker falls back to a
    topic-only label, so the Quotes section never shows a dead `#^id` anchor.
    """
    try:
        return json.loads(BLOCKID_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def format_quote(q: dict, blockids: dict[str, str] | None = None) -> str:
    """One quote as markdown. Multi-line quotes keep their line breaks (poetry
    and stacked aphorisms are why the indexer preserves them).

    The topic becomes a BLOCK LINK back to the exact line in the source page
    when we have a marker for it — Obsidian jumps to and highlights the quote,
    rather than dumping you at the top of a 600-line page.
    """
    blockids = blockids if blockids is not None else {}
    body = "\n".join(f"> {line}" if line.strip() else ">"
                     for line in q["text"].splitlines())
    tail = []
    if q.get("author"):
        tail.append(f"— {q['author']}")
    note = blockids.get(q.get("id", ""))
    label = q.get("subtopic") or q.get("topic")
    # Daily-archive quotes are topic'd by their filename (20260425); show a
    # readable date instead of an eight-digit number.
    if label and re.fullmatch(r"\d{8}", label):
        try:
            label = datetime.strptime(label, "%Y%m%d").strftime("%b %-d, %Y")
        except ValueError:
            pass
    if note and label:
        # Alias keeps the display text short — the path is long and Greek.
        tail.append(f"[[{note}#^{q['id']}|{label}]]")
    elif label:
        tail.append(f"*{label}*")
    if tail:
        body += f"\n> {'  ·  '.join(tail)}"
    return body


def render(picked: list[dict]) -> str:
    blockids = load_blockids()
    return "\n\n".join(format_quote(q, blockids) for q in picked)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
    ap.add_argument("--peek", action="store_true", help="don't record in the ledger")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    quotes = load_index()
    ledger = load_ledger()

    if args.status:
        shown = len(set(ledger.get("shown", [])))
        total = len(quotes)
        left = total - shown
        print(f"cycle {ledger.get('cycle',1)} · shown {shown}/{total} · "
              f"{left} remaining (~{left // max(args.count,1)} days at {args.count}/day)")
        print(f"last dealt: {ledger.get('last_date') or 'never'}")
        return

    picked, ledger = pick(quotes, ledger, args.count)
    if not args.peek:
        save_ledger(ledger)
    print(render(picked))


if __name__ == "__main__":
    main()
