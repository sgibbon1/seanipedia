"""
extract.py — Download all OneNote content via Microsoft Graph API.

Uses device code OAuth via the msal library. Run this first.
Output: raw/_pages/<page_id>.json  (metadata)
        raw/_pages/<page_id>.html  (page content)
        raw/manifest.json          (full notebook/section/page tree)
"""

import json
import os
import time
from pathlib import Path

import msal
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
SCOPES = ["https://graph.microsoft.com/Notes.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"

RAW_DIR = Path("raw")
PAGES_DIR = RAW_DIR / "_pages"
TOKEN_CACHE_PATH = RAW_DIR / ".token_cache.bin"

# Module-level app/cache so token can be refreshed anywhere
_app = None
_cache = None


def _init_app():
    global _app, _cache
    _cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        _cache.deserialize(TOKEN_CACHE_PATH.read_text())
    _app = msal.PublicClientApplication(
        CLIENT_ID,
        authority="https://login.microsoftonline.com/consumers",
        token_cache=_cache,
    )


def _save_cache():
    if _cache and _cache.has_state_changed:
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE_PATH.write_text(_cache.serialize())


def get_fresh_token() -> str:
    """Return a valid access token, refreshing silently or re-prompting if needed."""
    global _app, _cache
    if _app is None:
        _init_app()

    accounts = _app.get_accounts()
    if accounts:
        result = _app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache()
            return result["access_token"]

    # Need interactive login
    flow = _app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start device flow: {flow}")

    print("\n" + "=" * 60)
    print("ACTION REQUIRED: Authenticate with Microsoft")
    print(f"\n  1. Open this URL: {flow['verification_uri']}")
    print(f"  2. Enter code:    {flow['user_code']}")
    print("=" * 60 + "\n")
    print("Waiting for you to authenticate...")

    result = _app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")

    _save_cache()
    print("Authenticated successfully.\n")
    return result["access_token"]


def make_headers() -> dict:
    return {"Authorization": f"Bearer {get_fresh_token()}"}


def _get_with_retry(url: str, headers: dict, max_retries: int = 6) -> requests.Response:
    """GET with timeout, token refresh on 401, and backoff on 429/503."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.exceptions.Timeout:
            wait = 2 ** attempt
            print(f"      [timeout, retry {attempt+1}/{max_retries} after {wait}s]")
            time.sleep(wait)
            continue

        if resp.status_code == 401:
            print(f"      [token expired, refreshing...]")
            headers["Authorization"] = f"Bearer {get_fresh_token()}"
            continue

        if resp.status_code in (429, 502, 503):
            # Respect Retry-After header if present; otherwise use a longer backoff
            # for 429s since OneNote's rate limit needs more recovery time
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 20 * (attempt + 1)))
            else:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            retry_after = min(retry_after, 120)
            print(f"      [retry {attempt+1}/{max_retries} after {retry_after}s — {resp.status_code}]")
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        return resp

    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


def graph_get(url: str, headers: dict) -> list:
    """GET a Graph API URL, following @odata.nextLink pagination."""
    results = []
    while url:
        resp = _get_with_retry(url, headers)
        data = resp.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return results


def download_page_content(page_id: str, headers: dict) -> bytes:
    url = f"{GRAPH}/me/onenote/pages/{page_id}/content"
    resp = _get_with_retry(url, headers)
    return resp.content


def extract_all():
    RAW_DIR.mkdir(exist_ok=True)
    PAGES_DIR.mkdir(exist_ok=True)

    headers = make_headers()

    print("Fetching notebooks...")
    notebooks = graph_get(f"{GRAPH}/me/onenote/notebooks", headers)
    print(f"  Found {len(notebooks)} notebooks")

    manifest = []

    for nb in notebooks:
        nb_id = nb["id"]
        nb_name = nb["displayName"]
        print(f"\nNotebook: {nb_name}")

        # Refresh token at the start of each notebook
        headers = make_headers()

        nb_entry = {
            "id": nb_id,
            "name": nb_name,
            "createdDateTime": nb.get("createdDateTime"),
            "lastModifiedDateTime": nb.get("lastModifiedDateTime"),
            "sections": [],
            "sectionGroups": [],
        }

        sections = graph_get(f"{GRAPH}/me/onenote/notebooks/{nb_id}/sections", headers)
        for sec in sections:
            nb_entry["sections"].append(_process_section(sec, headers, nb_name))

        groups = graph_get(f"{GRAPH}/me/onenote/notebooks/{nb_id}/sectionGroups", headers)
        for group in groups:
            nb_entry["sectionGroups"].append(_process_section_group(group, headers, nb_name))

        manifest.append(nb_entry)

    manifest_path = RAW_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nManifest saved to {manifest_path}")

    total = sum(len(sec["pages"]) for nb in manifest for sec in nb["sections"]) + \
            sum(len(sec["pages"]) for nb in manifest for g in nb["sectionGroups"] for sec in g["sections"])
    print(f"Extraction complete. {total} pages downloaded.")


def _process_section(sec: dict, headers: dict, notebook_name: str) -> dict:
    sec_id = sec["id"]
    sec_name = sec["displayName"]
    print(f"  Section: {sec_name}")

    pages = graph_get(f"{GRAPH}/me/onenote/sections/{sec_id}/pages", headers)
    page_entries = []

    for page in pages:
        page_id = page["id"]
        html_path = PAGES_DIR / f"{page_id}.html"
        meta_path = PAGES_DIR / f"{page_id}.json"

        if html_path.exists() and meta_path.exists():
            print(f"    [skip] {page['title']}")
        else:
            print(f"    Downloading: {page['title']}")
            try:
                html = download_page_content(page_id, headers)
                html_path.write_bytes(html)
                meta_path.write_text(json.dumps(page, indent=2, ensure_ascii=False))
                time.sleep(1.0)  # more breathing room between pages
            except Exception as e:
                print(f"    ERROR downloading {page['title']}: {e}")
                continue

        page_entries.append({
            "id": page_id,
            "title": page.get("title", "Untitled"),
            "createdDateTime": page.get("createdDateTime"),
            "lastModifiedDateTime": page.get("lastModifiedDateTime"),
        })

    return {"id": sec_id, "name": sec_name, "pages": page_entries}


def _process_section_group(group: dict, headers: dict, notebook_name: str) -> dict:
    group_id = group["id"]
    group_name = group["displayName"]
    print(f"  Section Group: {group_name}")

    sections = graph_get(f"{GRAPH}/me/onenote/sectionGroups/{group_id}/sections", headers)
    return {
        "id": group_id,
        "name": group_name,
        "sections": [_process_section(s, headers, notebook_name) for s in sections],
    }


if __name__ == "__main__":
    extract_all()
