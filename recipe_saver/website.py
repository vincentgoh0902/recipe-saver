"""Recipe websites linked from a caption (F6): read their schema.org Recipe data.
No AI needed; most recipe sites publish it for search engines."""

import httpx
from recipe_scrapers import scrape_html

from recipe_saver.models import Recipe
from recipe_saver.parse.ingredients import parse_ingredient_line

# Some sites refuse requests without a browser-like user agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}


class WebsiteError(Exception):
    pass


def fetch_recipe(url: str, timeout: float = 20) -> Recipe:
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise WebsiteError(f"could not fetch {url}: {e}") from e
    try:
        scraper = scrape_html(resp.text, org_url=str(resp.url), supported_only=False)
        lines = scraper.ingredients()
        steps = scraper.instructions_list()
    except Exception as e:  # recipe-scrapers raises many types for pages without a recipe
        raise WebsiteError(f"no recipe data on {url}: {type(e).__name__}") from e
    if not lines or not steps:
        raise WebsiteError(f"no recipe data on {url}")
    return Recipe(
        title=_safe(scraper.title) or "",
        ingredients=[parse_ingredient_line(line) for line in lines if line.strip()],
        steps=[s.strip() for s in steps if s.strip()],
        servings=_safe(scraper.yields),
        parse_method="website",
    )


def _safe(method) -> str | None:
    try:
        return method() or None
    except Exception:
        return None
