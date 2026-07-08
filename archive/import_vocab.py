"""
import_vocab.py — Parse words_document.docx and build two vault files:

1. _sources/Vocabulary/words document.md  — every entry preserved faithfully
2. _topics/Vocabulary.md                  — Claude-organized thematic groupings
                                             with footnote citations back to source

Usage:
  python3 import_vocab.py                  # parse + generate topic page
  python3 import_vocab.py --source-only    # just parse, skip Claude synthesis
"""

import argparse
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Archived one-off script — set WORDS_DOCX_PATH in .env to the source .docx
# before running (not part of the regular pipeline, so not in the default .env).
_docx_env = os.environ.get("WORDS_DOCX_PATH", "")
if not _docx_env:
    raise SystemExit("Set WORDS_DOCX_PATH in .env to the source .docx before running import_vocab.py.")
DOCX_PATH = Path(_docx_env)
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
SOURCE_DIR = VAULT_PATH / "_sources" / "Vocabulary"
TOPICS_DIR = VAULT_PATH / "_topics"
SOURCE_FILE = SOURCE_DIR / "words document.md"
TOPIC_FILE  = TOPICS_DIR / "Vocabulary.md"


# ── Parsing ────────────────────────────────────────────────────────────────

def get_indent_level(para) -> int:
    """Return the outline indent level (0 = top-level entry, 1 = sub-definition)."""
    try:
        ilvl = para._p.pPr.numPr.ilvl.val
        return int(ilvl)
    except AttributeError:
        pass
    # Fallback: use left indent (sub-items are indented ~720+ twips more)
    try:
        indent = para.paragraph_format.left_indent
        if indent and indent > 500000:  # EMUs; ~0.35 inches
            return 1
    except Exception:
        pass
    return 0


def parse_top_level(text: str) -> dict:
    """
    Parse a top-level entry like:
      'louche: /luʃ/ (from Latin luscus...) Of questionable taste...'
      'dragoon: (v.) to coerce'
      'mirth: The emotion typically...'
    Returns dict with keys: word, pronunciation, pos, etymology, definition
    """
    entry = {"word": "", "pronunciation": "", "pos": "", "etymology": "", "definition": ""}

    # Split on first colon to get word vs rest
    if ":" not in text:
        entry["word"] = text.strip()
        return entry

    word_part, _, rest = text.partition(":")
    entry["word"] = word_part.strip()
    rest = rest.strip()

    # Extract IPA pronunciation /.../
    ipa_match = re.match(r"(/[^/]+/)\s*", rest)
    if ipa_match:
        entry["pronunciation"] = ipa_match.group(1)
        rest = rest[ipa_match.end():]

    # Extract etymology (from ...) at start
    etym_match = re.match(r"(\(from [^)]+\))\s*", rest)
    if etym_match:
        entry["etymology"] = etym_match.group(1)
        rest = rest[etym_match.end():]

    # Extract part of speech (v.) (n.) (adj.) (adv.) at start
    pos_match = re.match(r"(\([a-z]+\.?\))\s*", rest)
    if pos_match:
        entry["pos"] = pos_match.group(1)
        rest = rest[pos_match.end():]

    entry["definition"] = rest.strip()
    return entry


def parse_docx(path: Path) -> list:
    """
    Parse the vocabulary docx into a list of entry dicts:
    {word, pronunciation, pos, etymology, definition, sub_definitions: [...]}
    """
    try:
        from docx import Document
    except ImportError:
        raise ImportError("Run: pip3 install python-docx")

    doc = Document(path)
    entries = []
    current = None

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        level = get_indent_level(para)

        if level == 0:
            # New top-level entry
            if current:
                entries.append(current)
            current = parse_top_level(text)
            current["sub_definitions"] = []
        elif level == 1 and current is not None:
            # Sub-definition — strip leading "a. " "b. " etc.
            sub = re.sub(r"^[a-z]\.\s*", "", text).strip()
            # Also strip leading (v.) pos tags on sub-defs
            pos_match = re.match(r"(\([a-z]+\.?\))\s*", sub)
            if pos_match:
                sub = pos_match.group(1) + " " + sub[pos_match.end():]
            current["sub_definitions"].append(sub)

    if current:
        entries.append(current)

    return entries


# ── Source file generation ─────────────────────────────────────────────────

def entry_to_markdown(entry: dict, number: int) -> str:
    """Render one entry as a Markdown block."""
    lines = []

    # Header line: ## word
    header = f"## {entry['word']}"
    lines.append(header)

    # Metadata line
    meta_parts = []
    if entry["pronunciation"]:
        meta_parts.append(entry["pronunciation"])
    if entry["pos"]:
        meta_parts.append(f"*{entry['pos']}*")
    if entry["etymology"]:
        meta_parts.append(f"*{entry['etymology']}*")
    if meta_parts:
        lines.append(" · ".join(meta_parts))

    # Definition(s)
    if entry["definition"] and not entry["sub_definitions"]:
        lines.append(f"{entry['definition']}")
    elif entry["definition"] and entry["sub_definitions"]:
        # Main def on first line, then sub-defs as list
        lines.append(f"{entry['definition']}")
        for sub in entry["sub_definitions"]:
            lines.append(f"- {sub}")
    elif entry["sub_definitions"]:
        for sub in entry["sub_definitions"]:
            lines.append(f"- {sub}")

    return "\n".join(lines)


def write_source_file(entries: list):
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    frontmatter = (
        "---\n"
        'title: "Vocabulary"\n'
        "type: source\n"
        f"entry_count: {len(entries)}\n"
        "---"
    )

    blocks = [frontmatter, "\n# Vocabulary\n"]
    for i, entry in enumerate(entries, 1):
        blocks.append(entry_to_markdown(entry, i))

    content = "\n\n---\n\n".join(blocks) + "\n"
    SOURCE_FILE.write_text(content, encoding="utf-8")
    print(f"Source file written: {SOURCE_FILE} ({len(entries)} entries)")


# ── Topic page synthesis ───────────────────────────────────────────────────

def synthesize_vocab_topic(entries: list):
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    print(f"Synthesizing vocabulary topic page ({len(entries)} entries)...")

    # Build a compact representation for Claude
    compact = []
    for i, e in enumerate(entries, 1):
        word = e["word"]
        defs = []
        if e["definition"]:
            defs.append(e["definition"])
        defs.extend(e["sub_definitions"])
        definition_text = "; ".join(defs[:2])  # first two definitions max
        compact.append(f"{i}. {word}: {definition_text}")

    # Split into batches to stay within context — ~300 words per batch request
    BATCH = 300
    batches = [compact[i:i+BATCH] for i in range(0, len(compact), BATCH)]

    # Step 1: Get thematic groupings
    print("  Step 1/2: Identifying themes...")
    all_words_text = "\n".join(compact)

    theme_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=(
            "You are organizing a personal vocabulary list of ~1000 words into thematic groups "
            "for a wiki page. Identify 15-25 meaningful themes (e.g. 'Character & Morality', "
            "'Language & Rhetoric', 'Movement & Action', 'Emotions', 'Architecture & Places', etc.). "
            "Return ONLY a JSON array of theme name strings, no other text."
        ),
        messages=[{"role": "user", "content": all_words_text}],
    )
    raw = theme_resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    import json
    themes = json.loads(raw)
    print(f"  Identified {len(themes)} themes: {', '.join(themes[:6])}...")

    # Step 2: Assign words to themes and generate the page
    print("  Step 2/2: Generating topic page...")

    assignment_resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8096,
        system=(
            "You are writing a thematic vocabulary wiki page. "
            "Organize the provided words under the given themes. "
            "Format each theme as a ## heading followed by a table:\n\n"
            "| Word | Definition |\n|------|------------|\n| word | definition |\n\n"
            "Include a footnote after each theme section: [^n]: [[_sources/Vocabulary/words document]] — *Sean's vocabulary list*\n"
            "At the end include a '## Sources' section with the footnote definitions. "
            "Return ONLY Markdown, no code fences."
        ),
        messages=[{"role": "user", "content": (
            f"Themes: {json.dumps(themes)}\n\n"
            f"Words:\n{all_words_text}"
        )}],
    )

    body = assignment_resp.content[0].text.strip()

    frontmatter = (
        "---\n"
        'title: "Vocabulary"\n'
        "type: topic\n"
        f'description: "Personal vocabulary list of {len(entries)} words, organized thematically"\n'
        f"source_count: 1\n"
        "---"
    )

    content = f"{frontmatter}\n\n# Vocabulary\n\n{body}\n"
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    TOPIC_FILE.write_text(content, encoding="utf-8")
    print(f"Topic page written: {TOPIC_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true",
                        help="Only generate the source file, skip Claude synthesis")
    args = parser.parse_args()

    print(f"Parsing {DOCX_PATH.name}...")
    entries = parse_docx(DOCX_PATH)
    print(f"  Found {len(entries)} entries")

    write_source_file(entries)

    if not args.source_only:
        synthesize_vocab_topic(entries)

    print("\nDone. Files written:")
    print(f"  Source: {SOURCE_FILE}")
    if not args.source_only:
        print(f"  Topic:  {TOPIC_FILE}")


if __name__ == "__main__":
    main()
