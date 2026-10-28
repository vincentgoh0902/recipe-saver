"""Milestone 3 check: do saved voiceover transcripts turn into recipes with steps?

    uv run scripts/eval_audio.py [-v]

Two cases per transcript (needs the LLM):
  voiceover-only    caption has no recipe; everything comes from the transcript
  ingredients-only  caption ingredients (steps removed) + steps from the transcript (F7)
"""

import argparse
import copy
import sys
import time

from recipe_saver.fetch import Post
from recipe_saver.parse import parse_caption
from recipe_saver.parse.evaluate import load_fixtures, load_transcripts, score_spoken, score_steps
from recipe_saver.parse.llm import LLMClient
from recipe_saver.pipeline import ImportResult, Importer

POST = Post(id="", url="", title=None, author=None, thumbnail=None, duration=None, caption="")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="print parsed recipes")
    args = ap.parse_args()

    llm = LLMClient()
    if not llm.available():
        print(f"LLM not reachable at {llm.base_url}")
        return 1
    importer = Importer(llm)
    captions = {fx["slug"]: fx for fx in load_fixtures()}

    passed = total = 0
    for fx in load_transcripts():
        cases = [("voiceover-only", None)]
        caption = captions.get(fx["slug"])
        if caption and caption["expected"]["has_recipe"]:
            cases.append(("ingredients-only", caption))
        for case, cap in cases:
            recipe = None
            if cap:
                recipe = copy.deepcopy(parse_caption(cap["caption"], llm).recipe)
                recipe.steps = []
                want_ingredients = [(i.quantity, i.unit, i.item) for i in recipe.ingredients]
            start = time.monotonic()
            result = importer.merge(ImportResult(POST, recipe), fx["transcript"])
            secs = time.monotonic() - start
            r = result.recipe
            if cap:
                problems = [] if r and r.parse_method == "merged" else ["not merged"]
                if r and [(i.quantity, i.unit, i.item) for i in r.ingredients] != want_ingredients:
                    problems.append("caption ingredients changed")
                if r:
                    problems += score_steps(r, fx["expected"])
            else:
                problems = score_spoken(r, fx["expected"])
            total += 1
            passed += not problems
            print(f"{'PASS' if not problems else 'FAIL'}  {fx['slug']:22} {case:17} {secs:5.1f}s  "
                  f"{r.title if r else ''!r}, {len(r.steps) if r else 0} steps")
            for p in problems:
                print(f"        {p}")
            if args.verbose and r:
                print("        " + ", ".join(i.item for i in r.ingredients))
                for i, step in enumerate(r.steps, 1):
                    print(f"        {i}. {step}")
    print(f"\n{passed}/{total} correct")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
