"""Milestone 3 check: run the full import (caption, then voiceover) and print the recipe.

    uv run spike/import_post.py URL [URL ...]
    uv run spike/import_post.py --file spike/links.txt
"""

import argparse
import sys
import time
from pathlib import Path

from recipe_saver.fetch import FetchError
from recipe_saver.parse.llm import LLMClient
from recipe_saver.pipeline import Importer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--file", type=Path)
    args = ap.parse_args()
    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in args.file.read_text().splitlines() if l.strip()]

    llm = LLMClient()
    importer = Importer(llm if llm.available() else None)
    for url in urls:
        start = time.monotonic()
        try:
            result = importer.run(url)
        except FetchError as e:
            print(f"=== {url} FAILED: {e}\n")
            continue
        r = result.recipe
        print(f"=== {result.post.author} {url} ({time.monotonic() - start:.1f}s)")
        for note in result.notes:
            print(f"  note: {note}")
        if r is None:
            print("  no recipe\n")
            continue
        print(f"  {r.title!r} via {r.parse_method}, {len(r.ingredients)} ingredients, "
              f"{len(r.steps)} steps")
        for ing in r.ingredients:
            print(f"    - {ing.quantity or ''} {ing.unit or ''} {ing.item}".replace("  ", " "))
        for i, step in enumerate(r.steps, 1):
            print(f"    {i}. {step}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
