import pytest

from recipe_saver.fetch import Post
from recipe_saver.models import Ingredient, Recipe
from recipe_saver.parse.evaluate import load_transcripts, score_spoken
from recipe_saver.parse.llm import LLMClient, _said, _spoken_servings, _strip_keys
from recipe_saver.pipeline import Importer, ImportResult
from recipe_saver.transcribe import _tidy

TRANSCRIPTS = {fx["slug"]: fx for fx in load_transcripts()}
POST = Post(id="", url="", platform="tiktok", title=None, author=None, thumbnail=None, duration=None, caption="")


def test_tidy_drops_music_and_tags():
    raw = "♪ 'Cause you were out of my league ♪\n♪ All the things I believed ♪\n(singing in foreign language)\n[Music]\n"
    assert _tidy(raw) == ""
    assert _tidy(" First, chop the onion.\n Then fry it.\n") == "First, chop the onion. Then fry it."


@pytest.mark.parametrize("transcript", ["", "you", "(upbeat music)"])
def test_no_speech_keeps_caption_result(transcript):
    caption = Recipe(title="Pasta", ingredients=[Ingredient(raw_text="pasta", item="pasta")])
    result = Importer(llm=None).merge(ImportResult(POST, caption), _tidy(transcript))
    assert result.recipe is caption and caption.steps_missing
    assert result.transcript is None
    assert "no speech in audio" in result.notes


def test_strip_keys_removes_leaked_field_names():
    ing = {"item": "onion raw_text", "raw_text": "an onion", "quantity": None}
    assert _strip_keys(ing) == {"item": "onion", "raw_text": "an onion", "quantity": None}


def test_said_drops_invented_numbers_and_filler():
    transcript = "bake it for 8 minutes and 30 seconds then roll four ounce balls".lower()
    assert _said("Bake for 8 minutes and 30 seconds.", transcript)
    assert _said("Roll the beef into 4 oz balls.", transcript)
    assert not _said("Preheat oven to 350°F (175°C).", transcript)
    assert not _said("Enjoy the cookie!", transcript)


@pytest.mark.parametrize("guess, transcript, expected", [
    ("650 calories", "then portion everything out. 650 calories with 66 grams of protein", None),
    ("4", "this one serves 4 people", "4"),
    ("2", "it makes two portions", "2"),
    ("2", "four chicken thighs, about 20 minutes", None),
])
def test_servings_only_when_spoken(guess, transcript, expected):
    assert _spoken_servings(guess, transcript) == expected


@pytest.mark.llm
@pytest.mark.parametrize("slug", sorted(TRANSCRIPTS))
def test_voiceover_only(slug):
    llm = LLMClient()
    if not llm.available():
        pytest.skip(f"no LLM at {llm.base_url}")
    fx = TRANSCRIPTS[slug]
    result = Importer(llm).merge(ImportResult(POST, None), fx["transcript"])
    # A 3B model drops the odd garnish or step; scripts/eval_audio.py reports those
    # strictly. Here, more than one miss is a regression.
    assert len(score_spoken(result.recipe, fx["expected"])) <= 1
    assert result.recipe.title and result.recipe.steps
