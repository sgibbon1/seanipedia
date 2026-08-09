#!/usr/bin/env python3
"""fetch_onenote_page.py — re-import ONE OneNote page into the vault.

WHY THIS EXISTS
archive/extract.py re-exports every notebook (880+ pages, ~10 min, ~10MB of raw
HTML). When OneNote is the source of truth for a single page you've corrected,
that's wasteful and — worse — a full re-convert would clobber vault files that
have since been repaired locally. This fetches exactly one page and rewrites
exactly one file.

It reuses archive/convert.py's html_to_markdown + frontmatter helpers, so the
output is byte-for-byte in the same style as the rest of the collection.

SAFETY
  - Prints the page's lastModifiedDateTime BEFORE writing, so you can confirm
    the cloud copy actually has your recent edits (a stale cloud copy would
    silently overwrite good local work with old errors).
  - Backs up the existing vault file first.
  - --dry-run (default) shows a diff summary; --apply writes.

Usage:
    python3 fetch_onenote_page.py --title "By Subject"
    python3 fetch_onenote_page.py --title "By Subject" --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import msal
import requests
from dotenv import load_dotenv

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "archive"))
load_dotenv(dotenv_path=HERE / ".env", override=True)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/Notes.Read"]
CACHE_PATH = HERE / "raw" / ".token_cache.bin"
VAULT = Path(os.environ.get("VAULT_PATH", "./vault"))
BACKUP = Path.home() / "scripts" / f"onenote-page-backup-{date.today().isoformat()}"


def get_token() -> str:
    cache = msal.SerializableTokenCache()
    if CACHE_PATH.exists():
        cache.deserialize(CACHE_PATH.read_text())
    app = msal.PublicClientApplication(
        os.environ["AZURE_CLIENT_ID"],
        authority="https://login.microsoftonline.com/consumers",
        token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        raise SystemExit("No cached Microsoft account. Run archive/extract.py once to sign in.")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise SystemExit("Token expired. Run archive/extract.py once to re-authenticate.")
    if cache.has_state_changed:
        CACHE_PATH.write_text(cache.serialize())
    return result["access_token"]


def _get(url: str, headers: dict, tries: int = 12):
    """GET with Graph-aware 429 backoff. A preceding full export leaves the
    OneNote API throttled for a while, and it tells us how long to wait via
    Retry-After — honouring that beats blind retries."""
    delay = 10
    for attempt in range(tries):
        r = requests.get(url, headers=headers, timeout=120)
        if r.status_code not in (429, 500, 502, 503, 504):
            r.raise_for_status()
            return r
        if r.status_code != 429:
            print(f"  server error {r.status_code} — retrying [attempt {attempt+1}/{tries}]")
            import time; time.sleep(delay); delay = min(delay*2, 120); continue
        wait = int(r.headers.get("Retry-After", delay))
        print(f"  throttled (429) — waiting {wait}s "
              f"[attempt {attempt+1}/{tries}]")
        import time; time.sleep(max(wait, delay))
        delay = min(delay * 2, 300)
    raise SystemExit("Still throttled after several retries — wait a few minutes "
                     "and run again (a full export leaves the API rate-limited).")


def id_from_manifest(title: str) -> str | None:
    """Prefer the page id recorded by a previous export: fetching by id costs ONE
    request, whereas searching pages by title pages through the whole notebook
    set — which is exactly what gets us throttled."""
    mf = HERE / "raw" / "manifest.json"
    if not mf.exists():
        return None
    import json
    found = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("title") == title and "id" in o:
                found.append(o["id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)
    walk(json.loads(mf.read_text(encoding="utf-8")))
    return found[0] if found else None


def record_from_manifest(title: str) -> dict | None:
    """Full manifest record (id/title/dates) — costs ZERO API calls. The Graph
    metadata endpoint is the flakiest part of this flow when the API is
    throttled, and the manifest already holds everything we need."""
    mf = HERE / "raw" / "manifest.json"
    if not mf.exists():
        return None
    import json
    found = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("title") == title and "id" in o and "sections" not in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)
    walk(json.loads(mf.read_text(encoding="utf-8")))
    return found[0] if found else None


def find_page(title: str, headers: dict) -> dict:
    """Resolve the page — from the local manifest when possible, else by search."""
    rec = record_from_manifest(title)
    if rec:
        rec.setdefault("lastModifiedDateTime", "")
        rec.setdefault("createdDateTime", "")
        print("  (metadata from local manifest — no API call)")
        return rec
    url = (f"{GRAPH}/me/onenote/pages?$select=id,title,lastModifiedDateTime,"
           f"createdDateTime,parentSection&$orderby=lastModifiedDateTime desc&$top=100")
    hits = []
    while url:
        data = _get(url, headers).json()
        hits += [p for p in data.get("value", []) if p.get("title") == title]
        url = data.get("@odata.nextLink") if not hits else None
    if not hits:
        raise SystemExit(f"No OneNote page titled {title!r} found.")
    return hits[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="exact page title, e.g. 'By Subject'")
    ap.add_argument("--apply", action="store_true", help="write the vault file")
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    ap.add_argument("--out", help="also write the converted markdown here for inspection")
    args = ap.parse_args()

    headers = {"Authorization": f"Bearer {get_token()}"}
    page = find_page(args.title, headers)
    section = (page.get("parentSection") or {}).get("displayName", "")
    print(f"Found: {page['title']}  (section: {section})")
    print(f"  page id      : {page['id']}")
    print(f"  last modified: {page['lastModifiedDateTime']}   <-- confirm this reflects your edits")

    # Cache the raw HTML: the OneNote API throttles hard after a full export, so
    # re-fetching just to re-inspect the same page is expensive and needless.
    cache_file = HERE / "raw" / "_pages" / f"{page['id'].replace('/', '_')}.html"
    if cache_file.exists() and not args.refresh:
        html = cache_file.read_bytes()
        print(f"  using cached download ({len(html):,} bytes) — pass --refresh to re-fetch")
    else:
        html = _get(f"{GRAPH}/me/onenote/pages/{page['id']}/content", headers).content
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(html)
    print(f"  downloaded   : {len(html):,} bytes")

    import convert  # archive/convert.py — same converter as the original import
    body = convert.html_to_markdown(html, page["id"])
    fm = convert.make_frontmatter(page["title"], "Τά εἰς ἑαυτόν", section or "Quotes",
                                  page["id"], page.get("createdDateTime", "")[:10],
                                  page["lastModifiedDateTime"][:10])
    new_text = fm + body

    target = VAULT / "_sources" / "Τά εἰς ἑαυτόν" / (section or "Quotes") / f"{page['title']}.md"
    if target.exists():
        old = target.read_text(encoding="utf-8")
        print(f"\n  vault file   : {target.name}")
        print(f"  current      : {len(old.splitlines())} lines")
        print(f"  incoming     : {len(new_text.splitlines())} lines")
        for probe in ("Gerontion", "Morrison", "Chant of Love"):
            print(f"    {probe:<15} current={old.count(probe)}  incoming={body.count(probe)}")
    else:
        print(f"\n  vault file   : {target} (new)")

    if args.out:
        pathlib_out = Path(args.out); pathlib_out.write_text(new_text, encoding="utf-8")
        print(f"  converted markdown written for inspection -> {pathlib_out}")

    if not args.apply:
        print("\n[DRY RUN] nothing written. Re-run with --apply to replace the vault file.")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy(target, BACKUP / target.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_text, encoding="utf-8")
    print(f"\nWrote {target}\nBackup: {BACKUP / target.name}")
    print("Now re-run: python3 quotes_index.py")


if __name__ == "__main__":
    main()
