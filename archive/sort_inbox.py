"""
sort_inbox.py — Route inbox notes to the most relevant _sources/ pages.

Workflow:
  1. Read .md / .txt files from <vault>/_inbox/
  2. Build a lightweight index of all existing _sources/ pages
  3. Ask Claude to propose the top 3 most relevant destination source pages,
     with a one-line reason for each and optional split suggestions
  4. Present proposals interactively — user picks one, enters a custom path,
     requests a split, or skips
  5. Append the note (or a portion) to the chosen source page with a dated
     attribution separator
  6. Archive the processed inbox file to _sources/Inbox/

Usage:
  python sort_inbox.py             # interactive routing for all inbox files
  python sort_inbox.py --dry-run   # preview proposals without writing
  python sort_inbox.py --file foo.md  # process a single file by name
"""

import argparse
import json
import os
import re
import shutil
import textwrap
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
INBOX_DIR = VAULT_PATH / "_inbox"
SOURCES_DIR = VAULT_PATH / "_sources"
ARCHIVE_DIR = SOURCES_DIR / "Inbox"

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-opus-4-7"

SEPARATOR = "\n\n\n---\n\n"
MAX_SNIPPET_CHARS = 200
MAX_SOURCE_PAGES = 300   # cap to keep prompt manageable


# ── Index ──────────────────────────────────────────────────────────────────


# Folders excluded from routing destinations (ad-hoc targets, not general receptacles)
EXCLUDED_SOURCE_DIRS = {
    "Inbox",
    "Mac Notes",
    "Email Notes",
    "Daily Study",
}


def build_source_index() -> list[dict]:
    """
    Walk _sources/ and collect {path, rel_path, title, snippet} for each .md file.
    Excludes the Inbox archive subfolder and other ad-hoc-only folders.
    """
    index = []
    for path in sorted(SOURCES_DIR.rglob("*.md")):
        # Skip the archive folder and other excluded top-level dirs
        rel = path.relative_to(SOURCES_DIR)
        top_level = rel.parts[0]
        if top_level in EXCLUDED_SOURCE_DIRS:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")

        # Extract title from frontmatter, fall back to filename stem
        title_match = re.search(r'^title:\s*"?([^"\n]+)"?', text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem

        # Strip frontmatter for the snippet
        body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL).strip()
        # Remove markdown noise (backslash-only lines from OneNote artifacts)
        body = re.sub(r"^\\+\s*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"\n{3,}", "\n\n", body)
        snippet = body[:MAX_SNIPPET_CHARS].replace("\n", " ").strip()

        index.append({"path": path, "rel": str(rel), "title": title, "snippet": snippet})

    return index[:MAX_SOURCE_PAGES]


def format_index_for_prompt(index: list[dict]) -> str:
    lines = []
    for i, entry in enumerate(index):
        lines.append(f"{i+1}. [{entry['rel']}] {entry['title']}")
        if entry["snippet"]:
            lines.append(f"   {entry['snippet'][:120]}…")
    return "\n".join(lines)


# ── Routing ────────────────────────────────────────────────────────────────


ROUTING_SYSTEM = """\
You are a personal knowledge-base librarian. The user keeps a vault of source notes
organised into folders like "Sean's Notebook/AI/", "Technical Notebook/", "Email Notes/", etc.

Split the note into logical chunks — each chunk is a self-contained piece of content
that belongs together. For short single-topic notes, return one chunk. For notes with
multiple distinct topics, return multiple chunks.

For each chunk, propose the single best destination from the source index.

Rules:
1. Each chunk's "text" field must be copied exactly from the note (verbatim).
2. The "proposed_dest" must be a rel_path from the source index, copied exactly.
3. Write one sentence in "reason" explaining why this chunk fits that destination.
4. Return ONLY valid JSON — no prose outside the JSON block.

Schema:
{
  "chunks": [
    {
      "id": 1,
      "title": "short description of this chunk",
      "text": "...exact verbatim text from the note...",
      "proposed_dest": "Sean's Notebook/AI/1. AI Fundamentals.md",
      "reason": "one sentence"
    }
  ]
}
"""


def propose_chunks(note_text: str, note_filename: str, index: list[dict],
                   excluded_dests: set = None) -> list[dict]:
    """Ask Claude to split the note into chunks and propose a destination for each."""
    filtered_index = index
    if excluded_dests:
        filtered_index = [e for e in index if e["rel"] not in excluded_dests]

    index_str = format_index_for_prompt(filtered_index)
    user_prompt = (
        f"New inbox note: {note_filename}\n\n"
        f"Content:\n{note_text}\n\n"
        f"Source page index:\n{index_str}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=ROUTING_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw).get("chunks", [])
    except Exception:
        return []


# ── Display & interaction ──────────────────────────────────────────────────


def display_chunk(chunk: dict, idx: int, total: int) -> None:
    title = chunk.get("title", f"Chunk {chunk.get('id', idx+1)}")
    dest  = chunk.get("proposed_dest", "?")
    reason = chunk.get("reason", "")
    preview = chunk.get("text", "")[:120].replace("\n", " ")
    print(f"\n  ── Chunk {idx+1}/{total}: {title}")
    print(f"     \"{preview}{'…' if len(chunk.get('text','')) > 120 else ''}\"")
    print(f"\n  → {dest}")
    print(textwrap.fill(reason, width=70, initial_indent="     ", subsequent_indent="     "))


def prompt_chunk_choice(chunk: dict, index: list[dict],
                        rejected_dests: set):
    """
    Returns a confirmed rel_path to append to, or None to skip.
    Handles re-route by re-asking Claude with rejected destinations excluded.
    """
    while True:
        answer = input("  Accept? [Y/n/r=re-route/s=skip/c=custom path] ").strip()

        if answer in ("", "y", "yes"):
            return chunk.get("proposed_dest")

        if answer in ("s", "skip"):
            return None

        if answer == "n":
            sub = input("  [r]e-route or [s]kip? ").strip().lower()
            if sub != "r":
                return None
            answer = "r"

        if answer == "r":
            dest = chunk.get("proposed_dest", "")
            rejected_dests.add(dest)
            print(f"  Re-routing (excluding {len(rejected_dests)} destination(s))…")
            new_chunks = propose_chunks(
                chunk["text"], "", index, excluded_dests=rejected_dests
            )
            if not new_chunks:
                print("  No alternate destination found — skipping.")
                return None
            new_chunk = new_chunks[0]
            new_chunk["text"] = chunk["text"]  # preserve original text
            chunk.update(new_chunk)
            display_chunk(chunk, 0, 1)
            continue

        if answer == "c":
            print("  Enter path relative to _sources/, e.g.:")
            print("    Sean's Notebook/AI/1. AI Fundamentals.md")
            answer = input("  Path: ").strip()
            if not answer:
                continue
        return answer


# ── Write ──────────────────────────────────────────────────────────────────


def append_to_source(dest_rel: str, content: str, attribution: str, dry_run: bool,
                     prepend: bool = False) -> Path:
    dest_path = SOURCES_DIR / dest_rel
    addition = SEPARATOR + f"*{attribution} — via _inbox*\n\n" + content.strip() + "\n"

    if dry_run:
        action = "prepend to" if prepend else "append to"
        print(f"\n  [DRY RUN] Would {action}: {dest_rel}")
        print(textwrap.indent(addition[:300], "    "))
        if len(addition) > 300:
            print("    …(truncated)")
    else:
        if not dest_path.exists():
            print(f"  [warn] Destination not found, creating: {dest_rel}")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(f"# {dest_path.stem}\n", encoding="utf-8")
        existing = dest_path.read_text(encoding="utf-8")
        if prepend:
            # Insert after the first heading line (if any), otherwise at top
            lines = existing.split("\n", 1)
            if lines[0].startswith("#"):
                new_text = lines[0] + "\n" + addition + ("\n" + lines[1] if len(lines) > 1 else "")
            else:
                new_text = addition + existing
            dest_path.write_text(new_text, encoding="utf-8")
            print(f"  Prepended to: {dest_rel}")
        else:
            dest_path.write_text(existing.rstrip() + addition, encoding="utf-8")
            print(f"  Appended to: {dest_rel}")

    return dest_path


def process_chunks(chunks: list[dict], index: list[dict],
                   attribution: str, dry_run: bool) -> list[str]:
    """Walk through chunks interactively. Returns list of dest_rels that were written."""
    written_dests = []
    rejected_dests = set()

    for i, chunk in enumerate(chunks):
        if not chunk.get("text", "").strip():
            print(f"  Skipped chunk {i+1} (empty).")
            continue
        display_chunk(chunk, i, len(chunks))
        dest = prompt_chunk_choice(chunk, index, rejected_dests)
        if dest is None:
            print(f"  Skipped chunk {i+1}.")
            continue
        append_to_source(dest, chunk["text"], attribution, dry_run)
        written_dests.append(dest)

    return written_dests


def record_source_dest(note_path: Path, dest_rel: str) -> None:
    """Write source_dest into the inbox note's frontmatter so sync.py can cite it."""
    text = note_path.read_text(encoding="utf-8")
    safe = dest_rel.replace('"', "'")
    if text.startswith("---\n"):
        close = text.index("\n---\n", 4)
        text = text[:close] + f'\nsource_dest: "{safe}"' + text[close:]
    else:
        text = f'---\nsource_dest: "{safe}"\n---\n\n' + text
    note_path.write_text(text, encoding="utf-8")


def archive_note(note_path: Path, dry_run: bool) -> None:
    dest = ARCHIVE_DIR / note_path.name
    if dest.exists():
        dest = ARCHIVE_DIR / f"{note_path.stem}_{date.today().isoformat()}{note_path.suffix}"
    if dry_run:
        print(f"  [DRY RUN] Would archive: {note_path.name} → _sources/Inbox/")
    else:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(note_path), str(dest))
        print(f"  Archived: {dest.relative_to(VAULT_PATH)}")


# ── Content detection ─────────────────────────────────────────────────────


def has_user_content(note_text: str) -> bool:
    """Return True if the note has user-added content beyond frontmatter and pre-populated calendar context."""
    body = re.sub(r"^---\n.*?\n---\n?", "", note_text.strip(), count=1, flags=re.DOTALL).strip()
    if not body:
        return False
    body = re.sub(r"^-{3,}\s*$", "", body, flags=re.MULTILINE).strip()
    if not body:
        return False
    # Day-header pattern marks start of pre-populated calendar description
    calendar_header = re.search(
        r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d+",
        body, re.MULTILINE
    )
    if calendar_header:
        before = body[:calendar_header.start()].strip()
        return len(before) > 30
    return len(body) > 30


# ── Main ───────────────────────────────────────────────────────────────────


def process_file(note_path: Path, index: list[dict], dry_run: bool,
                 no_archive: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"Note: {note_path.name}")
    print(f"{'='*60}")

    note_text = note_path.read_text(encoding="utf-8", errors="replace").strip()
    if not note_text:
        print("  [skip] Empty file.")
        return
    if not has_user_content(note_text):
        print("  [skip] No user-added content — only pre-populated calendar context.")
        archive_note(note_path, dry_run)
        return

    # Check for forced destination in frontmatter — skip Claude + interactive routing
    forced_match = re.search(r'^forced_dest:\s*"?([^"\n]+)"?', note_text, re.MULTILINE)
    if forced_match:
        dest = forced_match.group(1).strip()
        reverse_chron = bool(re.search(r'^reverse_chron:\s*true', note_text, re.MULTILINE))
        today = date.today().isoformat()
        body = re.sub(r"^---\n.*?\n---\n?", "", note_text, count=1, flags=re.DOTALL).strip()
        print(f"  Auto-routing to: {dest}" + (" (prepend)" if reverse_chron else ""))
        append_to_source(dest, body, f"Sean, {today}", dry_run, prepend=reverse_chron)
        if not dry_run:
            record_source_dest(note_path, dest)
        archive_note(note_path, dry_run)
        return

    print("  Asking Claude to split into chunks and propose destinations…")
    chunks = propose_chunks(note_text, note_path.name, index)

    if not chunks:
        print("  [warn] Claude returned no chunks. Archiving as-is.")
        archive_note(note_path, dry_run)
        return

    print(f"  {len(chunks)} chunk(s) identified.")
    today = date.today().isoformat()
    attribution = f"Sean, {today}"

    written_dests = process_chunks(chunks, index, attribution, dry_run)

    if not written_dests:
        print("  All chunks skipped — archiving.")
        archive_note(note_path, dry_run)
        return

    if not dry_run:
        record_source_dest(note_path, written_dests[0])

    if not no_archive:
        archive_note(note_path, dry_run)
    else:
        print("  (archive deferred)")


def sort_inbox(dry_run: bool = False, single_file=None) -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    if single_file:
        files = [INBOX_DIR / single_file]
        missing = [f for f in files if not f.exists()]
        if missing:
            print(f"File not found in inbox: {single_file}")
            return
    else:
        files = sorted(
            f for f in list(INBOX_DIR.glob("*.md")) + list(INBOX_DIR.glob("*.txt"))
            if f.name.upper() not in ("README.MD", "SCRIPTS.MD")
            and not re.match(r"^\d{2}-\d{2}\.md$", f.name)
        )

    if not files:
        print("Inbox is empty — nothing to sort.")
        return

    print(f"Building source index…")
    index = build_source_index()
    print(f"  {len(index)} source pages indexed.")
    print(f"  {len(files)} inbox file(s) to process.")

    for note_path in files:
        process_file(note_path, index, dry_run)

    print(f"\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Route inbox notes to source pages")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview proposals without writing files")
    parser.add_argument("--file", metavar="FILENAME",
                        help="Process a single file from the inbox by name")
    args = parser.parse_args()
    sort_inbox(dry_run=args.dry_run, single_file=args.file)
