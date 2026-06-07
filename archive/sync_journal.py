"""
sync_journal.py — Periodically synthesize journal entries into topic pages.

Reads:  vault/_sources/Journal/Daily/*.md
Writes: vault/_topics/Calendar of Wisdom.md
        vault/_topics/Al-Anon Reflections.md
        vault/_topics/Classical Wisdom.md

Run this weekly or monthly as your Daily entries accumulate.
Each topic page draws themes and quotes across all entries, citing
back to the specific day as a footnote.

Usage:
  python3 sync_journal.py              # refresh all topic pages
  python3 sync_journal.py --topic "Classical Wisdom"  # refresh one
"""

import argparse
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH   = Path(os.environ.get("VAULT_PATH", "./vault"))
JOURNAL_DIR  = VAULT_PATH / "_sources" / "Journal" / "Daily"
TOPICS_DIR   = VAULT_PATH / "_topics"

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL  = "claude-sonnet-4-6"


# ── Load entries ───────────────────────────────────────────────────────────

def load_journal_entries() -> list[dict]:
    """Load all daily journal files, returning structured entry dicts."""
    entries = []
    for path in sorted(JOURNAL_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")

        # Parse frontmatter
        fm = {}
        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip().strip('"')

        # Parse sections from body
        body = re.sub(r"^---\n.*?\n---\n*", "", text, flags=re.DOTALL)
        sections = {}

        section_map = {
            "Calendar of Wisdom": "tolstoy",
            "Al-Anon": "alanon",
            "Sententiae Antiquae": "sententiae",
            "Vocabulary": "vocab",
        }

        for heading, key in section_map.items():
            pattern = rf"## {heading}\n+(.*?)(?=\n## |\Z)"
            m = re.search(pattern, body, re.DOTALL)
            if m:
                sections[key] = m.group(1).strip()

        date_display = fm.get("date_display", path.stem)
        wikilink = f"_sources/Journal/Daily/{path.stem}"

        entries.append({
            "date_display": date_display,
            "sort_date":    fm.get("sort_date", path.stem),
            "wikilink":     wikilink,
            "sections":     sections,
            "path":         str(path),
        })

    return entries


# ── Topic generators ───────────────────────────────────────────────────────

def generate_topic_page(topic_name: str, section_key: str,
                        entries: list[dict], system_prompt: str) -> str:
    """Generate a synthesis topic page for one journal section type."""

    relevant = [e for e in entries if section_key in e["sections"]]
    if not relevant:
        print(f"  No entries with '{section_key}' section — skipping.")
        return ""

    print(f"  {len(relevant)} entries with {section_key} content...")

    # Build corpus with citations
    corpus_parts = []
    for i, e in enumerate(relevant, 1):
        corpus_parts.append(
            f"[^{i}] {e['date_display']} | [[{e['wikilink']}]]\n"
            f"{e['sections'][section_key]}"
        )
    corpus = "\n\n---\n\n".join(corpus_parts)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": corpus}],
    )
    body = resp.content[0].text.strip()

    frontmatter = "\n".join([
        "---",
        f'title: "{topic_name}"',
        "type: topic",
        f"entry_count: {len(relevant)}",
        "---",
    ])

    return f"{frontmatter}\n\n# {topic_name}\n\n{body}\n"


TOPIC_CONFIGS = {
    "Calendar of Wisdom": {
        "section_key": "tolstoy",
        "system": (
            "You are synthesizing a personal calendar of wisdom — reflections and quotes "
            "drawn from daily readings across the year. These are drawn from Leo Tolstoy's "
            "'A Calendar of Wisdom' and related sources.\n\n"
            "Organize the entries thematically (e.g. 'On Impermanence', 'On Equality', "
            "'On the Inner Life'). Under each theme, quote or paraphrase the most resonant "
            "passages with inline citations [^n] pointing to the day they came from.\n\n"
            "Write in a contemplative, unhurried tone. "
            "End with a '## Sources' section listing all footnotes.\n"
            "Return ONLY Markdown, no code fences, no H1 title."
        ),
    },
    "Al-Anon Reflections": {
        "section_key": "alanon",
        "system": (
            "You are synthesizing personal Al-Anon reflections from a daily reader practice. "
            "These are drawn from 'Courage to Change' and personal synthesis.\n\n"
            "Identify recurring themes (e.g. 'Acceptance', 'Surrender', 'Boundaries', "
            "'Prayer and Trust'). Under each theme, draw out the key insights with "
            "inline citations [^n].\n\n"
            "Write with warmth and groundedness — these are personal spiritual notes. "
            "End with a '## Sources' section.\n"
            "Return ONLY Markdown, no code fences, no H1 title."
        ),
    },
    "Classical Wisdom": {
        "section_key": "sententiae",
        "system": (
            "You are compiling a personal anthology of classical wisdom — quotes from "
            "ancient Greek and Latin writers, drawn from daily reading practice.\n\n"
            "Organize by theme first (e.g. 'On Fortune', 'On Courage', 'On the Good Life'), "
            "then by author within each theme.\n\n"
            "For each entry:\n"
            "- Bold the author name\n"
            "- Blockquote the English translation\n"
            "- Italicize the original Greek or Latin beneath it\n"
            "- Add an inline citation [^n]\n\n"
            "End with a '## Sources' section and a '## Authors' index listing all authors cited.\n"
            "Return ONLY Markdown, no code fences, no H1 title."
        ),
    },
}


# ── Main ───────────────────────────────────────────────────────────────────

def sync_all(only_topic: str = None):
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)

    entries = load_journal_entries()
    if not entries:
        print(f"No journal entries found in {JOURNAL_DIR}")
        print("Run: python3 extract_notes.py --extract")
        return

    print(f"Loaded {len(entries)} journal entries.\n")

    topics_to_run = TOPIC_CONFIGS
    if only_topic:
        if only_topic not in TOPIC_CONFIGS:
            print(f"Unknown topic '{only_topic}'. Available: {list(TOPIC_CONFIGS.keys())}")
            return
        topics_to_run = {only_topic: TOPIC_CONFIGS[only_topic]}

    for topic_name, config in topics_to_run.items():
        print(f"Generating: {topic_name}")
        content = generate_topic_page(
            topic_name=topic_name,
            section_key=config["section_key"],
            entries=entries,
            system_prompt=config["system"],
        )
        if content:
            safe_name = re.sub(r'[\\/:*?"<>|]', "-", topic_name)
            out_path = TOPICS_DIR / f"{safe_name}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  Written: {out_path.name}\n")

    print("Journal sync complete.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", type=str, default=None,
                        help="Refresh only this topic page (exact name)")
    args = parser.parse_args()
    sync_all(only_topic=args.topic)


if __name__ == "__main__":
    main()
