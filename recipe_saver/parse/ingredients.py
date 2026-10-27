"""Parse one ingredient line ("85g (6 tbsp) butter") into quantity, unit, item, note."""

import re

from recipe_saver.models import Ingredient

FRACTIONS = {"½": 1 / 2, "¼": 1 / 4, "¾": 3 / 4, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 1 / 8}
# Denominators seen in recipes; anything else ("75/25 ground beef") is not a quantity.
DENOMINATORS = {2, 3, 4, 8, 16}

# Order matters: try "1 1/2" and "1/2" before plain "1", or the fraction gets cut off.
NUMBER = r"(?:\d+\s+\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}|\d*[½¼¾⅓⅔⅛]|\d+(?:\.\d+)?)"

UNITS = {
    "g": ["g", "gr", "gram", "grams"],
    "kg": ["kg", "kgs", "kilogram", "kilograms"],
    "ml": ["ml", "millilitre", "millilitres", "milliliter", "milliliters"],
    "l": ["l", "litre", "litres", "liter", "liters"],
    "oz": ["oz", "ounce", "ounces"],
    "lb": ["lb", "lbs", "pound", "pounds"],
    "cup": ["cup", "cups"],
    "tbsp": ["tbsp", "tbs", "tablespoon", "tablespoons"],
    "tsp": ["tsp", "teaspoon", "teaspoons"],
    "clove": ["clove", "cloves"],
    "packet": ["packet", "packets"],
    "handful": ["handful", "handfuls"],
    "tub": ["tub", "tubs"],
    "can": ["can", "cans"],
    "pinch": ["pinch", "pinches"],
    "slice": ["slice", "slices"],
    "bunch": ["bunch", "bunches"],
    "stick": ["stick", "sticks"],
}
UNIT_ALIASES = {alias: unit for unit, aliases in UNITS.items() for alias in aliases}
UNIT = "(?:" + "|".join(sorted(UNIT_ALIASES, key=len, reverse=True)) + r")\b"

LINE_RE = re.compile(
    rf"^~?\s*(?P<q1>{NUMBER})(?:\s*(?:-|–|to)\s*(?P<q2>{NUMBER}))?"
    rf"\s*(?P<unit>{UNIT})?\.?\s*(?:of\s+)?(?P<rest>.*)$",
    re.IGNORECASE,
)


def to_number(text: str) -> float | None:
    """'1 1/2' -> 1.5, '½' -> 0.5, '1.8' -> 1.8; None for things like '75/25'."""
    total = 0.0
    for part in text.split():
        if part[-1] in FRACTIONS:
            total += (float(part[:-1]) if part[:-1] else 0) + FRACTIONS[part[-1]]
        elif "/" in part:
            num, den = part.split("/")
            if int(den) not in DENOMINATORS:
                return None
            total += int(num) / int(den)
        else:
            total += float(part)
    return total


def parse_ingredient_line(text: str, raw_text: str | None = None) -> Ingredient:
    raw = (raw_text or text).strip()
    line = text.replace("|", " ").strip(" -*•,:;")
    line = re.sub(r"\s+", " ", line)

    quantity = quantity_max = unit = None
    rest = line
    match = LINE_RE.match(line)
    if match and (q1 := to_number(match["q1"])) is not None:
        quantity = q1
        quantity_max = to_number(match["q2"]) if match["q2"] else None
        unit = UNIT_ALIASES[match["unit"].lower()] if match["unit"] else None
        rest = match["rest"]

    notes = [n.strip() for n in re.findall(r"\(([^)]*)\)", rest) if n.strip()]
    item = re.sub(r"\([^)]*\)", " ", rest)
    if "," in item:
        item, after = item.split(",", 1)
        if after.strip():
            notes.append(after.strip())
    item = re.sub(r"\s+", " ", item).strip(" -*•,:;")

    return Ingredient(
        raw_text=raw,
        item=item,
        quantity=quantity,
        quantity_max=quantity_max,
        unit=unit,
        note="; ".join(notes) or None,
    )
