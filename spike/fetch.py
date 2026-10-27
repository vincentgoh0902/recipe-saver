# /// script
# requires-python = ">=3.11"
# dependencies = ["yt-dlp==2026.10.26"]
# ///
"""Milestone 1 fetch spike: print caption and metadata for TikTok URLs.

Usage:
    uv run spike/fetch.py URL [URL ...]
    uv run spike/fetch.py --file links.txt

Each result is also saved to data/samples/<id>.json so the parser
milestone has real captions to test against.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yt_dlp

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

# Rough F4 preview: does the caption look like it has ingredients and steps?
INGREDIENT_RE = re.compile(
    r"\b(\d+[\d/.\s]*|½|¼|¾)\s*(g|kg|ml|l|cups?|tbsp|tsp|tablespoons?|teaspoons?|oz|lbs?|cloves?)\b"
    r"|ingredients?\s*:",
    re.IGNORECASE,
)
# Captions usually arrive as one line, so look for "1. Word ... 2. Word" anywhere
# (the letter after the dot keeps quantities like "1.8KG" from matching).
STEP_RE = re.compile(
    r"(?:^|\s)1[.)]\s+[A-Za-z].*?\s2[.)]\s+[A-Za-z]"
    r"|\bstep\s*1\b"
    r"|\b(?:method|instructions|directions)\b\s*:?\s*\d"
    r"|\[(?:recipe|instructions|method)\]",
    re.IGNORECASE | re.DOTALL,
)


def fetch(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    caption = info.get("description") or ""
    return {
        "id": info.get("id"),
        "url": info.get("webpage_url") or url,
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("creator"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "caption": caption,
        "has_ingredients": bool(INGREDIENT_RE.search(caption)),
        "has_steps": bool(STEP_RE.search(caption)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--file", type=Path, help="text file with one URL per line")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in args.file.read_text().splitlines() if l.strip()]
    if not urls:
        parser.error("give at least one URL")

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for url in urls:
        print(f"=== {url}")
        try:
            result = fetch(url)
        except Exception as e:  # spike: report and move on
            print(f"FAILED: {e}\n")
            continue
        ok += 1
        (SAMPLES_DIR / f"{result['id']}.json").write_text(json.dumps(result, indent=2))
        print(f"title:       {result['title']}")
        print(f"author:      {result['author']}")
        print(f"duration:    {result['duration']}s")
        print(f"ingredients: {'yes' if result['has_ingredients'] else 'no'}")
        print(f"steps:       {'yes' if result['has_steps'] else 'no'}")
        print(f"caption:\n{result['caption']}\n")

    print(f"{ok}/{len(urls)} fetched; samples in {SAMPLES_DIR}")
    return 0 if ok == len(urls) else 1


if __name__ == "__main__":
    sys.exit(main())
