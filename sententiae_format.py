"""Tidy a raw ## Sententiae Antiquae paste into the journal's display layout.

WHY THIS EXISTS
---------------
Sean pastes each day's passage from a PDF, so it arrives as three loose runs of
hard-wrapped lines — citation, translation, original — with no markup:

    Seneca, *De Vita Beata* 18.1
    “For all these people were talking not about how they were living
    themselves but about how they should live.”
    omnes enim isti dicebant non quemadmodum ipsi viverent, sed
    quemadmodum esset ipsis vivendum

The OneNote-era entries (Oct 2025 – Apr 2026) were imported in a cleaner
layout, and that is what `## On This Day` shows when a day laps:

    **Seneca, *De Vita Beata* 18.1**

    > “For all these people were talking ... about how they should live.”

    *omnes enim isti dicebant ... quemadmodum esset ipsis vivendum*

--parse runs every new entry through `format_sententiae` before it is written
to _journal/, so next year's On This Day shows this year's passage the same way.

LOAD-BEARING: this never drops or rewrites Sean's words. It only joins wrapped
lines and adds markup, and anything it can't confidently identify (no
translation in quotes, an entry already formatted, a comment after the
passage) is passed through verbatim. A wrong guess costs a little formatting;
a lost line would be a hole in a permanent record.
"""

from __future__ import annotations

import re

_OPEN_QUOTE  = re.compile(r"^[“\"‘]")
# A translation's last line ends in a closing DOUBLE quote, perhaps followed by
# punctuation that sat outside it (“… ‘themselves’”.). Not ’: a translation that
# quotes speech can end a line on a single quote mid-passage (Phaedrus 4.23).
_CLOSE_QUOTE = re.compile(r"[”\"][.,;:!?]*$")
_GREEK       = re.compile(r"[Ͱ-Ͽἀ-῿]")


def _is_greek(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and sum(bool(_GREEK.match(c)) for c in letters) > len(letters) / 2


def _join(lines: list[str]) -> str:
    return " ".join(l.strip() for l in lines if l.strip())


def _bold(text: str) -> str:
    # A citation the size of the whole bolded line already (**…**) stays as is.
    return text if text.startswith("**") and text.endswith("**") else f"**{text}**"


def _italic(text: str) -> str:
    return text if re.fullmatch(r"\*[^*].*\*|_.*_", text) else f"*{text}*"


def _format_entry(lines: list[str]) -> tuple[str, list[str]] | None:
    """Format one passage from the top of `lines` (blank lines removed).

    Returns (formatted text, the lines left over), or None if the lines don't
    look like citation + quoted translation + original.
    """
    # Translation starts at the first line opening with a quote mark; the
    # citation is everything above it (long titles wrap onto a second line).
    start = next((i for i, l in enumerate(lines[:4]) if i and _OPEN_QUOTE.match(l)), None)
    if start is None:
        # Some pastes lose the opening quote but keep the closing one:
        # one citation line, then a translation that ends in ”.
        if len(lines) >= 3 and any(_CLOSE_QUOTE.search(l) for l in lines[1:4]):
            start = 1
        else:
            return None
    end = next((i for i in range(start, len(lines)) if _CLOSE_QUOTE.search(lines[i])), None)
    if end is None:
        # …and some lose the closing quote: then a Greek original marks where
        # the translation stopped. A Latin original can't be told apart from
        # English this way, so that case is left alone.
        greek = next((i for i in range(start + 1, len(lines)) if _is_greek(lines[i])), None)
        if greek is None:
            return None
        end = greek - 1
    rest = lines[end + 1:]
    if not rest:
        return None
    original = []
    for l in rest:
        # Hashtags and "Cp.:" asides are Sean's notes, not the original text.
        if l.lstrip().startswith(("#", "Cp.")):
            break
        original.append(l)
    if not original:
        return None
    leftover = rest[len(original):]
    text = (f"{_bold(_join(lines[:start]))}\n\n"
            f"> {_join(lines[start:end + 1])}\n\n"
            f"{_italic(_join(original))}")
    return text, leftover


def format_sententiae(body: str) -> str:
    """Return `body` in the display layout, or unchanged if it doesn't fit."""
    body = body.strip()
    if not body or body.startswith("**") or re.search(r"^> ", body, re.MULTILINE):
        return body     # empty, or already formatted (e.g. a re-parse)
    # Blank-line-separated paragraphs. A day can hold two passages (a second
    # citation after a blank line), and a translation can itself contain blank
    # lines, so paragraphs are consumed greedily: each passage takes as many as
    # it needs, and whatever no passage claims is kept verbatim.
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    i = 0
    while i < len(paras):
        done = None
        # Try the shortest run of paragraphs that forms a whole passage.
        for j in range(i + 1, min(i + 4, len(paras)) + 1):
            lines = [l for p in paras[i:j] for l in p.splitlines() if l.strip()]
            res = _format_entry(lines)
            if res and not res[1]:
                done = (res[0], j)
                break
            if res and res[1] and j == i + 1:
                # Passage plus trailing notes in the same paragraph.
                done = (res[0] + "\n\n" + "\n".join(res[1]), j)
                break
        if done:
            out.append(done[0]); i = done[1]
        else:
            out.append(paras[i].strip()); i += 1
    return "\n\n".join(out)
