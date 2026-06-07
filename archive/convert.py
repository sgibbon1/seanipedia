"""
convert.py — Convert raw OneNote HTML into Obsidian-ready Markdown files.

Reads:  raw/manifest.json + raw/_pages/<id>.html
Writes: <vault>/_sources/<Notebook>/<Section>/<Page Title>.md
        <vault>/_assets/<image files>

Each file gets YAML frontmatter with metadata and type: source.
Images are downloaded from the Graph API and saved to _assets/.
The OneNote hierarchy becomes the folder structure.
"""

import argparse
import json
import os
import re
import time
import unicodedata
from pathlib import Path

import msal
import requests
from dotenv import load_dotenv
from markdownify import markdownify as md

load_dotenv(override=True)

CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
SCOPES = ["https://graph.microsoft.com/Notes.Read"]

RAW_DIR = Path("raw")
TOKEN_CACHE_PATH = RAW_DIR / ".token_cache.bin"
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
SOURCES_DIR = VAULT_PATH / "_sources"
ASSETS_DIR = VAULT_PATH / "_assets"

# Set to True via --skip-images flag to defer image downloads
SKIP_IMAGES = False

# Shared auth session for image downloads
_auth_headers = None


def get_auth_headers() -> dict:
    global _auth_headers
    if _auth_headers:
        return _auth_headers

    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text())

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority="https://login.microsoftonline.com/consumers",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(
            "No cached auth token found. Run extract.py first to authenticate."
        )
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(
            "Could not refresh auth token. Run extract.py again to re-authenticate."
        )
    _auth_headers = {"Authorization": f"Bearer {result['access_token']}"}
    return _auth_headers


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[\\/:*?"<>|]', "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100]


def download_image(src: str, page_id: str) -> str:
    """
    Download an image from a Graph API URL and save to _assets/.
    Returns the Obsidian wikilink string, e.g. ![[_assets/abc123.png]]
    """
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # Derive a stable filename from the URL
    url_id = re.sub(r"[^a-zA-Z0-9]", "_", src.split("/")[-2] if "/" in src else src)
    url_id = url_id[:40]

    # Try to detect extension from URL or default to png
    ext = "png"
    ext_match = re.search(r"\.(jpg|jpeg|png|gif|webp|bmp|tiff)(\?|$)", src, re.IGNORECASE)
    if ext_match:
        ext = ext_match.group(1).lower()

    img_filename = f"{page_id[:8]}_{url_id}.{ext}"
    img_path = ASSETS_DIR / img_filename

    if img_path.exists():
        return f"![[_assets/{img_filename}]]"

    if SKIP_IMAGES:
        return f"![[_assets/{img_filename}]]"  # placeholder; download later

    try:
        headers = get_auth_headers()
        use_headers = headers if "graph.microsoft.com" in src else {}
        resp = None
        for attempt in range(6):
            resp = requests.get(src, headers=use_headers, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 20 * (attempt + 1)))
                wait = min(wait, 120)
                print(f"      [image rate limited, waiting {wait}s...]")
                time.sleep(wait)
                continue
            break
        resp.raise_for_status()

        # Detect real extension from content-type if possible
        content_type = resp.headers.get("content-type", "")
        if "jpeg" in content_type:
            ext = "jpg"
        elif "png" in content_type:
            ext = "png"
        elif "gif" in content_type:
            ext = "gif"
        elif "webp" in content_type:
            ext = "webp"

        img_filename = f"{page_id[:8]}_{url_id}.{ext}"
        img_path = ASSETS_DIR / img_filename
        img_path.write_bytes(resp.content)
        time.sleep(0.5)
        return f"![[_assets/{img_filename}]]"

    except Exception as e:
        print(f"      [image download failed: {e}]")
        return "[image — download failed]"


def process_images_in_html(html_text: str, page_id: str) -> str:
    """
    Find all <img> tags in HTML, download each image, and replace the tag
    with an Obsidian image wikilink before markdownify runs.
    """
    def replace_img(match):
        tag = match.group(0)
        # Try data-fullres-src first (highest quality in OneNote), then src
        src_match = re.search(r'data-fullres-src="([^"]+)"', tag)
        if not src_match:
            src_match = re.search(r'\bsrc="([^"]+)"', tag)
        if not src_match:
            return "[image]"

        src = src_match.group(1)
        if src.startswith("data:"):
            return "[image — embedded data URI not supported]"

        return download_image(src, page_id)

    return re.sub(r"<img[^>]+>", replace_img, html_text, flags=re.IGNORECASE)


def _is_list_item(line: str) -> bool:
    return bool(re.match(r'^\s*(\d+\.|[-*+])\s', line))


def _tighten_lists(text: str) -> str:
    """Remove blank lines between list items so Obsidian renders tight lists."""
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == '':
            prev_nb = next((lines[j] for j in range(i - 1, -1, -1) if lines[j].strip()), None)
            next_nb = next((lines[j] for j in range(i + 1, len(lines)) if lines[j].strip()), None)
            if prev_nb and _is_list_item(prev_nb) and next_nb and _is_list_item(next_nb):
                i += 1
                continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def _remove_backslash_lines(text: str) -> str:
    """Remove empty list items and standalone lines whose only content is a backslash."""
    lines = text.split('\n')
    result = [
        line for line in lines
        if not re.match(r'^\s*(\d+\.|[-*+]\s)?\s*\\$', line.rstrip())
    ]
    return '\n'.join(result)


def html_to_markdown(html: bytes, page_id: str) -> str:
    """Convert OneNote HTML to clean Markdown, downloading images along the way."""
    text = html.decode("utf-8", errors="replace")

    # Extract body content
    body_match = re.search(r"<body[^>]*>(.*?)</body>", text, re.DOTALL | re.IGNORECASE)
    body = body_match.group(1) if body_match else text

    # Download images and replace <img> tags before conversion
    body = process_images_in_html(body, page_id)

    converted = md(
        body,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
        newline_style="backslash",
    )

    # Remove empty list items and standalone lines whose only content is a backslash
    # (artifacts from OneNote empty list items converted via newline_style="backslash")
    converted = _remove_backslash_lines(converted)

    # Collapse blank lines between list items into tight lists
    converted = _tighten_lists(converted)

    # Clean up excessive blank lines
    converted = re.sub(r"\n{3,}", "\n\n", converted)

    return converted.strip()


def make_frontmatter(title: str, notebook: str, section: str,
                     created: str, modified: str, page_id: str,
                     group=None) -> str:
    path_parts = [notebook]
    if group:
        path_parts.append(group)
    path_parts.append(section)
    location = " > ".join(path_parts)

    lines = [
        "---",
        f'title: "{title}"',
        "type: source",
        f'notebook: "{notebook}"',
        f'section: "{section}"',
        f'location: "{location}"',
        f'onenote_id: "{page_id}"',
    ]
    if created:
        lines.append(f"created: {created[:10]}")
    if modified:
        lines.append(f"modified: {modified[:10]}")
    lines.append("---")
    return "\n".join(lines)


def convert_page(page: dict, notebook_name: str, section_name: str,
                 section_dir: Path, group_name=None):
    page_id = page["id"]
    title = page.get("title") or "Untitled"

    html_path = RAW_DIR / "_pages" / f"{page_id}.html"
    if not html_path.exists():
        print(f"    [missing html] {title}")
        return

    html = html_path.read_bytes()
    body_md = html_to_markdown(html, page_id)

    frontmatter = make_frontmatter(
        title=title,
        notebook=notebook_name,
        section=section_name,
        created=page.get("createdDateTime", ""),
        modified=page.get("lastModifiedDateTime", ""),
        page_id=page_id,
        group=group_name,
    )

    content = f"{frontmatter}\n\n# {title}\n\n{body_md}\n"

    section_dir.mkdir(parents=True, exist_ok=True)
    out_path = section_dir / f"{safe_filename(title)}.md"

    # If file exists and belongs to this page, skip re-conversion
    if out_path.exists() and page_id[:8] in out_path.read_text(encoding="utf-8"):
        return out_path

    # Handle duplicate filenames within a section
    if out_path.exists():
        out_path = section_dir / f"{safe_filename(title)} ({page_id[:8]}).md"

    out_path.write_text(content, encoding="utf-8")
    return out_path


def convert_all(skip_images: bool = False):
    global SKIP_IMAGES
    SKIP_IMAGES = skip_images
    if skip_images:
        print("  (image downloads deferred — run again without --skip-images to fetch them)")
    manifest_path = RAW_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("raw/manifest.json not found — run extract.py first.")

    manifest = json.loads(manifest_path.read_text())
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    total = 0

    for nb in manifest:
        nb_name = nb["name"]
        nb_dir = SOURCES_DIR / safe_filename(nb_name)
        print(f"\nNotebook: {nb_name}")

        for sec in nb.get("sections", []):
            sec_name = sec["name"]
            sec_dir = nb_dir / safe_filename(sec_name)
            print(f"  Section: {sec_name} ({len(sec['pages'])} pages)")
            for page in sec["pages"]:
                path = convert_page(page, nb_name, sec_name, sec_dir)
                if path:
                    total += 1

        for group in nb.get("sectionGroups", []):
            group_name = group["name"]
            group_dir = nb_dir / safe_filename(group_name)
            print(f"  Group: {group_name}")
            for sec in group.get("sections", []):
                sec_name = sec["name"]
                sec_dir = group_dir / safe_filename(sec_name)
                print(f"    Section: {sec_name} ({len(sec['pages'])} pages)")
                for page in sec["pages"]:
                    path = convert_page(page, nb_name, sec_name, sec_dir,
                                        group_name=group_name)
                    if path:
                        total += 1

    print(f"\nConversion complete. {total} Markdown files written to {SOURCES_DIR}")
    img_count = len(list(ASSETS_DIR.glob("*")))
    print(f"Images saved: {img_count} files in {ASSETS_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-images", action="store_true",
                        help="Skip image downloads (use placeholders, download later)")
    args = parser.parse_args()
    convert_all(skip_images=args.skip_images)
