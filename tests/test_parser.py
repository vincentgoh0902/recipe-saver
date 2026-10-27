import pytest

from recipe_saver.parse import parse_caption, rules
from recipe_saver.parse.evaluate import load_fixtures, score
from recipe_saver.parse.llm import LLMClient

FIXTURES = {fx["slug"]: fx for fx in load_fixtures()}
# Captions the rules can't split reliably; these must be handed to the LLM.
NEEDS_LLM = {"diner-pancakes", "chipotle-quesadillas"}


@pytest.mark.parametrize("slug", sorted(set(FIXTURES) - NEEDS_LLM))
def test_rules_parse_correctly(slug):
    fx = FIXTURES[slug]
    assert score(parse_caption(fx["caption"]), fx["expected"]) == []


@pytest.mark.parametrize("slug", sorted(NEEDS_LLM))
def test_rules_hand_off_to_llm(slug):
    assert not rules.parse(FIXTURES[slug]["caption"]).confident


@pytest.mark.llm
@pytest.mark.parametrize("slug", sorted(NEEDS_LLM))
def test_llm_parses_correctly(slug):
    llm = LLMClient()
    if not llm.available():
        pytest.skip(f"no LLM at {llm.base_url}")
    fx = FIXTURES[slug]
    assert score(parse_caption(fx["caption"], llm), fx["expected"]) == []
