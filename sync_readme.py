"""Copy the repo README into the vault twin, keeping the twins' two deliberate
differences.

The pipeline is documented in ONE README that lives in two places:

  seanipedia/README.md        — the repo copy, which is PUBLIC on GitHub
  vault/_inbox/README.md      — the copy Sean reads inside Obsidian

They must say the same thing, with exactly two exceptions:

  1. The repo copy has a "## Setup" section for anyone cloning the project;
     the vault copy doesn't need it.
  2. Lines naming Sean's real email addresses ("For Notes" instructions) are
     generic in the repo — it's public — and specific in the vault.

So edit the REPO copy, then run this. It never writes personal data into the
repo: for each personal line it reuses whatever the vault copy currently says
at that spot, rather than knowing the addresses itself.

  python3 sync_readme.py           # write the vault copy, then show the diff
  python3 sync_readme.py --check   # exit 1 if the twins have drifted
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

_LOCAL_ENV = Path.home() / ".config" / "seanipedia" / ".env"
_ENV_VAULT_PATH = os.environ.get("VAULT_PATH")
load_dotenv(_LOCAL_ENV if _LOCAL_ENV.exists() else Path(__file__).parent / ".env",
            override=True)
if _ENV_VAULT_PATH:
    os.environ["VAULT_PATH"] = _ENV_VAULT_PATH

REPO_README  = Path(__file__).parent / "README.md"
VAULT_README = Path(os.environ.get("VAULT_PATH", "./vault")) / "_inbox" / "README.md"

# Lines that are generic in the public repo and personal in the vault, keyed
# by a prefix both versions share. The vault's current text wins.
PERSONAL_LINE_PREFIXES = [
    "Email yourself at ",
    '- **Email "For Notes"** — send from ',
]


def build_vault_copy(repo: str, current_vault: str) -> str:
    # 1. Drop the repo-only Setup section, including the "---" rule above it.
    out = re.sub(r"\n---\n\n## Setup\n.*?(?=\n---\n)", "", repo, count=1, flags=re.DOTALL)

    # 2. Restore the vault's personal wording for each shared-prefix line.
    vault_lines = current_vault.splitlines()
    lines = out.splitlines()
    for prefix in PERSONAL_LINE_PREFIXES:
        mine = next((l for l in vault_lines if l.startswith(prefix)), None)
        if mine is None:
            continue
        lines = [mine if l.startswith(prefix) else l for l in lines]
    return "\n".join(lines) + ("\n" if out.endswith("\n") else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report drift; write nothing")
    args = ap.parse_args()

    repo = REPO_README.read_text(encoding="utf-8")
    current = VAULT_README.read_text(encoding="utf-8") if VAULT_README.exists() else ""
    wanted = build_vault_copy(repo, current)

    if args.check:
        if wanted == current:
            print("READMEs in sync.")
            return 0
        print("READMEs have drifted — run: python3 sync_readme.py")
        return 1

    VAULT_README.write_text(wanted, encoding="utf-8")
    print(f"Wrote {VAULT_README}")
    # Show the remaining (intended) differences so a stray one is obvious.
    subprocess.run(["diff", str(REPO_README), str(VAULT_README)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
