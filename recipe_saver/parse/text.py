"""Caption cleanup shared by the rule-based and LLM parsers."""

import re

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"#(\w+)")
MENTION_RE = re.compile(r"@\S+")
# Creators use emoji as punctuation ("Spaghetti 🔥 Quick, easy..."), so they become
# a "|" separator rather than disappearing; the title finder splits on it.
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")


def extract_links(caption: str) -> list[str]:
    return [url.rstrip(".,)") for url in URL_RE.findall(caption)]


def hashtags(caption: str) -> list[str]:
    return [tag.lower() for tag in HASHTAG_RE.findall(caption)]


def clean(caption: str) -> str:
    text = URL_RE.sub(" ", caption)
    text = HASHTAG_RE.sub(" ", text)
    text = MENTION_RE.sub(" ", text)
    text = EMOJI_RE.sub(" | ", text)
    text = re.sub(r"[®™]", "", text)
    text = re.sub(r"_{2,}|(?:\s\.){2,}", " ", text)
    return re.sub(r"\s+", " ", text).strip()
