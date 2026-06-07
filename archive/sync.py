"""
sync.py — Process new notes from the inbox and weave them into the wiki.

Workflow:
  1. Read all .md (or .txt) files in <vault>/_inbox/
  2. For each new note, ask Claude which existing topic pages it relates to,
     and what new points to add (or if a new topic page should be created)
  3. Update affected topic pages with new content + attribution footnote
  4. Move processed note to <vault>/_sources/Inbox/ as a permanent record

Usage:
  python sync.py                  # process all files in _inbox/
  python sync.py --dry-run        # preview changes without writing
"""

import argparse
import os
import re
import shutil
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
INBOX_DIR = VAULT_PATH / "_inbox"
TOPICS_DIR = VAULT_PATH / "_topics"
SOURCES_DIR = VAULT_PATH / "_sources" / "Inbox"

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-sonnet-4-6"


def load_topics() -> list[dict]:
    """Load all existing topic pages."""
    topics = []
    for path in sorted(TOPICS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # Parse title from frontmatter or filename
        title_match = re.search(r'^title:\s*"?([^"\n]+)"?', text, re.MULTILINE)
        title = title_match.group(1) if title_match else path.stem
        topics.append({"path": path, "title": title, "content": text})
    return topics


def load_inbox() -> list[Path]:
    """Return all .md and .txt files in the inbox."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        f for f in list(INBOX_DIR.glob("*.md")) + list(INBOX_DIR.glob("*.txt"))
        if f.name.upper() not in ("README.MD", "SCRIPTS.MD")
        and not re.match(r"^\d{2}-\d{2}\.md$", f.name)
    ]
    return sorted(files)


def route_note(note_text: str, note_filename: str, topics: list[dict]) -> dict:
    """
    Ask Claude: which topics does this note relate to, and what should be added?
    Returns a dict with 'updates' (list of {topic_title, new_points}) and
    'new_topics' (list of {name, description}).
    """
    topic_index = "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(topics))

    system = (
        "You are a personal wiki editor. A user has added a new note to their knowledge base. "
        "Your job is to decide how to incorporate it into the wiki.\n\n"
        "Rules:\n"
        "1. Identify which existing topic pages the note relates to (can be multiple or none).\n"
        "2. For each matching topic, identify the specific new points from the note that belong there.\n"
        "3. The topic_title field MUST be copied exactly — character for character — from the "
        "   numbered list below. Do not paraphrase or abbreviate.\n"
        "4. If the note contains ideas that don't fit any existing topic but are substantial enough "
        "   for their own page, suggest a new topic.\n"
        "5. Return ONLY valid JSON in this schema:\n"
        "{\n"
        '  "updates": [\n'
        '    {"topic_title": "exact title from the list", "new_points": ["point 1", "point 2"]}\n'
        "  ],\n"
        '  "new_topics": [\n'
        '    {"name": "...", "description": "one sentence"}\n'
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"New note filename: {note_filename}\n\n"
        f"New note content:\n{note_text}\n\n"
        f"Existing topics (use these titles exactly):\n{topic_index}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json_safe_parse(raw)


def json_safe_parse(text: str) -> dict:
    import json
    try:
        return json.loads(text)
    except Exception as e:
        print(f"  [warn] JSON parse failed: {e}")
        print(f"  [raw response] {text[:500]}")
        return {"updates": [], "new_topics": []}


def apply_update(topic: dict, new_points: list[str], attribution: str,
                 dry_run: bool = False) -> bool:
    """Append new points and a new footnote to an existing topic page."""
    content = topic["content"]
    if not new_points:
        return False

    # Find or create the Sources section
    footnote_section = re.search(r"## Sources\s*\n", content)

    # Find the highest existing footnote number
    existing_refs = re.findall(r"\[\^(\d+)\]", content)
    next_ref = max((int(n) for n in existing_refs), default=0) + 1

    # Build new bullets with citations
    new_bullets = []
    new_footnotes = []
    for i, point in enumerate(new_points):
        ref_num = next_ref + i
        new_bullets.append(f"- {point}[^{ref_num}]")
        new_footnotes.append(f"[^{ref_num}]: {attribution}")

    added_section = (
        "\n\n## Recent Additions\n\n"
        + "\n".join(new_bullets)
    )

    footnote_block = "\n".join(new_footnotes)

    if footnote_section:
        # Insert new bullets before ## Sources, append footnotes after
        insert_pos = footnote_section.start()
        updated = (
            content[:insert_pos]
            + added_section
            + "\n\n"
            + content[insert_pos:]
            + "\n"
            + footnote_block
            + "\n"
        )
    else:
        # No sources section yet — append everything
        updated = content.rstrip() + added_section + "\n\n## Sources\n\n" + footnote_block + "\n"

    if dry_run:
        print(f"\n  [DRY RUN] Would update: {topic['path'].name}")
        for b in new_bullets:
            print(f"    {b}")
    else:
        topic["path"].write_text(updated, encoding="utf-8")

    return True


def create_new_topic_page(name: str, description: str,
                          note_text: str, attribution: str,
                          dry_run: bool = False):
    """Generate a minimal new topic page seeded with this note."""
    system = (
        "Write a short wiki topic page (Markdown, no code fences, no H1 title) "
        "based on the provided note. Keep it concise — this is a seed page that will "
        "grow over time. End with a '## Sources' section with one footnote:\n"
        "[^1]: <attribution>"
    )
    user_prompt = (
        f"Topic: {name}\nDescription: {description}\n\n"
        f"Source note:\n{note_text}\n\n"
        f"Attribution: {attribution}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    body = resp.content[0].text.strip()
    safe_name = re.sub(r'[\\/:*?"<>|]', "-", name).strip()
    frontmatter = (
        f'---\ntitle: "{name}"\ntype: topic\ndescription: "{description}"\n---'
    )
    content = f"{frontmatter}\n\n# {name}\n\n{body}\n"

    out_path = TOPICS_DIR / f"{safe_name}.md"
    if dry_run:
        print(f"\n  [DRY RUN] Would create new topic page: {out_path.name}")
    else:
        TOPICS_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"  Created new topic page: {out_path.name}")


def archive_note(note_path: Path, dry_run: bool = False):
    """Move processed note from inbox to _sources/Inbox/."""
    dest = SOURCES_DIR / note_path.name
    if dry_run:
        print(f"  [DRY RUN] Would archive: {note_path.name} → _sources/Inbox/")
    else:
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        # Handle filename conflicts
        if dest.exists():
            stem = note_path.stem
            suffix = note_path.suffix
            dest = SOURCES_DIR / f"{stem}_{date.today().isoformat()}{suffix}"
        shutil.move(str(note_path), str(dest))
        print(f"  Archived to: {dest}")


def confirm(prompt: str) -> bool:
    """Prompt y/n; default yes on Enter."""
    answer = input(f"{prompt} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


def process_updates_interactive(updates: list[dict], note_text: str, note_filename: str,
                                topics: list[dict], attribution: str, dry_run: bool) -> int:
    """
    Walk through proposed updates interactively. On rejection, offer re-route or skip.
    Returns count of updates applied.
    """
    rejected = set()
    pending = [u for u in updates if u.get("new_points")]
    applied_count = 0

    while pending:
        update = pending.pop(0)
        target_title = update.get("topic_title", "")
        new_points = update.get("new_points", [])

        if target_title.lower() in rejected:
            continue

        match = next((t for t in topics if t["title"].lower() == target_title.lower()), None)
        if not match:
            print(f"  [warn] Topic not found: '{target_title}' — skipping")
            continue

        print(f"\n  → Update topic: \"{target_title}\"")
        for pt in new_points:
            print(f"      • {pt}")

        answer = input("  Apply? [Y/n/r=re-route/s=skip] ").strip().lower()

        if answer in ("", "y", "yes"):
            if apply_update(match, new_points, attribution, dry_run):
                print(f"  Updated: {target_title} (+{len(new_points)} point(s))")
                applied_count += 1

        elif answer in ("n", "r"):
            if answer == "n":
                sub = input("  [r]e-route for alternate topic / [s]kip? ").strip().lower()
                if sub != "r":
                    print(f"  Skipped: {target_title}")
                    continue
            rejected.add(target_title.lower())
            filtered = [t for t in topics if t["title"].lower() not in rejected]
            print(f"  Re-routing (excluding {len(rejected)} rejected topic(s))…")
            new_routing = route_note(note_text, note_filename, filtered)
            new_updates = [
                u for u in new_routing.get("updates", [])
                if u.get("new_points") and u.get("topic_title", "").lower() not in rejected
            ]
            if not new_updates:
                print("  No alternate topics found — skipping.")
            else:
                pending = new_updates + pending  # prepend so they're reviewed next

        else:
            print(f"  Skipped: {target_title}")

    return applied_count


def sync(dry_run: bool = False, interactive: bool = False, single_file: str = None):
    if single_file:
        p = Path(single_file)
        if not p.is_absolute():
            p = INBOX_DIR / single_file
        if not p.exists():
            print(f"File not found: {p}")
            return
        inbox_files = [p]
    else:
        inbox_files = load_inbox()
    if not inbox_files:
        print("Inbox is empty — nothing to sync.")
        return

    topics = load_topics()
    today = date.today().isoformat()

    print(f"Found {len(inbox_files)} note(s) in inbox. {len(topics)} existing topic pages.\n")

    for note_path in inbox_files:
        print(f"Processing: {note_path.name}")
        note_text = note_path.read_text(encoding="utf-8")

        # Use the source page as attribution if sort_inbox.py recorded it
        dest_match = re.search(r'^source_dest:\s*"?([^"\n]+)"?', note_text, re.MULTILINE)
        if dest_match:
            dest_rel = dest_match.group(1).strip().removesuffix(".md")
            attribution = f"[[_sources/{dest_rel}]] — Sean, {today}"
        else:
            attribution = f"Sean, {today}"

        routing = route_note(note_text, note_path.name, topics)
        updated_any = False

        proposed_updates = [u for u in routing.get("updates", []) if u.get("new_points")]
        proposed_new = routing.get("new_topics", [])

        if interactive:
            if not proposed_updates and not proposed_new:
                print("  No routing proposals — will archive as-is.")
            else:
                print(f"\n  {len(proposed_updates)} topic update(s) proposed.\n")

        # Update existing topics
        if interactive:
            n = process_updates_interactive(
                proposed_updates, note_text, note_path.name, topics, attribution, dry_run
            )
            if n:
                updated_any = True
        else:
            for update in proposed_updates:
                target_title = update.get("topic_title", "")
                new_points = update.get("new_points", [])
                match = next(
                    (t for t in topics if t["title"].lower() == target_title.lower()), None
                )
                if not match:
                    print(f"  [warn] Topic not found: '{target_title}' — skipping")
                    continue
                if apply_update(match, new_points, attribution, dry_run):
                    print(f"  Updated topic: {target_title} (+{len(new_points)} point(s))")
                    updated_any = True

        # Create new topics
        for new_topic in proposed_new:
            name = new_topic.get("name", "Untitled")
            if interactive and not confirm(f"  Create new topic \"{name}\"?"):
                print(f"  Skipped new topic: {name}")
                continue
            create_new_topic_page(
                name=name,
                description=new_topic.get("description", ""),
                note_text=note_text,
                attribution=attribution,
                dry_run=dry_run,
            )
            updated_any = True

        if not updated_any:
            print(f"  No matching topics found — archiving as-is")

        if not single_file:
            archive_note(note_path, dry_run)
        print()

    print("Sync complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync inbox notes into the wiki")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing files")
    parser.add_argument("--interactive", action="store_true",
                        help="Prompt for confirmation before each update")
    parser.add_argument("--file", metavar="PATH",
                        help="Process a single file (absolute path or inbox filename)")
    args = parser.parse_args()
    sync(dry_run=args.dry_run, interactive=args.interactive, single_file=args.file)
