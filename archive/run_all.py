"""
run_all.py — One-shot pipeline: extract → convert → synthesize.

Run this the first time to build your vault from scratch.
After that, use sync.py to add new notes incrementally.

Usage:
  python run_all.py              # full pipeline
  python run_all.py --skip-extract   # skip API download (re-use raw/)
  python run_all.py --skip-convert   # skip HTML→MD (re-use vault/_sources/)
  python run_all.py --only-synthesize
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="OneNote → Obsidian vault pipeline")
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip the Graph API download step")
    parser.add_argument("--skip-convert", action="store_true",
                        help="Skip the HTML→Markdown conversion step")
    parser.add_argument("--skip-images", action="store_true",
                        help="Skip image downloads during conversion (fast pass)")
    parser.add_argument("--only-synthesize", action="store_true",
                        help="Only run synthesis (implies --skip-extract --skip-convert)")
    parser.add_argument("--force-keypoints", action="store_true",
                        help="Re-extract key points for all pages (ignore cache)")
    parser.add_argument("--force-topics", action="store_true",
                        help="Re-identify topics even if topics.json cache exists")
    parser.add_argument("--force-pages", action="store_true",
                        help="Regenerate topic pages that already exist on disk")
    parser.add_argument("--force", action="store_true",
                        help="Force all synthesis steps (re-extract, re-identify, re-generate)")
    parser.add_argument("--review-topics", action="store_true",
                        help="Print proposed topics and exit without generating pages")
    args = parser.parse_args()

    if args.only_synthesize:
        args.skip_extract = True
        args.skip_convert = True

    if not args.skip_extract:
        print("=" * 60)
        print("STEP 1/3 — Extracting from OneNote via Microsoft Graph API")
        print("=" * 60)
        from extract import extract_all
        extract_all()
        print()

    if not args.skip_convert:
        print("=" * 60)
        print("STEP 2/3 — Converting HTML → Markdown")
        print("=" * 60)
        from convert import convert_all
        convert_all(skip_images=args.skip_images)
        print()

    print("=" * 60)
    print("STEP 3/3 — Synthesizing topic pages with Claude")
    print("=" * 60)
    from synthesize import synthesize_all
    synthesize_all(
        force_keypoints=args.force or args.force_keypoints,
        force_topics=args.force or args.force_topics,
        force_pages=args.force or args.force_pages,
        review_topics=args.review_topics,
    )
    print()

    print("=" * 60)
    print("Done! Open your vault in Obsidian:")

    import os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(override=True)
    vault = Path(os.environ.get("VAULT_PATH", "./vault")).resolve()
    print(f"  {vault}")
    print()
    print("Going forward, add new notes to:")
    print(f"  {vault / '_inbox'}/")
    print("Then run:  python sync.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
