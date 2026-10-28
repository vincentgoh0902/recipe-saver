"""Display helpers, shared by the templates and the editor (which parses these back)."""

from recipe_saver.models import Ingredient

FRACTIONS = {1 / 8: "1/8", 1 / 4: "1/4", 1 / 3: "1/3", 1 / 2: "1/2", 2 / 3: "2/3", 3 / 4: "3/4"}


def quantity(value: float | None) -> str:
    """1.5 -> '1 1/2', 0.333 -> '1/3', 200.0 -> '200'."""
    if value is None:
        return ""
    whole = int(value)
    frac = next((text for f, text in FRACTIONS.items() if abs(value - whole - f) < 0.01), None)
    if frac:
        return f"{whole} {frac}" if whole else frac
    return f"{round(value, 2):g}"


def ingredient_line(ing: Ingredient) -> str:
    """'200 g flour (sifted)': what the editor shows; parse_ingredient_line reads it back."""
    parts = []
    if ing.quantity is not None:
        q = quantity(ing.quantity)
        if ing.quantity_max is not None:
            q += f"-{quantity(ing.quantity_max)}"
        parts.append(q)
    if ing.unit:
        parts.append(ing.unit)
    parts.append(ing.item)
    line = " ".join(parts)
    return f"{line} ({ing.note})" if ing.note else line
