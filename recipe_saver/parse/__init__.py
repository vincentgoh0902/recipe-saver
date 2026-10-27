"""Caption -> Recipe. Rules first; the LLM only when the rules don't trust their result."""

from dataclasses import dataclass, field

from recipe_saver.models import Recipe
from recipe_saver.parse import rules
from recipe_saver.parse.llm import LLMClient, LLMError
from recipe_saver.parse.text import extract_links


@dataclass
class ParseResult:
    recipe: Recipe | None
    links: list[str] = field(default_factory=list)  # recipe websites to try next (F6)
    notes: list[str] = field(default_factory=list)  # why a path was taken, for the job log


def parse_caption(caption: str, llm: LLMClient | None = None) -> ParseResult:
    links = extract_links(caption)
    result = rules.parse(caption)
    notes = list(result.reasons)

    if result.recipe is None:
        return ParseResult(None, links, notes)

    recipe = result.recipe
    if not result.confident:
        if llm is None:
            notes.append("rules result kept: no LLM configured")
            return ParseResult(recipe, links, notes)
        try:
            return ParseResult(llm.parse(caption), links, notes)
        except LLMError as e:
            notes.append(f"rules result kept: {e}")
            return ParseResult(recipe, links, notes)

    if not result.title_confident and llm is not None:
        try:
            recipe.title = llm.title(caption) or recipe.title
        except LLMError as e:
            notes.append(f"title kept: {e}")
    return ParseResult(recipe, links, notes)
