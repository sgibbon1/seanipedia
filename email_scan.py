"""
email_scan.py — Scan your inbox (MAIL_ACCOUNT) for "For Notes" emails and route into the daily note.

Connects to Gmail over IMAP using an App Password (set GMAIL_APP_PASSWORD in .env).
Matches emails from SENDER_ADDRS whose subject starts with "For Notes" or today's date ("4 May", "04 May", "May 4").
Deduplicates via .email_scan_log.json.

Set MAIL_ACCOUNT and SENDER_ADDRS in .env before running — see README.

Routing:
  - Emails dated *today* are appended to _inbox/Today.md's "## Notes" section
    (the daily workspace; daily.py --parse routes it to _outbox/Notes/ at 3am).
  - Past-dated emails (catch-up) go straight to _outbox/Notes/YYYYMMDD.md, since
    that day's Today.md no longer exists.
  - If Today.md is missing (machine asleep at 6am generate), today's notes also
    fall back to _outbox/Notes/YYYYMMDD.md so nothing is lost.

Setup (one-time):
  1. Enable IMAP in Gmail: Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP
  2. Generate an App Password: myaccount.google.com → Security → 2-Step Verification → App Passwords
  3. Add to .env:
       GMAIL_APP_PASSWORD=your-16-char-password
       MAIL_ACCOUNT=your.email@gmail.com
       SENDER_ADDRS=your.email@gmail.com,another.address@example.com

Usage:
  python3 email_scan.py            # scan and route new emails
  python3 email_scan.py --dry-run  # preview without writing
  python3 email_scan.py --days 14  # look back further (default: 7)
"""

import argparse
import email as _email_lib
import imaplib
import json
import os
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv

# Secrets: LOCAL disk first (~/.config/seanipedia/.env), Drive copy as fallback.
# Reading .env off the Google Drive mount is what killed the 2026-08-12 scheduled
# run — Drive returns EDEADLK when the placeholder isn't hydrated, and this read
# happens at import, before any retry logic. resilient_run.sh keeps the local
# copy in sync whenever Drive is healthy. See daily_brief/daily_brief.py.
_LOCAL_ENV = Path.home() / ".config" / "seanipedia" / ".env"
load_dotenv(dotenv_path=_LOCAL_ENV if _LOCAL_ENV.exists() else Path(__file__).parent / ".env",
            override=True)

VAULT_PATH      = Path(os.environ.get("VAULT_PATH", "./vault"))
NOTES_DIR       = VAULT_PATH / "_outbox" / "Notes"
TODAY_PATH      = VAULT_PATH / "_inbox" / "Today.md"
ATTACHMENTS_DIR = VAULT_PATH / "attachments"
PROCESSED_LOG   = Path(__file__).parent / ".email_scan_log.json"
# MAIL_ACCOUNT: the inbox to scan. SENDER_ADDRS: comma-separated addresses
# treated as "you" (self-sent notes) — usually MAIL_ACCOUNT plus any other
# address you send yourself notes from. Set both in .env (see README).
MAIL_ACCOUNT    = os.environ.get("MAIL_ACCOUNT", "")
SENDER_ADDRS    = {a.strip() for a in os.environ.get("SENDER_ADDRS", "").split(",") if a.strip()}
SCAN_DAYS       = 7
IMAP_HOST       = "imap.gmail.com"
IMAP_PORT       = 993


def _today_subject_patterns(for_date=None) -> list[str]:
    """Date formats that count as a note: '4 May', '04 May', 'May 4', 'May 04'."""
    d_obj = for_date or date.today()
    d, mon = d_obj.day, d_obj.strftime("%b")
    return [f"{d} {mon}", f"{d:02d} {mon}", f"{mon} {d}", f"{mon} {d:02d}"]


def is_notes_subject(subject: str, for_date=None) -> bool:
    s = subject.strip()
    if s.lower().startswith("for notes"):
        return True
    return any(s.lower().startswith(p.lower()) for p in _today_subject_patterns(for_date))


# ── IMAP helpers ───────────────────────────────────────────────────────────

def _imap_connect() -> imaplib.IMAP4_SSL:
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD not set in .env — see setup instructions at the top of this file."
        )
    if not MAIL_ACCOUNT:
        raise RuntimeError("MAIL_ACCOUNT not set in .env — see setup instructions at the top of this file.")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(MAIL_ACCOUNT, password)
    return conn


# ── HTML → Markdown ────────────────────────────────────────────────────────

# Tags that are self-closing (no end tag in HTML5)
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


class _HTMLToMarkdown(HTMLParser):
    """Convert a Gmail HTML email body to Markdown.

    Handles:
      - <a href>              → [text](url)
      - <img>                 → ![alt](url) or ![[file]] for CID
      - <b>/<strong>          → **bold**
      - <i>/<em>              → *italic*
      - <u>                   → <u>underline</u>  (HTML passthrough; Obsidian renders it)
      - <h1>–<h4>             → # / ## / ### / ####
      - <ul>/<ol>/<li>        → - item / 1. item  (nested with indent)
      - <blockquote>          → > quoted text  (nested blockquotes stack correctly)
      - <table>/<tr>/<th>/<td>→ Markdown pipe table
      - <pre>/<code>          → ```fenced``` / `inline`
      - <hr>                  → ---
      - Gmail quote/signature → stripped entirely
    """

    def __init__(self, cid_map: dict):
        super().__init__()
        self.cid_map = cid_map          # content-id (no angle brackets) → saved filename

        self._parts: list[str] = []     # root output buffer

        # Link state
        self._link_href: str | None = None
        self._link_buf:  list[str]  = []

        # List state: stack of {"type": "ul"|"ol", "count": int}
        self._list_stack: list[dict] = []

        # Blockquote state: stack of per-level output buffers
        self._bq_stack: list[list[str]] = []

        # Table state: stack of table dicts (supports nested tables, rare but valid)
        #   rows         – completed rows, each a list of cell strings
        #   cur_row      – cells accumulated for the current <tr>
        #   is_header    – True while inside <thead> or a row of <th> cells
        #   cell_buf     – None when not in a cell; list[str] when inside <td>/<th>
        self._table_stack: list[dict] = []

        # Code state
        self._in_pre  = False
        self._in_code = False

        # Skip / head state
        self._skip_depth = 0
        self._in_head    = False

    # ── helpers ───────────────────────────────────────────────────────────

    def _active_buf(self) -> list[str]:
        """The currently active structural output buffer (blockquote level or root)."""
        return self._bq_stack[-1] if self._bq_stack else self._parts

    def _cell_buf(self):
        """Return the active cell buffer if we're inside a table cell, else None."""
        if self._table_stack:
            return self._table_stack[-1]["cell_buf"]
        return None

    def _emit(self, text: str) -> None:
        """Route text to the innermost active buffer, respecting all state."""
        if self._skip_depth or self._in_head:
            return
        if self._link_href is not None:
            self._link_buf.append(text)
            return
        cb = self._cell_buf()
        if cb is not None:
            cb.append(text)
        elif not self._table_stack:          # discard text between cells/rows
            self._active_buf().append(text)

    def _emit_direct(self, text: str) -> None:
        """Like _emit but bypasses link buffering (for structural tokens)."""
        if self._skip_depth or self._in_head:
            return
        cb = self._cell_buf()
        if cb is not None:
            cb.append(text)
        elif not self._table_stack:
            self._active_buf().append(text)

    def _list_prefix(self) -> str:
        """Return the correct bullet/number prefix for the current list item."""
        if not self._list_stack:
            return ""
        top    = self._list_stack[-1]
        indent = "  " * (len(self._list_stack) - 1)
        if top["type"] == "ul":
            return f"\n{indent}- "
        else:
            top["count"] += 1
            return f"\n{indent}{top['count']}. "

    @staticmethod
    def _render_table(rows: list[list[str]]) -> str:
        """Format a list-of-rows into a Markdown pipe table."""
        if not rows:
            return ""
        col_count = max(len(r) for r in rows)
        # Pad short rows
        rows = [r + [""] * (col_count - len(r)) for r in rows]
        # Column widths: at least 3 for a valid separator
        widths = [max(max(len(r[c]) for r in rows), 3) for c in range(col_count)]

        def fmt_row(row: list[str]) -> str:
            cells = [f" {cell.ljust(w)} " for cell, w in zip(row, widths)]
            return "|" + "|".join(cells) + "|"

        sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
        lines = [fmt_row(rows[0]), sep] + [fmt_row(r) for r in rows[1:]]
        return "\n".join(lines)

    def result(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ── tag handlers ──────────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs):
        tag     = tag.lower()
        attrs_d = dict(attrs)

        if tag == "head":
            self._in_head = True
            return

        # Gmail chrome → skip subtree
        cls = attrs_d.get("class") or ""
        if any(k in cls for k in ("gmail_quote", "gmail_signature")):
            self._skip_depth += 1
            return

        if tag in ("style", "script"):
            self._skip_depth += 1
            return

        if self._skip_depth:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return

        # ── Inline formatting ─────────────────────────────────────────────
        if tag == "a":
            href = attrs_d.get("href") or ""
            if href and not href.startswith("mailto:"):
                self._link_href = href
                self._link_buf  = []

        elif tag == "img":
            src = attrs_d.get("src") or ""
            alt = attrs_d.get("alt") or "image"
            if src.startswith("cid:"):
                cid   = src[4:].strip().rstrip(">")
                fname = self.cid_map.get(cid)
                if fname:
                    self._emit(f"![[{fname}]]")
            elif src:
                self._emit(f"![{alt}]({src})")

        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag in ("i", "em"):
            self._emit("*")
        elif tag == "u":
            self._emit("<u>")
        elif tag == "code":
            if not self._in_pre:
                self._emit("`")
                self._in_code = True

        # ── Block structure ───────────────────────────────────────────────
        elif tag == "pre":
            self._in_pre = True
            self._emit("\n```\n")

        elif tag in ("h1", "h2", "h3", "h4"):
            level = int(tag[1])
            self._emit("\n" + "#" * level + " ")

        elif tag == "p":
            self._emit("\n\n")
        elif tag == "div":
            self._emit("\n")
        elif tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._emit_direct("\n\n---\n\n")

        elif tag == "ul":
            self._list_stack.append({"type": "ul", "count": 0})
        elif tag == "ol":
            start = int(attrs_d.get("start") or 1)
            self._list_stack.append({"type": "ol", "count": start - 1})
        elif tag == "li":
            self._emit_direct(self._list_prefix())

        elif tag == "blockquote":
            self._bq_stack.append([])

        # ── Table structure ───────────────────────────────────────────────
        elif tag == "table":
            self._table_stack.append({
                "rows":      [],
                "cur_row":   [],
                "is_header": False,
                "cell_buf":  None,
            })
        elif tag == "thead":
            if self._table_stack:
                self._table_stack[-1]["is_header"] = True
        elif tag == "tr":
            if self._table_stack:
                self._table_stack[-1]["cur_row"] = []
        elif tag in ("th", "td"):
            if self._table_stack:
                self._table_stack[-1]["cell_buf"] = []

    def handle_endtag(self, tag: str):
        tag = tag.lower()

        if tag == "head":
            self._in_head = False
            return

        if self._skip_depth:
            self._skip_depth -= 1
            return

        # ── Link close ────────────────────────────────────────────────────
        if tag == "a" and self._link_href is not None:
            text = "".join(self._link_buf).strip()
            href = self._link_href
            self._link_href = None
            self._link_buf  = []
            token = f"[{text}]({href})" if (text and text != href) else (href or "")
            if token:
                # Route to cell buf if in a cell, otherwise active buf
                cb = self._cell_buf()
                if cb is not None:
                    cb.append(token)
                elif not self._table_stack:
                    self._active_buf().append(token)

        # ── Inline formatting close ───────────────────────────────────────
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag in ("i", "em"):
            self._emit("*")
        elif tag == "u":
            self._emit("</u>")
        elif tag == "code":
            if self._in_code:
                self._emit("`")
                self._in_code = False

        # ── Block close ───────────────────────────────────────────────────
        elif tag == "pre":
            self._in_pre = False
            self._emit("\n```\n")

        elif tag in ("h1", "h2", "h3", "h4"):
            self._emit("\n")
        elif tag == "p":
            self._emit("\n")
        elif tag == "div":
            self._emit("\n")

        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._emit_direct("\n")

        elif tag == "blockquote":
            if self._bq_stack:
                buf     = self._bq_stack.pop()
                content = "".join(buf).strip()
                quoted  = "\n".join(
                    f"> {line}" if line.strip() else ">"
                    for line in content.splitlines()
                )
                self._active_buf().append(f"\n{quoted}\n")

        # ── Table close ───────────────────────────────────────────────────
        elif tag == "thead":
            if self._table_stack:
                self._table_stack[-1]["is_header"] = False

        elif tag in ("th", "td"):
            if self._table_stack:
                t   = self._table_stack[-1]
                buf = t["cell_buf"]
                t["cell_buf"] = None
                if buf is not None:
                    cell_text = "".join(buf).strip().replace("\n", " ")
                    t["cur_row"].append(cell_text)

        elif tag == "tr":
            if self._table_stack:
                t = self._table_stack[-1]
                if t["cur_row"]:
                    t["rows"].append(t["cur_row"])
                t["cur_row"] = []

        elif tag == "table":
            if self._table_stack:
                t      = self._table_stack.pop()
                md_tbl = self._render_table(t["rows"])
                if md_tbl:
                    self._active_buf().append(f"\n\n{md_tbl}\n\n")

    def handle_data(self, data: str):
        self._emit(data)


def _save_inline_image(part, ymd: str) -> tuple[str, str]:
    """Extract an inline image MIME part to vault/attachments/.
    Returns (content-id-no-brackets, saved-filename)."""
    cid      = (part.get("Content-ID") or "").strip("<>")
    filename = part.get_filename() or f"img_{cid[:12] or 'unknown'}"
    filename = re.sub(r"[^\w.\-]", "_", filename)
    dated    = f"{ymd}_{filename}"
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ATTACHMENTS_DIR / dated
    if not dest.exists():
        payload = part.get_payload(decode=True)
        if payload:
            dest.write_bytes(payload)
    return cid, dated


def parse_email_source(raw_source: str, ymd: str) -> str:
    """Parse a raw MIME email source string into clean Markdown.

    - Finds the HTML part (preferred) or plain-text part (fallback).
    - Extracts inline image attachments (CID) to vault/attachments/.
    - Converts HTML links → [text](url), external images → ![alt](url),
      inline images → ![[filename]].
    - Strips Gmail quote blocks and signatures.
    """
    msg = _email_lib.message_from_string(raw_source)

    cid_map:    dict[str, str] = {}
    html_part                  = None
    plain_part                 = None

    parts_iter = msg.walk() if msg.is_multipart() else [msg]
    for part in parts_iter:
        ct  = part.get_content_type()
        cid = (part.get("Content-ID") or "").strip("<>")

        if ct.startswith("image/") and cid:
            _, saved = _save_inline_image(part, ymd)
            cid_map[cid] = saved
        elif ct == "text/html" and html_part is None:
            html_part  = part
        elif ct == "text/plain" and plain_part is None:
            plain_part = part

    # ── HTML → Markdown (preferred) ───────────────────────────────────────
    if html_part is not None:
        payload = html_part.get_payload(decode=True) or b""
        charset = html_part.get_content_charset() or "utf-8"
        html_text = payload.decode(charset, errors="replace")

        parser = _HTMLToMarkdown(cid_map)
        parser.feed(html_text)
        body = parser.result()

        # Stop at inline quoted-reply markers that survive HTML stripping
        lines, clean = body.splitlines(), []
        for line in lines:
            s = line.strip()
            if s == "--" or re.match(r"^On .+wrote:$", s):
                break
            clean.append(line)
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(clean))
        return normalize_note_spacing(body)

    # ── Plain-text fallback ───────────────────────────────────────────────
    if plain_part is not None:
        payload = plain_part.get_payload(decode=True) or b""
        charset = plain_part.get_content_charset() or "utf-8"
        text    = payload.decode(charset, errors="replace")
    else:
        raw  = msg.get_payload()
        text = raw if isinstance(raw, str) else ""

    return clean_body(text)


# ── Log helpers ────────────────────────────────────────────────────────────

def load_log() -> set:
    if PROCESSED_LOG.exists():
        return set(json.loads(PROCESSED_LOG.read_text()))
    return set()


def save_log(processed: set) -> None:
    PROCESSED_LOG.write_text(json.dumps(sorted(processed), indent=2))


# ── Routing ────────────────────────────────────────────────────────────────

def parse_email_date(date_str: str) -> date:
    """Parse RFC 2822 or Apple Mail date strings, falling back to today."""
    try:
        return parsedate_to_datetime(date_str).date()
    except Exception:
        pass
    for fmt in ("%A, %B %d, %Y at %I:%M:%S %p",
                "%A, %B %d, %Y at %I:%M %p",
                "%B %d, %Y at %I:%M:%S %p"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return date.today()


def normalize_note_spacing(text: str) -> str:
    """Collapse blank lines that the mail client inserted between ordinary
    adjacent text lines, while preserving ONE blank line before Markdown
    headings (#..######) and before the start of a list group.

    Rationale: Gmail emits a hard paragraph break (<p> -> blank line) even when
    Sean only meant a soft line break, so consecutive notes/attributions get
    spurious blank lines. We keep blanks only where they aid readability:
    above headings and above the first item of a list.
    """
    is_heading   = lambda s: bool(re.match(r"^#{1,6}\s", s))
    is_list_item = lambda s: bool(re.match(r"^\s*([-*+]|\d+\.)\s", s))

    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip() == "":
            j = i
            while j < n and lines[j].strip() == "":
                j += 1
            prev = out[-1] if out else ""
            nxt  = lines[j] if j < n else ""
            keep = bool(out) and j < n and (
                is_heading(nxt) or (is_list_item(nxt) and not is_list_item(prev))
            )
            if keep:
                out.append("")
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip()


def clean_body(body: str) -> str:
    """Strip Gmail signature, quoted reply chains, and image placeholders."""
    lines = body.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped == "--":
            break
        if re.match(r"^On .+ wrote:$", stripped):
            break
        if stripped.startswith(">"):
            break
        line = re.sub(r"\[image:[^\]]*\]", "", line)
        cleaned.append(line)
    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_note_spacing(text)


def insert_into_today_notes(content: str) -> bool:
    """Append note content into Today.md's ## Notes section.

    Returns False if Today.md or its ## Notes section can't be found, so the
    caller can fall back to writing _outbox/Notes/YYYYMMDD.md directly.
    """
    if not TODAY_PATH.exists():
        return False
    text = TODAY_PATH.read_text(encoding="utf-8")
    # Capture the body of the ## Notes section, up to the next --- separator.
    m = re.search(r"## Notes\n(.*?)(?=\n---\n)", text, re.DOTALL)
    if not m:
        return False
    body = m.group(1).strip()
    addition = (body + "\n\n" if body else "") + content.strip()
    new_text = text[: m.start(1)] + addition + "\n" + text[m.end(1) :]
    TODAY_PATH.write_text(new_text, encoding="utf-8")
    return True


def route_to_notes(msg: dict, dry_run: bool) -> None:
    msg_date  = parse_email_date(msg["date_str"])
    ymd       = msg_date.strftime("%Y%m%d")
    date_full = msg_date.strftime("%B %-d, %Y")
    body      = msg["body"]

    subject = msg["subject"]
    if subject.lower().startswith("for notes"):
        topic  = subject[len("for notes"):].strip(" :—-")
        header = f"**{topic}**\n\n" if topic else ""
    else:
        header = ""
    content = f"{header}{body}"

    is_today = msg_date == date.today()

    if dry_run:
        dest = "Today.md ## Notes" if (is_today and TODAY_PATH.exists()) else f"Notes/{ymd}.md"
        print(f"\n  [DRY RUN] Would append to {dest}:")
        print(f"  Subject: {subject}")
        print(f"  Preview: {body[:200]}…")
        return

    # Today's notes go into the daily workspace; --parse routes them at 3am.
    if is_today and insert_into_today_notes(content):
        print(f"  Routed to Today.md ## Notes — {subject}")
        return

    # Past-dated emails, or today's notes when Today.md is absent.
    out_path = NOTES_DIR / f"{ymd}.md"
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        existing = out_path.read_text(encoding="utf-8")
        out_path.write_text(existing.rstrip() + f"\n\n---\n\n{content}\n",
                            encoding="utf-8")
    else:
        out_path.write_text(f"# {date_full}\n\n{content}\n", encoding="utf-8")

    print(f"  Routed to Notes/{ymd}.md — {subject}")


# ── IMAP fetch & mark ──────────────────────────────────────────────────────

def get_recent_self_messages(days: int, for_date=None) -> list[dict]:
    """Return new notes emails from the last `days` days via IMAP."""
    cutoff    = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    processed = load_log()

    conn = _imap_connect()
    messages = []
    try:
        conn.select("INBOX")

        # Collect UIDs from each sender separately (a message has only one sender,
        # so there's no overlap to deduplicate).
        all_uids: list[bytes] = []
        for sender in SENDER_ADDRS:
            _, data = conn.uid("search", None, f'FROM "{sender}" SINCE {cutoff}')
            if data[0]:
                all_uids.extend(data[0].split())

        for uid in all_uids:
            _, raw_data = conn.uid("fetch", uid, "(RFC822)")
            if not raw_data or raw_data[0] is None:
                continue
            raw_bytes  = raw_data[0][1]
            raw_source = raw_bytes.decode("utf-8", errors="replace") if isinstance(raw_bytes, bytes) else raw_bytes

            parsed     = _email_lib.message_from_string(raw_source)
            subject    = str(parsed.get("Subject", "")).strip()
            message_id = str(parsed.get("Message-ID", "")).strip()
            date_str   = str(parsed.get("Date", "")).strip()

            if message_id in processed:
                continue
            if not is_notes_subject(subject, for_date):
                continue

            msg_date = parse_email_date(date_str)
            ymd      = msg_date.strftime("%Y%m%d")
            body     = parse_email_source(raw_source, ymd)

            messages.append({
                "id":       message_id,
                "uid":      uid,
                "subject":  subject,
                "date_str": date_str,
                "body":     body,
            })
    finally:
        conn.logout()

    return messages


def mark_messages_read(uids: list[bytes]) -> None:
    if not uids:
        return
    conn = _imap_connect()
    try:
        conn.select("INBOX")
        for uid in uids:
            conn.uid("store", uid, "+FLAGS", "\\Seen")
    except Exception as e:
        print(f"  Warning: could not mark messages read: {e}")
    finally:
        conn.logout()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scan notes emails and route to _outbox/Notes/")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=SCAN_DAYS,
                        help=f"Days to look back (default: {SCAN_DAYS})")
    parser.add_argument("--date", type=str, default=None,
                        help="Match subject patterns for this date (YYYY-MM-DD) instead of today")
    args = parser.parse_args()

    for_date = date.fromisoformat(args.date) if args.date else None

    try:
        messages = get_recent_self_messages(days=args.days, for_date=for_date)
    except RuntimeError as e:
        print(f"Error: {e}")
        return

    processed   = load_log()
    new_messages = [m for m in messages if m["id"] not in processed]

    if not new_messages:
        print("No new notes emails found.")
        return

    print(f"Found {len(new_messages)} new notes email(s).")

    for msg in new_messages:
        route_to_notes(msg, dry_run=args.dry_run)
        if not args.dry_run:
            processed.add(msg["id"])

    if not args.dry_run:
        mark_messages_read([m["uid"] for m in new_messages])
        save_log(processed)
        print(f"\nDone. {len(new_messages)} email(s) routed.")


if __name__ == "__main__":
    main()
