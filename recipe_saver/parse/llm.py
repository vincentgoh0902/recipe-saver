"""LLM fallback parser, via any OpenAI-compatible server (Ollama, llama.cpp's llama-server).

Output is constrained to a JSON schema, then checked against the caption so the
model can't add ingredients that aren't in the text.
"""

import json
import os
import re

import httpx

from recipe_saver.models import Ingredient, Recipe
from recipe_saver.parse.ingredients import UNIT_ALIASES
from recipe_saver.parse.text import clean

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5:3b"

RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "servings": {"type": ["string", "null"]},
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "quantity_max": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "item": {"type": "string"},
                    "note": {"type": ["string", "null"]},
                },
                "required": ["raw_text", "quantity", "quantity_max", "unit", "item", "note"],
            },
        },
        "steps": {"type": "array", "items": {"type": "string"}},
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

TITLE_PROMPT = "Give a short recipe name (2 to 6 words) for this caption. Reply with the name only."


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
            unit = (ing.get("unit") or "").lower().rstrip(".") or None
            ingredients.append(Ingredient(
                raw_text=(ing.get("raw_text") or item).strip(),
                item=item,
                quantity=ing.get("quantity"),
                quantity_max=ing.get("quantity_max"),
                unit=UNIT_ALIASES.get(unit, unit),
                note=ing.get("note") or None,
            ))
        return Recipe(
            title=(data.get("title") or "").strip(),
            ingredients=ingredients,
            steps=[s.strip() for s in data.get("steps", []) if s.strip()],
            servings=data.get("servings"),
            parse_method="llm",
        )


def _mentioned(item: str, caption_lower: str) -> bool:
    """Guard against made-up ingredients: some word of the item must be in the caption."""
    words = re.findall(r"[a-z]{3,}", item.lower())
    return any(w in caption_lower for w in words) if words else False
