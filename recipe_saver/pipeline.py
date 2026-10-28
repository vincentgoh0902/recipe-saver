"""One import, end to end: caption first, then a linked recipe website, then the
voiceover, until there are ingredients and steps (F4-F7). The worker wraps run()."""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from recipe_saver.fetch import Post, download_audio, fetch_post
from recipe_saver.models import Recipe
from recipe_saver.parse import ParseResult, parse_caption
from recipe_saver.parse.llm import LLMClient, LLMError
from recipe_saver.transcribe import Transcriber, TranscribeError
from recipe_saver.website import WebsiteError, fetch_recipe

# Fewer words than this is music, silence or a whisper hallucination ("you").
MIN_SPEECH_WORDS = 15


@dataclass
class ImportResult:
    post: Post
    recipe: Recipe | None
    transcript: str | None = None  # kept with the recipe so imports can be re-run (F9)
    links: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Importer:
    def __init__(self, llm: LLMClient | None = None, transcriber: Transcriber | None = None):
        self.llm = llm
        self.transcriber = transcriber or Transcriber()

    def run(self, url: str, on_status: Callable[[str], None] = lambda status: None,
            post: Post | None = None) -> ImportResult:
        """post: already fetched by the caller (the worker does, to spot re-shares)."""
        on_status("fetching")
        post = post or fetch_post(url)
        on_status("parsing")
        parsed = parse_caption(post.caption, self.llm)
        result = ImportResult(post, parsed.recipe, links=parsed.links, notes=parsed.notes)
        if _complete(parsed.recipe):
            return result

        # A linked recipe page is the creator's full recipe, quantities included, so
        # it replaces whatever the caption had.
        for link in parsed.links:
            try:
                result.recipe = fetch_recipe(link)
                result.notes.append(f"recipe from {link}")
                return result
            except WebsiteError as e:
                result.notes.append(str(e))

        on_status("transcribing")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                transcript = self.transcriber.transcribe(download_audio(url, Path(tmp)))
        except TranscribeError as e:
            result.notes.append(f"audio skipped: {e}")
            return result
        return self.merge(result, transcript, on_status)

    def run_text(self, text: str) -> ParseResult:
        """Paste-text import (F13): the same caption parser, no fetching."""
        return parse_caption(text, self.llm)

    def merge(self, result: ImportResult, transcript: str,
              on_status: Callable[[str], None] = lambda status: None) -> ImportResult:
        """Add what the voiceover says to a caption-only result. Split out of run() so
        it can be tested on saved transcripts without network access."""
        if len(transcript.split()) < MIN_SPEECH_WORDS:
            result.notes.append("no speech in audio")
            return result
        result.transcript = transcript
        if self.llm is None:
            result.notes.append("transcript not parsed: no LLM configured")
            return result

        on_status("parsing")
        caption = result.recipe
        has_ingredients = bool(caption and caption.ingredients)
        try:
            spoken = self.llm.parse_transcript(transcript, ingredients=not has_ingredients)
        except LLMError as e:
            result.notes.append(f"transcript not parsed: {e}")
            return result

        if not has_ingredients:
            result.recipe = spoken
            spoken.title = spoken.title or (caption.title if caption else "") or self._title(transcript)
            return result
        # F7: ingredients and quantities from the caption, steps from the voiceover.
        if not caption.steps:
            caption.steps = spoken.steps
            caption.parse_method = "merged"
        caption.servings = caption.servings or spoken.servings
        caption.title = caption.title or spoken.title
        return result

    def _title(self, text: str) -> str:
        try:
            return self.llm.title(text) or ""
        except LLMError:
            return ""


def _complete(recipe: Recipe | None) -> bool:
    return bool(recipe and recipe.ingredients and recipe.steps)
