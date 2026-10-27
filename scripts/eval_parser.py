"""Milestone 2 check: how many sample captions parse correctly?

    uv run scripts/eval_parser.py            # rules + LLM (LLM_BASE_URL, default Ollama)
    uv run scripts/eval_parser.py --no-llm   # rules only
"""

import argparse
import time

from recipe_saver.parse import parse_caption
from recipe_saver.parse.evaluate import load_fixtures, score
from recipe_saver.parse.llm import LLMClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print parsed recipes")
    args = ap.parse_args()

    llm = None
    if not args.no_llm:
        llm = LLMClient()
        if not llm.available():
            print(f"LLM not reachable at {llm.base_url}; running rules only\n")
            llm = None

    fixtures = load_fixtures()
    passed = 0
    for fx in fixtures:
        start = time.monotonic()
        result = parse_caption(fx["caption"], llm)
        secs = time.monotonic() - start
        problems = score(result, fx["expected"])
        passed += not problems
        method = result.recipe.parse_method if result.recipe else "-"
        title = result.recipe.title if result.recipe else ""
        print(f"{'PASS' if not problems else 'FAIL'}  {fx['slug']:26} {method:6} {secs:5.1f}s  {title}")
        for note in result.notes:
            print(f"        note: {note}")
        for p in problems:
            print(f"        {p}")
        if args.verbose and result.recipe:
            for ing in result.recipe.ingredients:
                print(f"          - {ing.quantity} {ing.quantity_max or ''} {ing.unit} | {ing.item} | {ing.note}")
    print(f"\n{passed}/{len(fixtures)} correct (target: 8/10)")


if __name__ == "__main__":
    main()
