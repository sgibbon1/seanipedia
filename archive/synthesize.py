"""
synthesize.py — Use Claude to generate topic pages from source notes.

Reads:  <vault>/_sources/**/*.md
Writes: <vault>/_topics/<Topic Name>.md
        <vault>/_reflections/<Theme>.md
        <vault>/_cache/key_points.json
        <vault>/_cache/topics.json
        <vault>/_cache/reflection_topics.json

Process:
  1. Load source pages, filtering to INCLUDE_NOTEBOOKS from .env
     Pages from Τά εἰς ἑαυτόν are tagged type=reflection automatically.
  2. Extract key points (cached by body hash)
  3. Identify topics (multi-pass, cached)
  4. Generate topic pages — external sources get encyclopedic prose;
     if reflection pages also touch the topic, a ## Personal Reflections
     section is appended with Sean's own voice preserved.
  5. Generate _reflections/ pages — thematic synthesis of Τά εἰς ἑαυτόν
     content that stands on its own, written in first-person register.
  6. Generate Quotes & Wisdom page (themed anthology)

Flags:
  --force-keypoints   Re-extract key points (ignore cache)
  --force-topics      Re-identify topics
  --force-pages       Regenerate all output pages
  --force             All of the above
"""

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
SOURCES_DIR = VAULT_PATH / "_sources"
TOPICS_DIR = VAULT_PATH / "_topics"
REFLECTIONS_DIR = VAULT_PATH / "_reflections"
CACHE_DIR = VAULT_PATH / "_cache"
KEY_POINTS_CACHE = CACHE_DIR / "key_points.json"
TOPICS_CACHE = CACHE_DIR / "topics.json"
REFLECTION_TOPICS_CACHE = CACHE_DIR / "reflection_topics.json"

REFLECTION_NOTEBOOK = "Τά εἰς ἑαυτόν"

_raw_include = os.environ.get("INCLUDE_NOTEBOOKS", "").strip()
INCLUDE_NOTEBOOKS = [n.strip() for n in _raw_include.split(",") if n.strip()]

# ---------------------------------------------------------------------------
# AI provider setup
# AI_PROVIDER in .env: "anthropic" (default) or "gemini"
#
# Anthropic setup:
#   ANTHROPIC_API_KEY=sk-ant-...   (from console.anthropic.com)
#
# Gemini setup:
#   1. Go to aistudio.google.com and sign in with your Google account
#   2. Click "Get API key" → "Create API key"
#   3. Add to .env:  GEMINI_API_KEY=AIza...
#   4. pip3 install google-generativeai
#   5. Set AI_PROVIDER=gemini in .env
#   Free tier: 1,500 requests/day, 1M tokens/min — more than enough for this use case
# ---------------------------------------------------------------------------

AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").strip().lower()

if AI_PROVIDER == "gemini":
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-pro")
    _gemini_model = genai.GenerativeModel(MODEL)
else:
    import anthropic as _anthropic
    _anthropic_client = _anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SHORT_PAGE_WORDS = 150
BATCH_LIMIT = 8000
CORPUS_CHUNK_SIZE = 55000


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

class _Response:
    """Unified response wrapper so callers don't need to know the provider."""
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


def api_call_with_retry(model, max_tokens, system, messages, max_retries=6):
    for attempt in range(max_retries):
        try:
            if AI_PROVIDER == "gemini":
                # Gemini takes a single string prompt; prepend system as context
                prompt = f"{system}\n\n" + "\n\n".join(
                    m["content"] for m in messages if m["role"] == "user"
                )
                result = _gemini_model.generate_content(
                    prompt,
                    generation_config={"max_output_tokens": max_tokens},
                )
                return _Response(result.text)
            else:
                return _anthropic_client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
        except Exception as e:
            err = str(e)
            # Rate limit handling for both providers
            is_rate_limit = (
                "rate" in err.lower() or "429" in err or "quota" in err.lower()
                or (AI_PROVIDER == "anthropic" and
                    hasattr(e, "__class__") and
                    "RateLimitError" in type(e).__name__)
            )
            if is_rate_limit and attempt < max_retries - 1:
                wait = 30 * (attempt + 1)
                print(f"\n  [rate limited, waiting {wait}s before retry {attempt+1}/{max_retries}]")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Exceeded max retries on rate limit")


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

TODO_SIGNALS = [
    r"\btodo\b", r"\bto.do\b", r"\bto-do\b", r"\bchecklist\b",
    r"\baction items?\b", r"\btask list\b", r"\bnext steps?\b",
    r"^\s*[-*]\s+\[[ x]\]",
    r"^\s*\d+\.\s+(?:call|email|send|buy|get|schedule|follow.?up|remind)",
]
TODO_RE = re.compile("|".join(TODO_SIGNALS), re.IGNORECASE | re.MULTILINE)


def looks_like_todo(body: str, title: str) -> bool:
    combined = (title + "\n" + body).lower()
    matches = len(TODO_RE.findall(combined))
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return False
    list_lines = sum(1 for l in lines if re.match(r"^[-*•]|^\d+\.", l))
    return matches >= 3 or (len(lines) and list_lines / len(lines) > 0.7 and matches >= 1)


QUOTE_SIGNALS = [
    r'^>',
    r'[""\u201c\u201d].{10,}[""\u201c\u201d]',
    r'[—–-]\s*[A-Z][a-z]',
    r'\*[^*]+\*\s*[—–-]',
]
QUOTE_RE = re.compile("|".join(QUOTE_SIGNALS), re.MULTILINE)


def quote_density(body: str) -> float:
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return 0.0
    return sum(1 for l in lines if QUOTE_RE.search(l)) / len(lines)


def looks_like_quotes(body: str, title: str) -> bool:
    title_hints = re.search(r'\bquote|wisdom|saying|aphorism|maxim|adage\b',
                            title, re.IGNORECASE)
    return quote_density(body) > 0.35 or bool(title_hints)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def body_hash(body: str) -> str:
    return hashlib.md5(body.encode("utf-8")).hexdigest()[:16]


def load_key_points_cache() -> dict:
    if KEY_POINTS_CACHE.exists():
        try:
            return json.loads(KEY_POINTS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_key_points_cache(cache: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    KEY_POINTS_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_json_cache(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_json_cache(path: Path, data):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def notebook_name(location: str) -> str:
    return location.split(">")[0].strip()


def load_sources() -> list:
    pages = []
    for path in sorted(SOURCES_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm = {}
        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip().strip('"')
        body = re.sub(r"^---\n.*?\n---\n*", "", text, flags=re.DOTALL).strip()
        rel = path.relative_to(VAULT_PATH)
        wikilink = str(rel.with_suffix("")).replace("\\", "/")

        page_type = fm.get("type", "source")
        title = fm.get("title", path.stem)
        location = fm.get("location", "")
        nb = notebook_name(location)

        # Tag pages from the reflections notebook
        if page_type == "source" and nb == REFLECTION_NOTEBOOK:
            page_type = "reflection"
            updated = text.replace("type: source", "type: reflection", 1)
            path.write_text(updated, encoding="utf-8")
        elif page_type == "source" and looks_like_todo(body, title):
            page_type = "todo"
            updated = text.replace("type: source", "type: todo", 1)
            path.write_text(updated, encoding="utf-8")

        pages.append({
            "path": str(path),
            "wikilink": wikilink,
            "title": title,
            "location": location,
            "notebook": nb,
            "created": fm.get("created", ""),
            "body": body,
            "word_count": len(body.split()),
            "type": page_type,
        })
    return pages


# ---------------------------------------------------------------------------
# Key point extraction (cached)
# ---------------------------------------------------------------------------

def extract_key_points(pages: list, force: bool = False) -> list:
    print(f"Extracting key points from {len(pages)} pages...")
    kp_cache = {} if force else load_key_points_cache()
    cache_hits = 0
    enriched = []
    to_summarize = []

    for page in pages:
        h = body_hash(page["body"])
        cache_key = page["wikilink"]

        if page["word_count"] <= SHORT_PAGE_WORDS:
            page["key_points"] = page["body"]
            kp_cache[cache_key] = {"hash": h, "key_points": page["body"]}
            enriched.append(page)
        elif not force and cache_key in kp_cache and kp_cache[cache_key].get("hash") == h:
            page["key_points"] = kp_cache[cache_key]["key_points"]
            enriched.append(page)
            cache_hits += 1
        else:
            to_summarize.append((page, h))

    print(f"  {cache_hits} pages loaded from cache, "
          f"{len(pages) - len(to_summarize) - cache_hits} short pages kept as-is, "
          f"{len(to_summarize)} pages to summarize via Claude...")

    batch = []
    batch_words = 0

    def flush_batch(b):
        if not b:
            return
        prompt_parts = [
            f"=== PAGE: {p['title']} | {p['location']} ===\n{p['body']}"
            for p, _ in b
        ]
        system = (
            "You are extracting key points from personal notes. "
            "For each page, output ONLY a bullet list of the most important "
            "distinct ideas, facts, insights, or claims. "
            "Scale bullets to page length (short: 1-3; long: up to 15). "
            "Be faithful — do not add interpretation. "
            "Format: === PAGE: <title> ===\n- point\n\n=== PAGE: ..."
        )
        resp = api_call_with_retry(
            model=MODEL, max_tokens=4096, system=system,
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
        )
        output = resp.content[0].text
        time.sleep(2)
        for p, h in b:
            m = re.search(
                rf"=== PAGE: {re.escape(p['title'])}[^=]*===\n(.*?)(?===|$)",
                output, re.DOTALL,
            )
            p["key_points"] = m.group(1).strip() if m else p["body"][:500]
            kp_cache[p["wikilink"]] = {"hash": h, "key_points": p["key_points"]}
            enriched.append(p)
        save_key_points_cache(kp_cache)

    for page, h in to_summarize:
        batch.append((page, h))
        batch_words += page["word_count"]
        if batch_words >= BATCH_LIMIT:
            flush_batch(batch)
            batch = []
            batch_words = 0
    flush_batch(batch)
    save_key_points_cache(kp_cache)

    print(f"  Done. {len(enriched)} pages with key points.")
    return enriched


# ---------------------------------------------------------------------------
# Topic identification (multi-pass, cached)
# ---------------------------------------------------------------------------

def _parse_topics_json(raw: str) -> list:
    raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("  [warning] JSON incomplete — extracting partial objects...")
        pat = re.compile(
            r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"description"\s*:\s*"[^"]+"\s*,'
            r'\s*"keywords"\s*:\s*\[[^\]]+\]\s*\}',
            re.DOTALL,
        )
        matches = pat.findall(raw)
        if not matches:
            raise RuntimeError(
                "Could not extract valid topic objects.\nResponse (first 500 chars):\n" + raw[:500]
            )
        print(f"  [recovered {len(matches)} objects]")
        return [json.loads(m) for m in matches]


def _identify_topics_chunk(summaries_chunk: list, target: int, section_hints: list) -> list:
    corpus = "\n\n---\n\n".join(summaries_chunk)
    hints_text = (
        "\n\nFor reference, the section titles in this knowledge base include:\n"
        + ", ".join(section_hints[:80])
    ) if section_hints else ""

    system = (
        "You are analyzing a personal knowledge base of notes spanning school, work, "
        "reading, and personal reflection. Identify the major recurring topics or themes "
        "that would make meaningful wiki pages. "
        f"Aim for approximately {target} topics — specific enough to be useful, "
        "broad enough to draw from multiple sources. "
        "Return a JSON array with keys: "
        '"name" (topic title), "description" (1 sentence), "keywords" (3-6 strings). '
        "Return ONLY valid JSON. Do not truncate."
    )
    resp = api_call_with_retry(
        model=MODEL, max_tokens=8192, system=system,
        messages=[{"role": "user", "content": corpus + hints_text}],
    )
    time.sleep(2)
    return _parse_topics_json(resp.content[0].text)


def _merge_topics(all_candidates: list, target: int) -> list:
    print(f"  Merging {len(all_candidates)} candidates into ~{target}...")
    system = (
        "Consolidate this list of topic objects: merge overlapping topics, "
        "keep the best name/description/keywords, remove true duplicates. "
        f"Return ~{target} distinct topics as a valid JSON array. "
        'Keys: "name", "description", "keywords". Do not truncate.'
    )
    resp = api_call_with_retry(
        model=MODEL, max_tokens=8192, system=system,
        messages=[{"role": "user", "content": json.dumps(all_candidates, ensure_ascii=False)}],
    )
    time.sleep(2)
    return _parse_topics_json(resp.content[0].text)


def identify_topics(pages: list, force: bool = False) -> list:
    if not force and TOPICS_CACHE.exists():
        try:
            topics = load_json_cache(TOPICS_CACHE)
            if topics:
                print(f"  Loaded {len(topics)} topics from cache (--force-topics to redo).")
                return topics
        except Exception:
            pass

    print("Identifying topics...")
    section_hints = sorted({p["location"] for p in pages if p["location"]})
    target = max(50, int(len(section_hints) * 1.2))
    print(f"  {len(section_hints)} unique sections → targeting ~{target} topics")

    summaries = [
        f"[{p['title']} | {p['location']}]\n{(p['key_points'] or '')[:200]}"
        for p in pages
    ]

    chunks = []
    current_chunk, current_len = [], 0
    for s in summaries:
        if current_len + len(s) > CORPUS_CHUNK_SIZE and current_chunk:
            chunks.append(current_chunk)
            current_chunk, current_len = [], 0
        current_chunk.append(s)
        current_len += len(s)
    if current_chunk:
        chunks.append(current_chunk)

    print(f"  Corpus split into {len(chunks)} chunk(s)")

    CANDIDATES_CACHE = CACHE_DIR / "topic_candidates.json"

    if len(chunks) == 1:
        topics = _identify_topics_chunk(chunks[0], target, section_hints)
    else:
        per_chunk_target = max(20, target // len(chunks) + 5)

        # Resume from saved candidates if available (in case merge step crashed)
        if not force and CANDIDATES_CACHE.exists():
            try:
                all_candidates = json.loads(CANDIDATES_CACHE.read_text(encoding="utf-8"))
                print(f"  Resuming from {len(all_candidates)} saved candidates (chunk passes already done).")
            except Exception:
                all_candidates = []
        else:
            all_candidates = []

        if not all_candidates:
            for i, chunk in enumerate(chunks):
                print(f"  Pass {i+1}/{len(chunks)} ({len(chunk)} pages)...")
                all_candidates.extend(_identify_topics_chunk(chunk, per_chunk_target, section_hints))
                # Save after each chunk so a crash doesn't lose progress
                save_json_cache(CANDIDATES_CACHE, all_candidates)

        topics = _merge_topics(all_candidates, target)
        # Clean up candidates cache once merge succeeds
        CANDIDATES_CACHE.unlink(missing_ok=True)

    save_json_cache(TOPICS_CACHE, topics)
    print(f"  Identified {len(topics)} topics: "
          + ", ".join(t["name"] for t in topics[:8])
          + ("..." if len(topics) > 8 else ""))
    return topics


# ---------------------------------------------------------------------------
# Topic page generation (with Personal Reflections section)
# ---------------------------------------------------------------------------

def find_relevant_pages(topic: dict, pages: list) -> list:
    keywords = [k.lower() for k in topic.get("keywords", [])]
    topic_name = topic["name"].lower()
    relevant = []
    for p in pages:
        text = (p.get("key_points", "") + " " + p["title"] + " " + p["location"]).lower()
        if any(kw in text for kw in keywords) or topic_name in text:
            relevant.append(p)
    return relevant


def generate_topic_page(topic: dict, relevant_pages: list) -> str:
    if not relevant_pages:
        return ""

    external = [p for p in relevant_pages if p["type"] != "reflection"]
    reflections = [p for p in relevant_pages if p["type"] == "reflection"]

    # Build external source context
    ext_context_parts = []
    for i, p in enumerate(external):
        ext_context_parts.append(
            f"SOURCE [{i+1}]: {p['title']}\n"
            f"Location: {p['location']}\n"
            f"Wikilink: [[{p['wikilink']}]]\n"
            f"Key points:\n{p['key_points']}"
        )

    # Build reflection context
    ref_context_parts = []
    for i, p in enumerate(reflections):
        ref_context_parts.append(
            f"REFLECTION [{i+1}]: {p['title']}\n"
            f"Wikilink: [[{p['wikilink']}]]\n"
            f"{p['body'][:1500]}"
        )

    has_external = bool(external)
    has_reflections = bool(reflections)

    # --- Encyclopedic body from external sources ---
    if has_external:
        ext_system = (
            "You are writing a personal wiki page in the style of Wikipedia. "
            "Rules:\n"
            "- Clear, neutral prose with Markdown headings (##, ###).\n"
            "- Pull specific insights from sources, not vague generalities.\n"
            "- Inline footnote citations [^1], [^2] after each claim.\n"
            "- Short topics (1-2 points): one paragraph is fine — don't pad.\n"
            "- End with '## Sources':\n"
            "  [^1]: [[wikilink]] — *location*, date\n"
            "- Do NOT add an H1 title. Return ONLY Markdown."
        )
        ext_prompt = (
            f"Topic: {topic['name']}\n"
            f"Description: {topic['description']}\n\n"
            f"Sources:\n\n" + "\n\n---\n\n".join(ext_context_parts)
        )
        resp = api_call_with_retry(
            model=MODEL, max_tokens=4096, system=ext_system,
            messages=[{"role": "user", "content": ext_prompt}],
        )
        ext_body = resp.content[0].text.strip()
        time.sleep(2)
    else:
        ext_body = ""

    # --- Personal Reflections section from Τά εἰς ἑαυτόν ---
    if has_reflections:
        ref_system = (
            "You are synthesizing someone's personal reflections and private notes on a topic. "
            "Write a '## Personal Reflections' section that:\n"
            "- Preserves the author's own voice and phrasing as closely as possible.\n"
            "- Uses first-person register (e.g. 'I have found...', 'It strikes me...').\n"
            "- Groups related thoughts under ### sub-headings if there are several themes.\n"
            "- Uses inline footnote citations [^R1], [^R2] after each thought.\n"
            "- Ends with '### Sources' listing the reflection wikilinks.\n"
            "- Do NOT be encyclopedic — this is a personal voice section.\n"
            "- Return ONLY the Markdown section, starting with '## Personal Reflections'."
        )
        ref_prompt = (
            f"Topic: {topic['name']}\n\n"
            "Personal reflection notes:\n\n" + "\n\n---\n\n".join(ref_context_parts)
        )
        resp = api_call_with_retry(
            model=MODEL, max_tokens=2048, system=ref_system,
            messages=[{"role": "user", "content": ref_prompt}],
        )
        ref_body = resp.content[0].text.strip()
        time.sleep(2)
    else:
        ref_body = ""

    # Assemble final page
    body_parts = []
    if ext_body:
        body_parts.append(ext_body)
    if ref_body:
        body_parts.append(ref_body)
    full_body = "\n\n".join(body_parts)

    frontmatter = "\n".join([
        "---",
        f'title: "{topic["name"]}"',
        "type: topic",
        f'description: "{topic["description"]}"',
        f"source_count: {len(external)}",
        f"reflection_count: {len(reflections)}",
        "---",
    ])
    return f"{frontmatter}\n\n# {topic['name']}\n\n{full_body}\n"


# ---------------------------------------------------------------------------
# Reflections pages (_reflections/ folder)
# ---------------------------------------------------------------------------

def generate_reflections_pages(reflection_pages: list, force: bool = False) -> None:
    """Synthesize thematic pages from Τά εἰς ἑαυτόν into _reflections/."""
    if not reflection_pages:
        print("  No reflection pages found — skipping _reflections/ generation.")
        return

    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nGenerating reflections pages from {len(reflection_pages)} reflection notes...")

    # Identify themes within the reflection corpus
    cached_themes = [] if force else load_json_cache(REFLECTION_TOPICS_CACHE)
    if cached_themes:
        print(f"  Loaded {len(cached_themes)} reflection themes from cache (--force-topics to redo).")
        themes = cached_themes
    else:
        summaries = [
            f"[{p['title']}]\n{(p.get('key_points') or p['body'])[:200]}"
            for p in reflection_pages
        ]
        corpus = "\n\n---\n\n".join(summaries)
        if len(corpus) > 40000:
            corpus = corpus[:40000] + "\n\n[... truncated ...]"

        theme_system = (
            "You are analyzing someone's private personal reflections and journal-like notes. "
            "Identify 5-15 meaningful themes that recur across these notes — "
            "themes that reflect genuine personal preoccupations, not just academic topics. "
            "Examples: 'On Faith and Doubt', 'On Ambition and Rest', 'On Loss', "
            "'On What I Owe Others'. "
            "Return a JSON array with keys: "
            '"name" (theme title starting with On...), "description" (1 sentence), '
            '"keywords" (3-5 strings). Return ONLY valid JSON.'
        )
        resp = api_call_with_retry(
            model=MODEL, max_tokens=4096, system=theme_system,
            messages=[{"role": "user", "content": corpus}],
        )
        time.sleep(2)
        themes = _parse_topics_json(resp.content[0].text)
        save_json_cache(REFLECTION_TOPICS_CACHE, themes)
        print(f"  Identified {len(themes)} reflection themes: "
              + ", ".join(t["name"] for t in themes[:5])
              + ("..." if len(themes) > 5 else ""))

    generated = 0
    skipped = 0
    already_existed = 0

    for theme in themes:
        safe_name = re.sub(r'[\\/:*?"<>|]', "-", theme["name"]).strip()
        out_path = REFLECTIONS_DIR / f"{safe_name}.md"

        if out_path.exists() and not force:
            already_existed += 1
            continue

        relevant = find_relevant_pages(theme, reflection_pages)
        if len(relevant) < 2:
            skipped += 1
            continue

        print(f"  Generating reflection: '{theme['name']}' ({len(relevant)} notes)...")

        context_parts = []
        for i, p in enumerate(relevant):
            context_parts.append(
                f"NOTE [{i+1}]: {p['title']}\n"
                f"Wikilink: [[{p['wikilink']}]]\n\n"
                f"{p['body'][:2000]}"
            )
        context = "\n\n---\n\n".join(context_parts)

        ref_page_system = (
            "You are writing a personal reflection page for someone's private wiki. "
            "This page collects their own thoughts, not external facts. "
            "Rules:\n"
            "- Write in first-person register, preserving the author's voice.\n"
            "- Use Markdown headings (##, ###) to group sub-themes.\n"
            "- Quote or closely paraphrase the source notes; don't generalize away specifics.\n"
            "- Use inline citations [^1], [^2] after each thought drawn from a note.\n"
            "- End with '## Sources' listing [[wikilinks]].\n"
            "- Do NOT add an H1 title. Return ONLY Markdown."
        )
        user_prompt = (
            f"Theme: {theme['name']}\n"
            f"Description: {theme['description']}\n\n"
            f"Notes:\n\n{context}"
        )
        resp = api_call_with_retry(
            model=MODEL, max_tokens=4096, system=ref_page_system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        time.sleep(2)
        body = resp.content[0].text.strip()

        frontmatter = "\n".join([
            "---",
            f'title: "{theme["name"]}"',
            "type: reflection",
            f'description: "{theme["description"]}"',
            f"source_count: {len(relevant)}",
            "---",
        ])
        out_path.write_text(f"{frontmatter}\n\n# {theme['name']}\n\n{body}\n", encoding="utf-8")
        generated += 1

    print(f"  Reflections: {generated} pages written, "
          f"{already_existed} already existed, {skipped} skipped.")
    print(f"  Reflection pages are in: {REFLECTIONS_DIR}")


# ---------------------------------------------------------------------------
# Quotes & Wisdom page
# ---------------------------------------------------------------------------

def generate_quotes_page(pages: list, force: bool = False) -> None:
    out_path = TOPICS_DIR / "Quotes & Wisdom.md"
    if out_path.exists() and not force:
        print("  [skip] 'Quotes & Wisdom' already exists (--force-pages to regenerate)")
        return

    quote_pages = [p for p in pages if looks_like_quotes(p["body"], p["title"])]
    print(f"  Generating: 'Quotes & Wisdom' ({len(quote_pages)} quote-heavy pages)...")
    if not quote_pages:
        print("  [skip] No quote-heavy pages detected.")
        return

    pages_context = [
        f"SOURCE [{i+1}]: {p['title']}\nLocation: {p['location']}\n"
        f"Wikilink: [[{p['wikilink']}]]\n\n{p['body'][:2000]}"
        for i, p in enumerate(quote_pages)
    ]

    chunks = []
    current, current_len = [], 0
    for entry in pages_context:
        if current_len + len(entry) > 60000 and current:
            chunks.append(current)
            current, current_len = [], 0
        current.append(entry)
        current_len += len(entry)
    if current:
        chunks.append(current)

    system = (
        "You are compiling a personal anthology of quotes, aphorisms, and wisdom. "
        "Organize into thematic sub-sections (## On Courage, ## On Power, etc.). "
        "Format each quote as:\n> \"Quote text.\" — *Author* [^N]\n\n"
        "Collect as many distinct quotes as possible. "
        "End with '## Sources' as [[wikilinks]]. No H1 title. Return ONLY Markdown."
    )

    all_bodies = []
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            print(f"    Quotes pass {i+1}/{len(chunks)}...")
        resp = api_call_with_retry(
            model=MODEL, max_tokens=8192, system=system,
            messages=[{"role": "user", "content": "\n\n---\n\n".join(chunk)}],
        )
        all_bodies.append(resp.content[0].text.strip())
        time.sleep(2)

    if len(all_bodies) > 1:
        print("    Merging quote chunks...")
        merge_system = (
            "Merge these partial Quotes & Wisdom pages into one: consolidate themes, "
            "deduplicate quotes, keep > \"Quote\" — *Author* [^N] format. "
            "Return ONLY merged Markdown, no H1 title."
        )
        resp = api_call_with_retry(
            model=MODEL, max_tokens=8192, system=merge_system,
            messages=[{"role": "user", "content": "\n\n---NEXT BATCH---\n\n".join(all_bodies)}],
        )
        final_body = resp.content[0].text.strip()
        time.sleep(2)
    else:
        final_body = all_bodies[0]

    frontmatter = "\n".join([
        "---",
        'title: "Quotes & Wisdom"',
        "type: topic",
        'description: "An anthology of quotes, aphorisms, and wisdom from a lifetime of reading."',
        f"source_count: {len(quote_pages)}",
        "---",
    ])
    out_path.write_text(f"{frontmatter}\n\n# Quotes & Wisdom\n\n{final_body}\n", encoding="utf-8")
    print(f"    Saved: {out_path}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def synthesize_all(force_keypoints=False, force_topics=False, force_pages=False,
                   review_topics=False):
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)

    all_pages = load_sources()
    if not all_pages:
        raise RuntimeError(f"No source pages found in {SOURCES_DIR}. Run convert.py first.")

    # Filter to included notebooks
    if INCLUDE_NOTEBOOKS:
        filtered = [p for p in all_pages if p["notebook"] in INCLUDE_NOTEBOOKS]
        excluded_nbs = set(p["notebook"] for p in all_pages) - set(INCLUDE_NOTEBOOKS)
        print(f"Notebook filter: {len(filtered)} pages kept, "
              f"{len(all_pages) - len(filtered)} excluded "
              f"({', '.join(sorted(excluded_nbs))}).")
        all_pages = filtered

    # Separate reflection pages before filtering for synthesis
    reflection_pages = [p for p in all_pages if p["type"] == "reflection"]
    todo_count = sum(1 for p in all_pages if p["type"] == "todo")
    synthesis_pages = [p for p in all_pages if p["type"] not in ("todo", "journal")]

    print(f"Loaded {len(all_pages)} pages: "
          f"{len(synthesis_pages)} for synthesis "
          f"(incl. {len(reflection_pages)} reflections), "
          f"{todo_count} todo pages skipped.")

    # Extract key points for ALL synthesis pages (including reflections)
    synthesis_pages = extract_key_points(synthesis_pages, force=force_keypoints)

    # Rebuild reflection_pages list with key_points now populated
    reflection_pages = [p for p in synthesis_pages if p["type"] == "reflection"]
    external_pages = [p for p in synthesis_pages if p["type"] != "reflection"]

    # Identify topics from external pages only (reflections handled separately)
    topics = identify_topics(external_pages, force=force_topics)

    # --review-topics: print the list and stop so user can edit topics.json before committing
    if review_topics:
        print("\n" + "="*60)
        print(f"PROPOSED TOPICS ({len(topics)} total)")
        print("="*60)
        for i, t in enumerate(topics, 1):
            keywords = ", ".join(t.get("keywords", []))
            print(f"\n{i:>3}. {t['name']}")
            print(f"      {t['description']}")
            print(f"      Keywords: {keywords}")
        print("\n" + "="*60)
        print(f"Topics saved to: {TOPICS_CACHE}")
        print("\nTo edit: open vault/_cache/topics.json and add, remove, or rename topics.")
        print("Then run:  python3 run_all.py --only-synthesize --force-pages")
        print("="*60)
        return

    generated = 0
    skipped = 0
    already_existed = 0

    for topic in topics:
        # Find relevant external sources AND relevant reflections separately
        relevant_external = find_relevant_pages(topic, external_pages)
        relevant_reflections = find_relevant_pages(topic, reflection_pages)

        if len(relevant_external) < 2 and len(relevant_reflections) < 2:
            print(f"  [skip] '{topic['name']}' — too few sources")
            skipped += 1
            continue

        safe_name = re.sub(r'[\\/:*?"<>|]', "-", topic["name"]).strip()
        out_path = TOPICS_DIR / f"{safe_name}.md"

        if out_path.exists() and not force_pages:
            already_existed += 1
            continue

        all_relevant = relevant_external + relevant_reflections
        print(f"  Generating: '{topic['name']}' "
              f"({len(relevant_external)} sources, {len(relevant_reflections)} reflections)...")
        content = generate_topic_page(topic, all_relevant)
        if not content:
            skipped += 1
            continue

        out_path.write_text(content, encoding="utf-8")
        generated += 1

    print(f"\nTopic synthesis complete.")
    print(f"  {generated} topic pages written")
    if already_existed:
        print(f"  {already_existed} already existed (--force-pages to regenerate)")
    print(f"  {skipped} skipped")

    # Quotes pass
    print("\nGenerating Quotes & Wisdom page...")
    generate_quotes_page(synthesis_pages, force=force_pages)

    # Reflections pass
    generate_reflections_pages(reflection_pages, force=force_topics or force_pages)

    print(f"\nAll done. Vault is at: {VAULT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthesize topic pages from source notes")
    parser.add_argument("--force-keypoints", action="store_true")
    parser.add_argument("--force-topics", action="store_true")
    parser.add_argument("--force-pages", action="store_true")
    parser.add_argument("--force", action="store_true", help="Force all steps")
    parser.add_argument("--review-topics", action="store_true",
                        help="Print proposed topics and exit without generating pages")
    args = parser.parse_args()

    synthesize_all(
        force_keypoints=args.force or args.force_keypoints,
        force_topics=args.force or args.force_topics,
        force_pages=args.force or args.force_pages,
        review_topics=args.review_topics,
    )
