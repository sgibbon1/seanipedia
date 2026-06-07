"""
inbox_pipeline.py — Route inbox notes to _sources/ pages.

Topic pages are kept current by resynthesize.py (runs Sunday nights, or
on-demand with --topic). This pipeline handles only the source-routing step.

Usage:
  python3 inbox_pipeline.py             # interactive routing for all inbox files
  python3 inbox_pipeline.py --dry-run   # preview without writing
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def run(cmd: list[str]) -> int:
    return subprocess.run(cmd).returncode


def pipeline(dry_run: bool = False) -> None:
    base = [sys.executable]
    dry = ["--dry-run"] if dry_run else []

    print("=" * 60)
    print("Route to _sources/")
    print("=" * 60)
    rc = run(base + [str(SCRIPT_DIR / "sort_inbox.py")] + dry)
    if rc != 0:
        print(f"\nsort_inbox.py exited with code {rc}")
        sys.exit(rc)

    print("\nPipeline complete. Run resynthesize.py to update topic pages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Route inbox notes to source pages")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing files")
    args = parser.parse_args()
    pipeline(dry_run=args.dry_run)
