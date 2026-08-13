"""
resynthesize.py — Rewrite topic pages from current source files.

Unlike synthesize.py (which runs once to bootstrap the vault), this script
is designed to run periodically (Sunday nights via launchd) to keep topic
pages coherent as new source content accumulates.

By default, only topics whose source pages have changed since the last run
are regenerated (mtime-based). Use --all to force a full rewrite.

Usage:
  python3 resynthesize.py                        # changed topics only (default)
  python3 resynthesize.py --all                  # rewrite every topic page
  python3 resynthesize.py --topic "AI and National Security"
  python3 resynthesize.py --topic "AI and National Security" --dry-run
  python3 resynthesize.py --force-keypoints      # re-extract key points too
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Secrets: LOCAL disk first (~/.config/seanipedia/.env), Drive copy as fallback.
# Reading .env off the Google Drive mount is what killed the 2026-08-12 scheduled
# run — Drive returns EDEADLK when the placeholder isn't hydrated, and this read
# happens at import, before any retry logic. resilient_run.sh keeps the local
# copy in sync whenever Drive is healthy. See daily_brief/daily_brief.py.
_LOCAL_ENV = Path.home() / ".config" / "seanipedia" / ".env"
load_dotenv(dotenv_path=_LOCAL_ENV if _LOCAL_ENV.exists() else Path(__file__).parent / ".env",
            override=True)

sys.path.insert(0, str(Path(__file__).parent))
import synthesize as _s

VAULT_PATH   = Path(os.environ.get("VAULT_PATH", "./vault"))
TOPICS_DIR   = VAULT_PATH / "_topics"
CACHE_DIR    = VAULT_PATH / "_cache"
TOPICS_CACHE = CACHE_DIR / "topics.json"
LAST_RUN_FILE = CACHE_DIR / "resynthesize_last_run.json"


def load_topics_from_cache() -> list[dict]:
    if not TOPICS_CACHE.exists():
        print("No topics.json cache found — run synthesize.py first.")
        sys.exit(1)
    return _s.load_json_cache(TOPICS_CACHE)


def get_last_run_time() -> float:
    """Return epoch timestamp of last successful resynthesize run, or 0 if never."""
    if LAST_RUN_FILE.exists():
        try:
            data = json.loads(LAST_RUN_FILE.read_text())
            return float(data.get("timestamp", 0))
        except Exception:
            pass
    return 0.0


def save_last_run_time() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(
        json.dumps({"timestamp": time.time(),
                    "iso": datetime.now(timezone.utc).isoformat()})
    )


def find_changed_source_paths(since: float) -> set[str]:
    """Return wikilink strings for _sources/ files modified after `since`."""
    changed = set()
    sources_dir = VAULT_PATH / "_sources"
    for path in sources_dir.rglob("*.md"):
        if path.stat().st_mtime > since:
            rel = str(path.relative_to(VAULT_PATH).with_suffix("")).replace("\\", "/")
            changed.add(rel)
    return changed


def topic_touches_changed_sources(topic: dict, relevant_pages: list[dict],
                                   changed: set[str]) -> bool:
    """True if any relevant source page for this topic was recently changed."""
    for p in relevant_pages:
        if p.get("wikilink", "") in changed:
            return True
    return False


def resynthesize(topic_filter: str = None, dry_run: bool = False,
                 force_keypoints: bool = False, rewrite_all: bool = False) -> None:

    # 1. Load and filter sources
    all_pages = _s.load_sources()
    if _s.INCLUDE_NOTEBOOKS:
        all_pages = [p for p in all_pages if p["notebook"] in _s.INCLUDE_NOTEBOOKS]

    synthesis_pages  = [p for p in all_pages if p["type"] not in ("todo", "journal")]
    reflection_pages = [p for p in synthesis_pages if p["type"] == "reflection"]
    external_pages   = [p for p in synthesis_pages if p["type"] != "reflection"]

    print(f"Loaded {len(synthesis_pages)} synthesis pages "
          f"({len(external_pages)} external, {len(reflection_pages)} reflections).")

    # 2. Extract key points (hash cache — only re-runs for changed files)
    synthesis_pages  = _s.extract_key_points(synthesis_pages, force=force_keypoints)
    reflection_pages = [p for p in synthesis_pages if p["type"] == "reflection"]
    external_pages   = [p for p in synthesis_pages if p["type"] != "reflection"]

    # 3. Load topics and determine scope
    topics = load_topics_from_cache()

    if topic_filter:
        topics = [t for t in topics if t["name"].lower() == topic_filter.lower()]
        if not topics:
            topics = [t for t in load_topics_from_cache()
                      if topic_filter.lower() in t["name"].lower()]
        if not topics:
            print(f"No topic found matching '{topic_filter}'.")
            sys.exit(1)
        print(f"Targeting {len(topics)} topic(s) matching '{topic_filter}'.")
        changed = None  # skip change filtering when a specific topic is requested

    elif rewrite_all:
        print(f"Resynthesizing all {len(topics)} topic pages (--all).")
        changed = None

    else:
        last_run = get_last_run_time()
        changed = find_changed_source_paths(last_run)
        if last_run == 0:
            print("No previous run recorded — resynthesizing all topics.")
            changed = None
        elif not changed:
            print("No source pages changed since last run — nothing to do.")
            if not dry_run:
                save_last_run_time()
            return
        else:
            since_str = datetime.fromtimestamp(last_run).strftime("%Y-%m-%d %H:%M")
            print(f"{len(changed)} source page(s) changed since {since_str}.")

    # 4. Regenerate affected topic pages
    written = 0
    skipped = 0
    unchanged = 0

    for topic in topics:
        relevant_external    = _s.find_relevant_pages(topic, external_pages)
        relevant_reflections = _s.find_relevant_pages(topic, reflection_pages)
        all_relevant = relevant_external + relevant_reflections

        if len(relevant_external) < 2 and len(relevant_reflections) < 2:
            skipped += 1
            continue

        # Skip if none of this topic's sources changed (change-detection mode only)
        if changed is not None and not topic_touches_changed_sources(
                topic, all_relevant, changed):
            unchanged += 1
            continue

        safe_name = re.sub(r'[\\/:*?"<>|]', "-", topic["name"]).strip()
        out_path  = TOPICS_DIR / f"{safe_name}.md"

        print(f"  {'[dry-run] ' if dry_run else ''}Resynthesizing: '{topic['name']}' "
              f"({len(relevant_external)} sources, {len(relevant_reflections)} reflections)...")

        if not dry_run:
            content = _s.generate_topic_page(topic, all_relevant)
            if not content:
                skipped += 1
                continue
            out_path.write_text(content, encoding="utf-8")
            time.sleep(1)

        written += 1

    if not dry_run:
        save_last_run_time()

    print(f"\nDone. {written} page(s) rewritten"
          + (f", {unchanged} unchanged" if unchanged else "")
          + (f", {skipped} skipped" if skipped else "") + ".")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Rewrite topic pages from current source files"
    )
    parser.add_argument("--topic", metavar="NAME",
                        help="Resynthesize a single topic (partial match ok)")
    parser.add_argument("--all", action="store_true",
                        help="Resynthesize all topic pages regardless of changes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing")
    parser.add_argument("--force-keypoints", action="store_true",
                        help="Re-extract key points even for unchanged source files")
    args = parser.parse_args()

    resynthesize(
        topic_filter=args.topic,
        dry_run=args.dry_run,
        force_keypoints=args.force_keypoints,
        rewrite_all=args.all,
    )
