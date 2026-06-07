"""
reimport_page.py — Re-fetch and reconvert a single OneNote page.

Useful when a page has been updated in OneNote since the original extraction.
Looks up the page by title in raw/manifest.json, fetches fresh HTML from the
Graph API, and overwrites the _sources/ Markdown file.

Usage:
  python3 reimport_page.py "Writing"
  python3 reimport_page.py "Writing" --notebook "Sean's Notebook" --section AI
  python3 reimport_page.py "Writing" --dry-run
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Reuse auth + conversion helpers from existing scripts
sys.path.insert(0, str(Path(__file__).parent))
import extract as _ext
import convert as _conv

RAW_DIR   = Path("raw")
PAGES_DIR = RAW_DIR / "_pages"
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
SOURCES_DIR = VAULT_PATH / "_sources"


def find_page(title: str, notebook: str = None, section: str = None) -> list[dict]:
    """Return all manifest entries matching the given title (optionally filtered)."""
    manifest_path = RAW_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("raw/manifest.json not found — run extract.py first.")
    manifest = json.loads(manifest_path.read_text())

    results = []
    for nb in manifest:
        if notebook and nb["name"].lower() != notebook.lower():
            continue
        sections = list(nb.get("sections", []))
        for g in nb.get("sectionGroups", []):
            sections += list(g.get("sections", []))
        for sec in sections:
            if section and sec["name"].lower() != section.lower():
                continue
            for page in sec.get("pages", []):
                if page["title"].lower() == title.lower():
                    results.append({
                        "page":     page,
                        "notebook": nb["name"],
                        "section":  sec["name"],
                        "section_dir": SOURCES_DIR / nb["name"] / sec["name"],
                    })
    return results


def reimport(title: str, notebook: str = None, section: str = None,
             dry_run: bool = False) -> None:
    matches = find_page(title, notebook, section)
    if not matches:
        print(f"Page not found: '{title}'" +
              (f" in {notebook}/{section}" if notebook or section else ""))
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple matches for '{title}':")
        for m in matches:
            print(f"  {m['notebook']} / {m['section']} / {m['page']['title']}")
        print("Use --notebook and --section to disambiguate.")
        sys.exit(1)

    m        = matches[0]
    page     = m["page"]
    page_id  = page["id"]
    sec_dir  = m["section_dir"]
    out_path = sec_dir / f"{_conv.safe_filename(title)}.md"

    print(f"Page:     {m['notebook']} / {m['section']} / {title}")
    print(f"Page ID:  {page_id}")
    print(f"Output:   {out_path}")

    if dry_run:
        print("[dry-run] Would fetch fresh HTML and reconvert.")
        return

    # 1. Fetch fresh HTML from OneNote
    print("Fetching fresh HTML from OneNote…")
    headers = _ext.make_headers()
    html = _ext.download_page_content(page_id, headers)
    html_path = PAGES_DIR / f"{page_id}.html"
    html_path.write_bytes(html)
    print(f"  Saved {len(html):,} bytes → {html_path.name}")

    # 2. Force reconvert — remove existing file so convert_page doesn't skip it
    if out_path.exists():
        out_path.unlink()
        print(f"  Removed stale: {out_path.name}")

    # 3. Convert
    _conv.convert_page(page, m["notebook"], m["section"], sec_dir)
    print(f"  Reconverted → {out_path}")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-fetch and reconvert a OneNote page")
    parser.add_argument("title", help="Exact page title (case-insensitive)")
    parser.add_argument("--notebook", help="Filter by notebook name")
    parser.add_argument("--section",  help="Filter by section name")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Preview without fetching or writing")
    args = parser.parse_args()
    reimport(args.title, args.notebook, args.section, args.dry_run)
