"""Score a parse against a hand-written expected result (tests/fixtures/captions)."""

import json
from pathlib import Path

from recipe_saver.parse import ParseResult

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "captions"


def load_fixtures() -> list[dict]:
    return [json.loads(p.read_text()) | {"slug": p.stem} for p in sorted(FIXTURES_DIR.glob("*.json"))]


def _matches(ing, want) -> bool:
    qty, unit, item = want
    if item not in ing.item.lower():
        return False
    if qty is not None:
        low, high = qty if isinstance(qty, list) else (qty, None)
        if ing.quantity is None or abs(ing.quantity - low) > 0.01:
            return False
        if high is not None and (ing.quantity_max is None or abs(ing.quantity_max - high) > 0.01):
            return False
    return unit == "*" or ing.unit == unit


def score(result: ParseResult, expected: dict) -> list[str]:
    """Return a list of problems; empty means the parse is correct."""
    if not expected["has_recipe"]:
        problems = [] if result.recipe is None else ["found a recipe where there is none"]
        if expected.get("links") and result.links != expected["links"]:
            problems.append(f"links {result.links} != {expected['links']}")
        return problems
    if result.recipe is None:
        return ["no recipe found"]

    problems = []
    remaining = list(result.recipe.ingredients)
    for want in expected["ingredients"]:
        hit = next((ing for ing in remaining if _matches(ing, want)), None)
        if hit:
            remaining.remove(hit)
        else:
            problems.append(f"missing or wrong: {want}")
    problems += [f"unexpected: {ing.raw_text!r} -> {ing.item!r}" for ing in remaining]
    if len(result.recipe.steps) != expected["steps"]:
        problems.append(f"{len(result.recipe.steps)} steps, expected {expected['steps']}")
    if "servings" in expected and result.recipe.servings != expected["servings"]:
        problems.append(f"servings {result.recipe.servings!r}, expected {expected['servings']!r}")
    return problems


TRANSCRIPTS_DIR = FIXTURES_DIR.parent / "transcripts"


def load_transcripts() -> list[dict]:
    return [json.loads(p.read_text()) | {"slug": p.stem} for p in sorted(TRANSCRIPTS_DIR.glob("*.json"))]


def score_spoken(recipe, expected: dict) -> list[str]:
    """Voiceover parse: every ingredient the speaker names, and steps that cover each key
    action they describe (how the model splits them into steps doesn't matter)."""
    if recipe is None:
        return ["no recipe found"]
    items = [ing.item.lower() for ing in recipe.ingredients]
    problems = [f"missing: {want}" for want in expected["ingredients"]
                if not any(want in item for item in items)]
    return problems + score_steps(recipe, expected)


def score_steps(recipe, expected: dict) -> list[str]:
    steps = " ".join(recipe.steps).lower()
    return [f"no step mentions: {action}" for action in expected["actions"] if action not in steps]
