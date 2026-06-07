"""
extract_notes.py — Extract Mac Notes to the Obsidian vault.

Steps:
  python3 extract_notes.py --list      # show checkbox UI, save selection
  python3 extract_notes.py --extract   # extract selected notes to vault
  python3 extract_notes.py             # do both in sequence

Special handling:
  - Note named "Daily" → parsed into _sources/Journal/Daily/MM-DD.md
  - All others → _sources/Mac Notes/<Folder>/<Title>.md

Requires: pip3 install questionary
"""

import argparse
import calendar
import html
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
NOTES_DIR   = VAULT_PATH / "_sources" / "Mac Notes"
JOURNAL_DIR = VAULT_PATH / "_sources" / "Journal" / "Daily"
SELECTION_FILE = Path("notes_selection.json")

DAILY_NOTE_NAME = "Daily"

# ── AppleScript helpers ────────────────────────────────────────────────────

def run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def list_all_notes() -> list[dict]:
    """Return list of {account, folder, title} for every note."""
    script = """
    tell application "Notes"
        set output to ""
        repeat with theAccount in accounts
            set acctName to name of theAccount
            repeat with theFolder in folders of theAccount
                set folderName to name of theFolder
                repeat with theNote in notes of theFolder
                    set noteTitle to name of theNote
                    set output to output & acctName & "|||" & folderName & "|||" & noteTitle & "\n"
                end repeat
            end repeat
        end repeat
        return output
    end tell
    """
    raw = run_applescript(script)
    notes = []
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) == 3:
            notes.append({
                "account": parts[0].strip(),
                "folder":  parts[1].strip(),
                "title":   parts[2].strip(),
            })
    return notes


def get_note_body(title: str, folder: str) -> str:
    """Fetch the HTML body of a note by title and folder."""
    # Escape quotes in title/folder for AppleScript
    safe_title  = title.replace('"', '\\"')
    safe_folder = folder.replace('"', '\\"')
    script = f"""
    tell application "Notes"
        set theFolder to first folder whose name is "{safe_folder}"
        set theNote to first note of theFolder whose name is "{safe_title}"
        return body of theNote
    end tell
    """
    return run_applescript(script)


# ── HTML → plain text ──────────────────────────────────────────────────────

def html_to_text(html_body: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    # Replace block tags with newlines
    text = re.sub(r"<br\s*/?>", "\n", html_body, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</div>|</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    # Normalize whitespace but preserve intentional newlines
    lines = [line.rstrip() for line in text.splitlines()]
    # Collapse 3+ blank lines to 2
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return result.strip()


def html_to_markdown(html_body: str) -> str:
    """Convert HTML to Markdown using markdownify."""
    try:
        from markdownify import markdownify as md
        converted = md(html_body, heading_style="ATX", bullets="-",
                       strip=["script", "style"])
        return re.sub(r"\n{3,}", "\n\n", converted).strip()
    except ImportError:
        return html_to_text(html_body)


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[\\/:*?"<>|]', "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100]


# ── Checkbox selection UI ──────────────────────────────────────────────────

def run_selection_ui(notes: list[dict]) -> list[dict]:
    try:
        import questionary
    except ImportError:
        raise ImportError("Run: pip3 install questionary")

    # Group by account → folder
    grouped: dict[str, dict[str, list]] = {}
    for n in notes:
        grouped.setdefault(n["account"], {}).setdefault(n["folder"], []).append(n)

    choices = []
    for account, folders in grouped.items():
        choices.append(questionary.Separator(f"━━ {account} ━━"))
        for folder, folder_notes in folders.items():
            choices.append(questionary.Separator(f"  [{folder}]"))
            for note in folder_notes:
                choices.append(
                    questionary.Choice(
                        title=f"    {note['title']}",
                        value=note,
                        checked=True,  # default: all selected
                    )
                )

    print("\nUse ↑↓ to navigate, Space to toggle, Enter to confirm.")
    print("All notes are pre-selected — deselect the ones you don't want.\n")

    selected = questionary.checkbox(
        "Select notes to extract:",
        choices=choices,
    ).ask()

    return selected or []


# ── Daily note parser ──────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,  "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DATE_PATTERNS = [
    re.compile(r"^(\d{1,2})/(\d{1,2})$"),                    # 2/18
    re.compile(r"^([A-Za-z]{3,9})\s+(\d{1,2})$"),            # Feb 17 / February 17
]

def parse_date_header(line: str):
    """
    Parse a date header line.
    Returns (display_str, sort_str) e.g. ("February 18", "02-18") or None.
    """
    line = line.strip()

    # M/D format
    m = DATE_PATTERNS[0].match(line)
    if m:
        month_num = int(m.group(1))
        day = int(m.group(2))
        if 1 <= month_num <= 12 and 1 <= day <= 31:
            month_name = calendar.month_name[month_num]
            return f"{month_name} {day}", f"{month_num:02d}-{day:02d}"

    # Mon D / Month D format
    m = DATE_PATTERNS[1].match(line)
    if m:
        month_str = m.group(1).lower()[:3]
        day = int(m.group(2))
        month_num = MONTH_MAP.get(month_str)
        if month_num and 1 <= day <= 31:
            month_name = calendar.month_name[month_num]
            return f"{month_name} {day}", f"{month_num:02d}-{day:02d}"

    return None


def parse_sententiae(text: str) -> dict:
    """
    Parse a Sententiae Antiquae section:
      Author, Source Title
      "English translation"
      Original Greek/Latin
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    result = {"author": "", "translation": "", "original": ""}

    if not lines:
        return result

    result["author"] = lines[0]

    translation_lines = []
    original_lines = []
    in_translation = False
    in_original = False

    for line in lines[1:]:
        if line.startswith('"') or line.startswith('\u201c') or in_translation:
            in_translation = True
            translation_lines.append(line.strip('"').strip('\u201c').strip('\u201d'))
            if line.endswith('"') or line.endswith('\u201d'):
                in_translation = False
                in_original = True
        elif in_original or original_lines:
            original_lines.append(line)
        else:
            original_lines.append(line)

    result["translation"] = " ".join(translation_lines).strip()
    result["original"]    = " ".join(original_lines).strip()
    return result


SECTION_NAMES = ["tolstoy", "alanon", "sententiae", "vocab"]

def parse_daily_note(text: str) -> list[dict]:
    """
    Parse the full Daily note text into a list of entry dicts.
    Each entry: {display_date, sort_date, sections: {name: text}}
    """
    entries = []
    current_date = None
    current_lines = []

    def flush(date_display, date_sort, lines):
        raw = "\n".join(lines).strip()
        # Split by *** separator (with optional surrounding whitespace/newlines)
        parts = re.split(r"\n?\s*\*{3}\s*\n?", raw)
        parts = [p.strip() for p in parts if p.strip()]

        sections = {}
        present = []
        for i, part in enumerate(parts):
            if i < len(SECTION_NAMES):
                name = SECTION_NAMES[i]
                sections[name] = part
                present.append(name)

        entries.append({
            "display_date": date_display,
            "sort_date":    date_sort,
            "sections":     sections,
            "present":      present,
        })

    for line in text.splitlines():
        parsed = parse_date_header(line)
        if parsed:
            if current_date and current_lines:
                flush(current_date[0], current_date[1], current_lines)
            current_date = parsed
            current_lines = []
        elif current_date is not None:
            current_lines.append(line)

    if current_date and current_lines:
        flush(current_date[0], current_date[1], current_lines)

    return entries


def entry_to_markdown(entry: dict) -> str:
    """Render a parsed daily entry as a Markdown file."""
    sections_present = entry["present"]
    fm_lines = [
        "---",
        f'date_sort: "{entry["sort_date"]}"',
        f'date_display: "{entry["display_date"]}"',
        "type: journal",
        f'sections: [{", ".join(sections_present)}]',
        "---",
    ]
    frontmatter = "\n".join(fm_lines)
    lines = [frontmatter, f"\n# {entry['display_date']}\n"]

    secs = entry["sections"]

    if "tolstoy" in secs:
        lines.append("## Calendar of Wisdom\n")
        lines.append(secs["tolstoy"])

    if "alanon" in secs:
        lines.append("\n---\n\n## Al-Anon\n")
        lines.append(secs["alanon"])

    if "sententiae" in secs:
        lines.append("\n---\n\n## Sententiae Antiquae\n")
        sent = parse_sententiae(secs["sententiae"])
        if sent["author"]:
            lines.append(f"**{sent['author']}**\n")
        if sent["translation"]:
            lines.append(f'> "{sent["translation"]}"\n')
        if sent["original"]:
            lines.append(f"*{sent['original']}*")

    if "vocab" in secs:
        lines.append("\n---\n\n## Vocabulary\n")
        # Link to source entry if word matches "word: definition" format
        vocab_text = secs["vocab"].strip()
        word_match = re.match(r"^([^:]+):\s*(.+)$", vocab_text, re.DOTALL)
        if word_match:
            word = word_match.group(1).strip()
            defn = word_match.group(2).strip()
            lines.append(f"**{word}**: {defn}")
        else:
            lines.append(vocab_text)

    return "\n".join(lines) + "\n"


def write_daily_entries(entries: list[dict], dry_run: bool = False):
    """Write parsed daily entries to vault/Journal/Daily/."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0

    for entry in entries:
        filename = f"{entry['sort_date']}.md"
        out_path = JOURNAL_DIR / filename

        content = entry_to_markdown(entry)

        if dry_run:
            print(f"  [dry-run] {filename}")
            continue

        # Don't overwrite if already exists and is newer — preserve edits
        if out_path.exists():
            existing = out_path.read_text(encoding="utf-8")
            if existing.strip() == content.strip():
                skipped += 1
                continue

        out_path.write_text(content, encoding="utf-8")
        written += 1

    if not dry_run:
        print(f"  Daily entries: {written} written, {skipped} unchanged.")


# ── Regular note writer ────────────────────────────────────────────────────

def write_note(note: dict, body_html: str):
    """Convert and write a regular note to _sources/Mac Notes/."""
    folder_dir = NOTES_DIR / safe_filename(note["folder"])
    folder_dir.mkdir(parents=True, exist_ok=True)

    body_md = html_to_markdown(body_html)

    frontmatter = "\n".join([
        "---",
        f'title: "{note["title"]}"',
        "type: source",
        f'folder: "{note["folder"]}"',
        f'account: "{note["account"]}"',
        f'source: "Mac Notes"',
        "---",
    ])

    content = f"{frontmatter}\n\n# {note['title']}\n\n{body_md}\n"
    out_path = folder_dir / f"{safe_filename(note['title'])}.md"
    out_path.write_text(content, encoding="utf-8")


# ── Main ───────────────────────────────────────────────────────────────────

def cmd_list():
    print("Fetching notes from Mac Notes app...")
    notes = list_all_notes()
    print(f"Found {len(notes)} notes.\n")

    selected = run_selection_ui(notes)
    if not selected:
        print("No notes selected.")
        return

    SELECTION_FILE.write_text(json.dumps(selected, indent=2))
    print(f"\n{len(selected)} notes selected. Run with --extract to pull them into the vault.")


def cmd_extract():
    if not SELECTION_FILE.exists():
        raise FileNotFoundError("No selection found. Run with --list first.")

    selected = json.loads(SELECTION_FILE.read_text())
    print(f"Extracting {len(selected)} notes...\n")

    daily_count = sum(1 for n in selected if n["title"] == DAILY_NOTE_NAME)
    regular = [n for n in selected if n["title"] != DAILY_NOTE_NAME]

    # Handle Daily note
    for note in selected:
        if note["title"] != DAILY_NOTE_NAME:
            continue
        print(f"Parsing Daily note...")
        try:
            body_html = get_note_body(note["title"], note["folder"])
            body_text = html_to_text(body_html)
            entries = parse_daily_note(body_text)
            print(f"  Found {len(entries)} dated entries.")
            write_daily_entries(entries)
        except Exception as e:
            print(f"  ERROR parsing Daily note: {e}")

    # Handle regular notes
    print(f"\nExtracting {len(regular)} regular notes...")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)

    for i, note in enumerate(regular, 1):
        print(f"  [{i}/{len(regular)}] {note['folder']} / {note['title']}")
        try:
            body_html = get_note_body(note["title"], note["folder"])
            write_note(note, body_html)
        except Exception as e:
            print(f"    ERROR: {e}")

    print(f"\nDone. Notes written to {VAULT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Extract Mac Notes to Obsidian vault")
    parser.add_argument("--list",    action="store_true", help="Show selection UI")
    parser.add_argument("--extract", action="store_true", help="Extract selected notes")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.extract:
        cmd_extract()
    else:
        # Default: do both
        cmd_list()
        cmd_extract()


if __name__ == "__main__":
    main()
