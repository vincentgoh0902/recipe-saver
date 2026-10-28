"""LLM fallback parser, via any OpenAI-compatible server (Ollama, llama.cpp's llama-server).

Output is constrained to a JSON schema, then checked against the caption so the
model can't add ingredients that aren't in the text.
"""

import json
import os
import re

import httpx

from recipe_saver.models import Ingredient, Recipe
from recipe_saver.parse.ingredients import UNIT_ALIASES, parse_ingredient_line
from recipe_saver.parse.text import clean

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5:3b"
# A long recipe is well under this. Small models under a JSON schema can loop on an
# array forever; the cap turns that into truncated JSON (an LLMError) instead of a hang.
MAX_TOKENS = 2048

# maxLength/maxItems bound each string and list: a small model can get stuck repeating
# ("flour (flour) (flour) ..."), and the grammar then forces the value to close.
RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "servings": {"type": ["string", "null"], "maxLength": 20},
        "ingredients": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string", "maxLength": 120},
                    "quantity": {"type": ["number", "null"]},
                    "quantity_max": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"], "maxLength": 20},
                    "item": {"type": "string", "maxLength": 60},
                    "note": {"type": ["string", "null"], "maxLength": 120},
                },
                "required": ["raw_text", "quantity", "quantity_max", "unit", "item", "note"],
            },
        },
        "steps": {"type": "array", "maxItems": 25, "items": {"type": "string", "maxLength": 400}},
    },
    "required": ["title", "servings", "ingredients", "steps"],
}

SYSTEM_PROMPT = """You extract recipes from social media captions.
Rules:
- Use only what the caption says. Never invent ingredients, quantities or steps.
- One entry per ingredient. A caption may run ingredients together on one line
  ("30g butter vanilla extract" is two ingredients: "30g butter" and "vanilla extract").
- quantity is a number (convert fractions: 1/2 -> 0.5). For ranges like 300-400g,
  quantity=300 and quantity_max=400. null if no quantity is given.
- unit is a short unit (g, kg, ml, l, oz, lb, cup, tbsp, tsp, clove, packet, handful) or null.
- item is the ingredient name only. Brands, prep and alternatives go in note.
- raw_text is the ingredient exactly as written in the caption.
- steps are the method steps in order. If the caption has no steps, return [].
- title is a short recipe name (2 to 6 words), not a hook like "This was dangerous"."""

TRANSCRIPT_PROMPT = """You extract recipes from the voiceover of a cooking video.
The transcript is speech-to-text, so expect misheard words and no punctuation.
Rules:
- Use only what the speaker says. Never invent ingredients, quantities or steps.
- ingredients: every ingredient the speaker mentions anywhere, one entry each, including
  ones listed inside a sentence ("the sauce is mayo, ketchup and mustard" is three
  ingredients), seasonings, and what they serve it with. Never a combined entry like
  "burger sauce". raw_text is the phrase as spoken ("a ton of garlic powder").
  quantity and unit only when a number is said ("four ounce balls" -> 4, oz);
  otherwise null. item is the ingredient name only.
- steps: the cooking actions the speaker describes, in order, as short instructions
  ("Season the chicken thighs with salt, pepper and garlic powder."). Do not add steps
  they don't describe (no preheating, temperatures or times they never say). Skip
  intros, opinions, tasting, "enjoy" and calls to action.
- title is a short recipe name (2 to 6 words), or "" if the dish is never named."""

# Small models still skip some ingredients while also writing steps, so a second,
# ingredients-only call adds whatever the first one missed.
SPOKEN_INGREDIENTS_PROMPT = """List every food ingredient the speaker mentions in this
cooking video transcript, in the order they are mentioned.
- Include ingredients named inside sentences ("the sauce is mayo, ketchup and mustard"
  is three ingredients), oils, seasonings, toppings, and what they serve it with.
- One entry per ingredient, never a combined entry like "burger sauce".
- raw_text is the phrase as spoken ("a ton of garlic powder"). item is the ingredient
  name only ("garlic powder"). quantity and unit only when a number is said ("four ounce
  balls" -> 4, oz); otherwise null."""

SPOKEN_INGREDIENTS_SCHEMA = {
    "type": "object",
    "properties": {"ingredients": RECIPE_SCHEMA["properties"]["ingredients"]},
    "required": ["ingredients"],
}

NUMBER_WORDS = {
    "a half": 0.5, "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "twenty": 20,
}

TITLE_PROMPT = """Give a short recipe name (2 to 6 words) for this text. If it names the
dish, copy that name in the same word order and add nothing. Reply with the name only.
Example: 'POV: the "date night" Honey Garlic Salmon everyone wants' -> Honey Garlic Salmon"""


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 120):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def available(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/models", timeout=3).is_success
        except httpx.HTTPError:
            return False

    def _chat(self, system: str, user: str, schema: dict | None = None) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "recipe", "schema": schema, "strict": True},
            }
        try:
            resp = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise LLMError(f"LLM request failed: {e}") from e

    def _json(self, system: str, user: str, schema: dict, attempts: int = 2) -> dict:
        # Runaway output is cut off by MAX_TOKENS as invalid JSON; it's intermittent,
        # so one retry usually succeeds.
        for attempt in range(attempts):
            try:
                return json.loads(self._chat(system, user, schema))
            except json.JSONDecodeError as e:
                if attempt == attempts - 1:
                    raise LLMError(f"LLM returned invalid JSON: {e}") from e

    def title(self, caption: str) -> str | None:
        text = self._chat(TITLE_PROMPT, clean(caption)).strip().strip('"').strip()
        return text if 0 < len(text.split()) <= 8 else None

    def parse(self, caption: str) -> Recipe:
        content = self._chat(SYSTEM_PROMPT, f"Caption:\n{clean(caption)}", RECIPE_SCHEMA)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM returned invalid JSON: {e}") from e

        caption_lower = caption.lower()
        ingredients = []
        for ing in data.get("ingredients", []):
            item = (ing.get("item") or "").strip()
            if not item or not _mentioned(item, caption_lower):
                continue
            ingredients.append(_check_against_raw(ing, item))
        ingredients = _recover_skipped(clean(caption), ingredients)
        return Recipe(
            title=(data.get("title") or "").strip(),
            ingredients=ingredients,
            steps=[s.strip() for s in data.get("steps", []) if s.strip()],
            servings=data.get("servings"),
            parse_method="llm",
        )

    def parse_transcript(self, transcript: str, ingredients: bool = True) -> Recipe:
        """Voiceover -> recipe. Quantities are rarely spoken, so most come back null;
        when a caption has the ingredient list the pipeline uses that instead (F7) and
        passes ingredients=False to skip the extra ingredients call."""
        user = f"Transcript:\n{transcript}"
        data = self._json(TRANSCRIPT_PROMPT, user, RECIPE_SCHEMA)
        extra = {}
        if ingredients:
            extra = self._json(SPOKEN_INGREDIENTS_PROMPT, user, SPOKEN_INGREDIENTS_SCHEMA)

        text_lower = transcript.lower()
        ingredients = []
        for ing in data.get("ingredients", []) + extra.get("ingredients", []):
            item = (ing.get("item") or "").strip()
            if not item or not _mentioned(item, text_lower):
                continue
            raw = (ing.get("raw_text") or item).strip()
            if _looping(item):
                item = parse_ingredient_line(raw).item
                if not item or not _mentioned(item, text_lower):
                    continue
            spoken = _spoken_number(raw)
            quantity = ing.get("quantity") if spoken is not None else None
            unit = (ing.get("unit") or "").lower().rstrip(".") or None
            ingredients.append(Ingredient(
                raw_text=raw,
                item=item,
                quantity=quantity,
                quantity_max=ing.get("quantity_max") if quantity is not None else None,
                unit=UNIT_ALIASES.get(unit) if quantity is not None else None,
                note=ing.get("note") or None,
            ))
        return Recipe(
            title=(data.get("title") or "").strip(),
            ingredients=_dedupe(ingredients),
            steps=[s.strip() for s in data.get("steps", []) if _said(s, text_lower)],
            servings=data.get("servings"),
            parse_method="llm",
        )


def _looping(text: str) -> bool:
    """A string the model got stuck repeating: some word appears 3 or more times."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    return any(words.count(w) >= 3 for w in set(words))


def _said(step: str, transcript_lower: str) -> bool:
    """Drop steps the model made up. Invented steps tend to carry numbers ("Preheat oven
    to 350°F"), so every number in a step must appear in the transcript; filler like
    "Enjoy!" has no cooking action at all."""
    step = step.strip()
    if not step or re.match(r"(enjoy|serve and enjoy)\b", step, re.IGNORECASE):
        return False
    spoken = {str(int(v)): w for w, v in NUMBER_WORDS.items() if v >= 1}
    return all(n in transcript_lower or re.search(rf"\b{spoken.get(n, n)}\b", transcript_lower)
               for n in re.findall(r"\d+", step))


def _spoken_number(text: str) -> float | None:
    """A quantity the speaker actually said: digits ("75/25" aside) or a number word."""
    ruled = parse_ingredient_line(text)
    if ruled.quantity is not None:
        return ruled.quantity
    lower = text.lower()
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", lower):
            return value
    return None


def _dedupe(ingredients: list[Ingredient]) -> list[Ingredient]:
    # Speakers repeat ingredients ("more chipotle seasoning"); keep the first mention.
    seen, result = set(), []
    for ing in ingredients:
        key = ing.item.lower()
        if key not in seen:
            seen.add(key)
            result.append(ing)
    return result


def _check_against_raw(ing: dict, item: str) -> Ingredient:
    """Small models copy raw_text faithfully but often drop the quantity ("2 eggs" ->
    quantity null, item "egg") or invent units ("juiced lime"). Re-parse raw_text with
    the rule parser and trust it for the numbers."""
    raw = (ing.get("raw_text") or item).strip()
    ruled = parse_ingredient_line(raw)
    quantity = ing.get("quantity")
    quantity_max = ing.get("quantity_max")
    if quantity is None or (ruled.quantity is not None and ruled.quantity != quantity):
        quantity, quantity_max = ruled.quantity, ruled.quantity_max
    unit = (ing.get("unit") or "").lower().rstrip(".") or None
    unit = UNIT_ALIASES.get(unit) or ruled.unit
    # Widen a clipped item back to whole words as written: "egg" in "2 eggs" -> "eggs".
    whole = re.search(rf"\w*{re.escape(item)}\w*", raw, re.IGNORECASE)
    return Ingredient(
        raw_text=raw,
        item=whole[0] if whole else item,
        quantity=quantity,
        quantity_max=quantity_max,
        unit=unit,
        note=ing.get("note") or None,
    )


def _recover_skipped(text: str, ingredients: list[Ingredient]) -> list[Ingredient]:
    """Text left between two ingredients the model found is an ingredient it skipped
    ("40g Light Grated Cheese Diced Chives" -> only the cheese came back)."""
    lower = text.lower()
    result, prev_end, pos = [], None, 0
    for ing in ingredients:
        start = lower.find(ing.raw_text.lower(), pos)
        if start != -1:
            if prev_end is not None:
                gap = re.sub(r"\([^)]*\)", " ", text[prev_end:start]).strip(" -*•,:;|")
                if re.search(r"[a-z]{3,}", gap, re.IGNORECASE) and len(gap.split()) <= 5:
                    result.append(parse_ingredient_line(gap))
            prev_end = pos = start + len(ing.raw_text)
        result.append(ing)
    return result


def _mentioned(item: str, caption_lower: str) -> bool:
    """Guard against made-up ingredients: some word of the item must be in the caption."""
    words = re.findall(r"[a-z]{3,}", item.lower())
    return any(w in caption_lower for w in words) if words else False
