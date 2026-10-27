"""Rule-based caption parser.

TikTok captions arrive as one line, so sections are found by markers
("Ingredients:", "[Recipe]", "1. ...") rather than by line breaks. The parser also
says whether it trusts its own result; when it doesn't, the caller falls back to
the LLM.
"""

import re
from dataclasses import dataclass, field

from recipe_saver.models import Ingredient, Recipe
from recipe_saver.parse.ingredients import NUMBER, UNIT, parse_ingredient_line, to_number
from recipe_saver.parse.text import clean, hashtags

ING_MARKER_RE = re.compile(r"\[ingredients?\]|\bingredients?\b\s*:?", re.IGNORECASE)
STEP_MARKER_RE = re.compile(
    r"\[(?:recipe|instructions|method|directions|steps)\]"
    r"|\b(?:instructions|directions|method)\b\s*:?",
    re.IGNORECASE,
)
# "1. Slice" but not "(1) in half" or "1.8KG".
STEP_NUMBER_RE = re.compile(r"(?:^|(?<=\s))(\d{1,2})[.)]\s+(?=[A-Za-z])")
BRACKET_HEADER_RE = re.compile(r"\[([^\]]+)\]")
SERVINGS_RE = re.compile(r"\(?\b(?:serves\s+(\d+)|(\d+)\s*servings?)\b\)?", re.IGNORECASE)
# Not after "-": the 8 in "6-8oz" is the end of a range, not a new ingredient.
QTY_START_RE = re.compile(rf"(?<![\w/.~\-–])~?{NUMBER}(?!\s*%)")
BULLET_RE = re.compile(r"(?:^|(?<=\s))[-*•](?=\s*\S)")

GENERIC_HEADERS = {"ingredients", "ingredient", "recipe", "instructions", "method",
                   "directions", "steps", "sauce"}
# A bare word like this before a quantity list is a sub-heading: "* Pickles Sauce * 3/4 cup mayo".
SECTION_WORDS = {"sauce", "dressing", "marinade", "glaze", "topping", "toppings",
                 "filling", "garnish", "batter", "dough"}
MAX_UNBULLETED_ITEM_WORDS = 3


@dataclass
class RulesResult:
    recipe: Recipe | None
    confident: bool
    title_confident: bool = True
    reasons: list[str] = field(default_factory=list)


def _top_level(text: str, matches) -> list[re.Match]:
    """Keep matches that are not inside parentheses."""
    depth_at, depth = [], 0
    for ch in text:
        depth_at.append(depth)
        depth += (ch == "(") - (ch == ")" and depth > 0)
    return [m for m in matches if depth_at[m.start()] == 0]


def _split_at(text: str, positions: list[int]) -> list[str]:
    bounds = [0, *positions, len(text)]
    parts = (text[a:b].strip(" |") for a, b in zip(bounds, bounds[1:]))
    return [p for p in parts if p.strip(" -*•|")]


def _quantity_starts(text: str) -> list[int]:
    return [
        m.start()
        for m in _top_level(text, QTY_START_RE.finditer(text))
        if to_number(m.group().lstrip("~")) is not None
    ]


def _find_ingredient_run(text: str) -> int | None:
    """Without an 'Ingredients' marker, find where a run of 3+ quantities starts."""
    starts = _quantity_starts(text)
    best, run_start, count = None, None, 0
    for prev, cur in zip([None, *starts], starts):
        if prev is None or cur - prev > 60:
            run_start, count = cur, 0
        count += 1
        if count >= 3 and (best is None or count > best[1]):
            best = (run_start, count)
    return best[0] if best else None


def _split_steps(text: str) -> list[str]:
    expected, cuts = 1, []
    for m in STEP_NUMBER_RE.finditer(text):
        if int(m.group(1)) == expected:
            cuts.append(m)
            expected += 1
    if not cuts:
        return [s.strip() for s in re.split(r"(?<=[.!])\s+", text) if s.strip(" .|")]
    steps = []
    for cur, nxt in zip(cuts, [*cuts[1:], None]):
        step = text[cur.end(): nxt.start() if nxt else len(text)]
        steps.append(re.sub(r"\s+", " ", step.replace("|", " ")).strip(" ."))
    return [s for s in steps if s]


def _split_ingredients(text: str) -> tuple[list[str], bool]:
    """Return ingredient segments and whether the list used bullets."""
    bullets = _top_level(text, BULLET_RE.finditer(text))
    if len(bullets) >= 3:
        return _split_at(text, [m.start() for m in bullets]), True
    return _split_at(text, _quantity_starts(text)), False


def _expand_each(segment: str) -> list[Ingredient] | None:
    """'Seasonings (1 tsp each): paprika, salt' -> one ingredient per item."""
    m = re.match(r"^[-*•\s]*[^()]*\((?P<qty>[^)]*?)\s*each\)\s*:?\s*(?P<items>.+)$", segment)
    if not m:
        return None
    return [
        parse_ingredient_line(f"{m['qty']} {item}", raw_text=segment)
        for item in m["items"].split(",")
        if item.strip()
    ]


def _strip_subheadings(segments: list[str]) -> list[str]:
    has_qty = [bool(_quantity_starts(s.lstrip(" -*•"))) for s in segments]
    out = []
    for i, seg in enumerate(segments):
        words = seg.strip(" -*•").split()
        if (
            len(words) >= 2
            and words[-1].lower() in SECTION_WORDS
            and not has_qty[i]
            and i + 1 < len(segments) and has_qty[i + 1]
            and i > 0 and not has_qty[i - 1]
        ):
            seg = seg.rsplit(words[-1], 1)[0]
        out.append(seg)
    return out


def _title_from(prefix: str, headers: list[str]) -> str:
    for part in re.split(r"[.!?|:]", prefix):
        part = part.strip(" -")
        if re.search(r"[A-Za-z]", part):
            return part[0].upper() + part[1:]
    for header in headers:
        if header.lower() not in GENERIC_HEADERS:
            return header
    return ""


def _title_confident(title: str, recipe: Recipe, tags: list[str]) -> bool:
    """A title is trusted if one of its words shows up in the ingredients or hashtags."""
    haystack = " ".join([*(i.item.lower() for i in recipe.ingredients), *tags])
    words = [w.lower().rstrip("s") for w in re.findall(r"[A-Za-z]{4,}", title)]
    return any(w in haystack for w in words)


def parse(caption: str) -> RulesResult:
    tags = hashtags(caption)
    text = clean(caption)

    servings = None
    if m := SERVINGS_RE.search(text):
        servings = m.group(1) or m.group(2)
        text = text[: m.start()] + " " + text[m.end():]

    headers = [h.strip() for h in BRACKET_HEADER_RE.findall(text)]
    ing_marker = ING_MARKER_RE.search(text)
    if ing_marker:
        title_end, ing_start = ing_marker.start(), ing_marker.end()
    elif (run := _find_ingredient_run(text)) is not None:
        title_end = ing_start = run
    else:
        return RulesResult(None, confident=True, reasons=["no ingredients in caption"])

    step_marker = STEP_MARKER_RE.search(text, ing_start)
    first_step = next((m for m in STEP_NUMBER_RE.finditer(text, ing_start) if m.group(1) == "1"), None)
    candidates = [m for m in (step_marker, first_step) if m]
    if candidates:
        steps_at = min(candidates, key=lambda m: m.start())
        ing_text = text[ing_start: steps_at.start()]
        step_text = text[steps_at.end() if steps_at is step_marker else steps_at.start():]
    else:
        ing_text, step_text = text[ing_start:], ""

    # Remaining [Headers] are sub-sections like "[Sauce]"; drop them from the list.
    ing_text = BRACKET_HEADER_RE.sub(" ", ing_text)
    segments, bulleted = _split_ingredients(ing_text)
    if bulleted:
        segments = _strip_subheadings(segments)

    ingredients: list[Ingredient] = []
    for seg in segments:
        ingredients.extend(_expand_each(seg) or [parse_ingredient_line(seg)])
    ingredients = [i for i in ingredients if i.item]

    title = _title_from(text[:title_end], headers)
    recipe = Recipe(
        title=title,
        ingredients=ingredients,
        steps=_split_steps(BRACKET_HEADER_RE.sub(" ", step_text)) if step_text else [],
        servings=servings,
        parse_method="rules",
    )

    reasons = []
    if len(ingredients) < 2:
        reasons.append("fewer than 2 ingredients found")
    if not bulleted:
        long_items = [i.item for i in ingredients if len(i.item.split()) > MAX_UNBULLETED_ITEM_WORDS]
        if long_items:
            reasons.append(f"unbulleted list with run-on items: {long_items}")
    return RulesResult(
        recipe=recipe,
        confident=not reasons,
        title_confident=bool(title) and _title_confident(title, recipe, tags),
        reasons=reasons,
    )
