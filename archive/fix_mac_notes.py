"""
fix_mac_notes.py — Clean up formatting bugs in Mac Notes source files.

Fixes:
  1. HTML entities (&quot; → ", &amp; → &, etc.)
  2. Shattered title headings (duplicate title repeated as fragments after the first heading)
  3. Shattered subheadings (consecutive same-level headings whose text are short fragments
     of a single word — merged into one heading)
  4. Empty headings (lines that are just # or ## with no text)

Usage:
  python3 fix_mac_notes.py           # fix all Mac Notes source files (dry run first)
  python3 fix_mac_notes.py --apply   # write changes to disk
"""

import argparse
import html
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
MAC_NOTES_DIR = VAULT_PATH / "_sources" / "Mac Notes"


# ── HTML entity fix ────────────────────────────────────────────────────────

def fix_entities(text: str) -> str:
    # html.unescape handles &quot; &amp; &lt; &gt; &#NNN; &#xNN; etc.
    return html.unescape(text)


# ── Heading helpers ────────────────────────────────────────────────────────

HEADING_RE = re.compile(r'^(#{1,6})(\s*)(.*)$')

def is_fragment(text: str) -> bool:
    """True if text looks like a word fragment (short, letters only, no spaces/digits)."""
    t = text.strip()
    if not t:
        return False
    # Allow Cyrillic, Latin, accented — but no spaces, digits, or punctuation
    return bool(re.match(r'^[A-Za-zÀ-ÖØ-öø-ÿА-яёЁ]{1,5}$', t))


def get_heading_level(line: str):
    """Return (level_str, text) or None if not a heading."""
    m = HEADING_RE.match(line)
    if m:
        return m.group(1), m.group(3).strip()
    return None


# ── Main fix logic ─────────────────────────────────────────────────────────

def fix_headings(lines: list[str], title: str) -> list[str]:
    """
    Pass 1: Remove empty headings.
    Pass 2: Remove the shattered-title block — the sequence of level-1 headings
            (with only blank lines between them) that immediately follows the first
            correct title heading. Mac Notes always shards the title right after it.
    Pass 3: Merge shattered subheadings — consecutive same-level headings whose
            texts are short fragments (≤5 letters, no spaces/digits). Only merges
            if at least one fragment in the run is ≤3 chars (avoids merging real
            short standalone headings like "Stanley" + "Baldwin").
    """
    title_norm = re.sub(r'[\s\(\)\-]', '', title).lower()

    # --- Pass 1: remove empty headings ---
    lines = [l for l in lines if not re.match(r'^#{1,6}\s*$', l)]

    # --- Pass 2: remove shattered-title block ---
    # After the first level-1 heading whose text matches the title, consume all
    # subsequent level-1 headings (and blank lines between them) until real content.
    result = []
    title_found = False
    in_title_zone = False

    for line in lines:
        h = get_heading_level(line)

        if not title_found:
            result.append(line)
            if h and h[0] == '#':
                text_norm = re.sub(r'[\s\(\)\-]', '', h[1]).lower()
                if text_norm == title_norm:
                    title_found = True
                    in_title_zone = True
        elif in_title_zone:
            if line.strip() == '':
                continue  # skip blank lines in the zone
            elif h and h[0] == '#':
                continue  # skip level-1 heading fragments in the zone
            else:
                # First real content exits the zone
                in_title_zone = False
                result.append('')   # restore one blank line before content
                result.append(line)
        else:
            result.append(line)

    # --- Pass 3: merge shattered subheadings ---
    merged = []
    i = 0
    while i < len(result):
        h = get_heading_level(result[i])
        if h:
            level, text = h
            if is_fragment(text):
                # Collect a run of same-level fragment headings (blank lines ok between)
                run_texts = [text]
                run_end = i + 1
                j = i + 1
                while j < len(result):
                    if result[j].strip() == '':
                        j += 1
                        continue
                    h2 = get_heading_level(result[j])
                    if h2 and h2[0] == level and is_fragment(h2[1]):
                        run_texts.append(h2[1])
                        run_end = j + 1
                        j += 1
                    else:
                        break

                # Only merge if ≥2 fragments AND at least one is ≤3 chars
                # (guards against merging real short words like Stanley + Baldwin)
                if len(run_texts) >= 2 and min(len(t) for t in run_texts) <= 3:
                    merged.append(f'{level} {"".join(run_texts)}')
                    i = run_end
                    continue
            merged.append(result[i])
        else:
            merged.append(result[i])
        i += 1

    return merged


def fix_file(path: Path, apply: bool) -> bool:
    """Fix one file. Returns True if changes were made."""
    original = path.read_text(encoding='utf-8')

    # Extract frontmatter title
    title = path.stem
    fm_match = re.match(r'^---\n(.*?)\n---\n', original, re.DOTALL)
    if fm_match:
        for fm_line in fm_match.group(1).splitlines():
            if fm_line.startswith('title:'):
                title = fm_line.split(':', 1)[1].strip().strip('"')
                break

    # Split into frontmatter + body
    if fm_match:
        frontmatter = original[:fm_match.end()]
        body_lines = original[fm_match.end():].splitlines()
    else:
        frontmatter = ''
        body_lines = original.splitlines()

    # Fix entities in body
    body_lines = [fix_entities(l) for l in body_lines]

    # Fix headings
    body_lines = fix_headings(body_lines, title)

    # Reconstruct
    fixed_body = '\n'.join(body_lines)
    # Collapse 3+ consecutive blank lines to 2
    fixed_body = re.sub(r'\n{3,}', '\n\n', fixed_body)
    fixed = frontmatter + fixed_body.lstrip('\n')
    if not fixed.endswith('\n'):
        fixed += '\n'

    if fixed == original:
        return False

    if apply:
        path.write_text(fixed, encoding='utf-8')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='Write fixes to disk (default: dry run)')
    args = parser.parse_args()

    files = list(MAC_NOTES_DIR.rglob('*.md'))
    changed = []
    for f in sorted(files):
        if fix_file(f, apply=args.apply):
            changed.append(f.name)

    if not changed:
        print('No files need fixing.')
        return

    action = 'Fixed' if args.apply else 'Would fix (dry run — pass --apply to write)'
    print(f'{action} {len(changed)} file(s):')
    for name in changed:
        print(f'  {name}')

    if not args.apply:
        print('\nRe-run with --apply to write changes.')


if __name__ == '__main__':
    main()
