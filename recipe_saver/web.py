"""FastAPI app: the share endpoint for the iPhone Shortcut, and the web app (Jinja + HTMX)."""

import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from recipe_saver import config
from recipe_saver.db import Store
from recipe_saver.format import ingredient_line, quantity
from recipe_saver.models import Recipe
from recipe_saver.parse.ingredients import parse_ingredient_line
from recipe_saver.parse.llm import LLMClient
from recipe_saver.pipeline import Importer
from recipe_saver.worker import Worker

HERE = Path(__file__).parent
URL_RE = re.compile(r"https?://\S+")
MAX_TEXT = 20_000  # characters; a long caption is ~2,000
MAX_FIELD_LINES = 100
THUMB_RE = re.compile(r"\d+\.jpg")

log = logging.getLogger(__name__)
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["qty"] = quantity
templates.env.filters["ingredient_line"] = ingredient_line


def classify(source: str) -> tuple[str | None, str | None]:
    """A share or paste is either a link (possibly with "Check out this video!" around
    it) or a recipe's text. Returns (url, text)."""
    source = source.strip()
    urls = URL_RE.findall(source)
    if urls and len(source.split()) <= 25:
        return urls[0].rstrip(".,)"), None
    return None, source


def create_app(store: Store | None = None, importer: Importer | None = None,
               start_worker: bool = True) -> FastAPI:
    store = store or Store(config.DB_PATH)
    thumbs_dir = config.THUMBS_DIR

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            llm = LLMClient()
            if not llm.available():
                log.warning("LLM not reachable at %s; imports use the rule parser only", llm.base_url)
            app.state.worker = Worker(store, importer or Importer(llm), thumbs_dir)
            app.state.worker.start()
        yield
        if start_worker:
            app.state.worker.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.worker = None
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def queue(url: str | None, text: str | None) -> int:
        job_id = store.add_job(url=url, text=text)
        if app.state.worker:
            app.state.worker.notify()
        return job_id

    def render(request: Request, name: str, **ctx):
        return templates.TemplateResponse(request, name, ctx)

    def is_htmx(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    # Share endpoint (Shortcut) and health

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/import", status_code=202)
    async def api_import(request: Request):
        """Body: JSON {"url": ...} or {"text": ...}, a form, or plain text. The Shortcut
        sends whatever the share sheet gives it; a link is picked out of it."""
        body = (await request.body())[: MAX_TEXT * 2]
        ctype = request.headers.get("content-type", "")
        try:
            if "json" in ctype:
                data = json.loads(body or b"{}")
                raw = (data.get("url") or data.get("text") or "") if isinstance(data, dict) else ""
                raw = raw if isinstance(raw, str) else ""
            elif "form" in ctype:
                form = await request.form()
                raw = str(form.get("url") or form.get("text") or "")
            else:
                raw = body.decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "unreadable body")
        if not raw.strip():
            raise HTTPException(400, "send a url or recipe text")
        url, text = classify(raw[:MAX_TEXT])
        job_id = queue(url, text)
        return {"job_id": job_id, "status": "queued", "status_url": f"/api/jobs/{job_id}"}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: int):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404)
        return {"id": job.id, "status": job.status, "error": job.error, "recipe_id": job.recipe_id}

    # Web app

    @app.get("/")
    def index(request: Request, q: str = ""):
        recipes = store.search(q[:200])
        total, tags = store.library_stats()
        if is_htmx(request) and request.headers.get("HX-Target") == "results":
            return render(request, "_results.html", recipes=recipes, q=q, total=total, partial=True)
        return render(request, "index.html", recipes=recipes, q=q, total=total, tags=tags,
                      jobs=store.visible_jobs())

    @app.post("/import")
    def import_form(request: Request, source: str = Form("")):
        if source.strip():
            queue(*classify(source[:MAX_TEXT]))
        if is_htmx(request):
            return render(request, "_jobs.html", jobs=store.visible_jobs())
        return RedirectResponse("/", status_code=303)

    @app.get("/jobs")
    def jobs_panel(request: Request):
        # The panel only polls while an import runs, so a poll that finds none running
        # means one just finished: tell the page to refresh the recipe list.
        jobs = store.visible_jobs()
        resp = render(request, "_jobs.html", jobs=jobs)
        if not any(job.active for job in jobs):
            resp.headers["HX-Trigger"] = "recipes-changed"
        return resp

    @app.get("/imports")
    def imports_page(request: Request):
        return render(request, "imports.html", jobs=store.list_jobs())

    @app.post("/jobs/{job_id}/retry")
    def retry(request: Request, job_id: int):
        store.retry_job(job_id)
        if app.state.worker:
            app.state.worker.notify()
        return _back(request)

    @app.post("/jobs/{job_id}/dismiss")
    def dismiss(request: Request, job_id: int):
        store.delete_job(job_id)
        return _back(request)

    def _back(request: Request):
        if is_htmx(request):
            return render(request, "_jobs.html", jobs=store.visible_jobs())
        return RedirectResponse(request.headers.get("referer") or "/", status_code=303)

    @app.get("/recipes/new")
    def new_recipe(request: Request):
        return render(request, "edit.html", stored=None, recipe=Recipe(title="", parse_method="manual"),
                      tags=[])

    @app.post("/recipes")
    def create_recipe(title: str = Form(""), servings: str = Form(""), tags: str = Form(""),
                      ingredients: str = Form(""), steps: str = Form("")):
        recipe = _from_form(title, servings, ingredients, steps, previous=None)
        recipe.parse_method = "manual"
        recipe_id = store.add_recipe(recipe, tags=_tags(tags))
        return RedirectResponse(f"/recipes/{recipe_id}", status_code=303)

    @app.get("/recipes/{recipe_id}")
    def show_recipe(request: Request, recipe_id: int):
        stored = _get(recipe_id)
        return render(request, "recipe.html", stored=stored, recipe=stored.recipe)

    @app.get("/recipes/{recipe_id}/edit")
    def edit_recipe(request: Request, recipe_id: int):
        stored = _get(recipe_id)
        return render(request, "edit.html", stored=stored, recipe=stored.recipe, tags=stored.tags)

    @app.post("/recipes/{recipe_id}")
    def update_recipe(recipe_id: int, title: str = Form(""), servings: str = Form(""),
                      tags: str = Form(""), ingredients: str = Form(""), steps: str = Form("")):
        stored = _get(recipe_id)
        recipe = _from_form(title, servings, ingredients, steps, previous=stored.recipe)
        store.update_recipe(recipe_id, recipe, _tags(tags))
        return RedirectResponse(f"/recipes/{recipe_id}", status_code=303)

    @app.post("/recipes/{recipe_id}/delete")
    def delete_recipe(recipe_id: int):
        thumb = store.delete_recipe(recipe_id)
        if thumb and THUMB_RE.fullmatch(thumb):
            (thumbs_dir / thumb).unlink(missing_ok=True)
        return RedirectResponse("/", status_code=303)

    @app.get("/thumbs/{name}")
    def thumbnail(name: str):
        path = thumbs_dir / name
        if not THUMB_RE.fullmatch(name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=86400"})

    def _get(recipe_id: int):
        stored = store.get_recipe(recipe_id)
        if stored is None:
            raise HTTPException(404, "recipe not found")
        return stored

    return app


def _lines(text: str) -> list[str]:
    lines = [l.strip(" \t-*•") for l in text[:MAX_TEXT].splitlines()]
    return [l for l in lines if l][:MAX_FIELD_LINES]


def _tags(text: str) -> list[str]:
    tags = [t.strip().lstrip("#").lower() for t in text[:500].split(",")]
    return list(dict.fromkeys(t for t in tags if t))[:20]


def _from_form(title: str, servings: str, ingredients: str, steps: str,
               previous: Recipe | None) -> Recipe:
    # An untouched line keeps its stored ingredient, raw_text from the import included.
    unchanged = {ingredient_line(i): i for i in previous.ingredients} if previous else {}
    parsed = [unchanged.get(line) or parse_ingredient_line(line) for line in _lines(ingredients)]
    step_lines = [re.sub(r"^\d{1,2}[.)]\s*", "", l) for l in _lines(steps)]
    return Recipe(
        title=title.strip()[:200] or "Untitled recipe",
        ingredients=[i for i in parsed if i.item],
        steps=step_lines,
        servings=servings.strip()[:50] or None,
        parse_method=previous.parse_method if previous else "manual",
    )
