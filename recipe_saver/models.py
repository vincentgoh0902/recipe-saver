from dataclasses import dataclass, field


@dataclass
class Ingredient:
    raw_text: str
    item: str
    quantity: float | None = None
    quantity_max: float | None = None  # upper end of a range like "300-400g"
    unit: str | None = None
    note: str | None = None


@dataclass
class Recipe:
    title: str
    ingredients: list[Ingredient] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    servings: str | None = None
    parse_method: str = "rules"  # rules / llm / website / merged / manual

    @property
    def steps_missing(self) -> bool:
        return not self.steps
