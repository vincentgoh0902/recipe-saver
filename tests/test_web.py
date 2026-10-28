import pytest
from fastapi.testclient import TestClient

from recipe_saver import worker as worker_module
from recipe_saver.db import Store
from recipe_saver.fetch import FetchError, Post
from recipe_saver.models import Ingredient, Recipe
from recipe_saver.pipeline import Importer, ImportResult
from recipe_saver.web import classify, create_app
from recipe_saver.worker import Worker

POST = Post(id="123", url="https://www.tiktok.com/@cook/video/123", platform="tiktok",
            title=None, author="cook", thumbnail=None, duration=30, caption="caption text")


class FakeImporter(Importer):
    """Pipeline stand-in: returns a fixed recipe, or none."""

    def __init__(self, recipe: Recipe | None):
        self.recipe = recipe
        self.llm = None  # paste-text imports use the real rule parser

    def run(self, url, on_status=lambda s: None, post=None):
        on_status("parsing")
        return ImportResult(post, self.recipe, transcript="spoken words", notes=["from fake"])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "recipes.db")


@pytest.fixture
def client(store):
    return TestClient(create_app(store, start_worker=False))


def pasta() -> Recipe:
    return Recipe(
        title="Tomato Pasta",
        ingredients=[Ingredient("200g pasta", "pasta", 200, None, "g"),
                     Ingredient("2 tbsp tomato paste", "tomato paste", 2, None, "tbsp")],
        steps=["Boil the pasta.", "Stir in the paste."],
    )


def run_jobs(store, monkeypatch, recipe=None, fetch=None):
    monkeypatch.setattr(worker_module, "fetch_post", fetch or (lambda url: POST))
    w = Worker(store, FakeImporter(recipe), thumbs_dir=store.path.parent / "thumbs")
    while job := store.claim_job():
        w.process(job.id, job.url, job.text)


@pytest.mark.parametrize("source, expected", [
    ("https://vt.tiktok.com/ZSgbGunfU/", ("https://vt.tiktok.com/ZSgbGunfU/", None)),
    ("Check out this video! https://vt.tiktok.com/abc/", ("https://vt.tiktok.com/abc/", None)),
    ("Pasta\n200g pasta\n1 tbsp oil", (None, "Pasta\n200g pasta\n1 tbsp oil")),
])
def test_classify(source, expected):
    assert classify(source) == expected


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("kwargs", [
    {"json": {"url": "https://vt.tiktok.com/abc/"}},
    {"data": {"url": "https://vt.tiktok.com/abc/"}},
    {"content": "https://vt.tiktok.com/abc/", "headers": {"content-type": "text/plain"}},
])
def test_api_import_accepts_json_form_and_text(client, store, kwargs):
    resp = client.post("/api/import", **kwargs)
    assert resp.status_code == 202
    job = store.get_job(resp.json()["job_id"])
    assert job.url == "https://vt.tiktok.com/abc/" and job.status == "queued"


@pytest.mark.parametrize("kwargs", [{"json": {}}, {"json": ["x"]}, {"json": {"url": 5}}, {"content": ""}])
def test_api_import_rejects_empty(client, kwargs):
    assert client.post("/api/import", **kwargs).status_code == 400


def test_import_saves_recipe_and_skips_reshare(client, store, monkeypatch):
    first = client.post("/api/import", json={"url": "https://vt.tiktok.com/abc/"}).json()["job_id"]
    second = client.post("/api/import", json={"url": "https://vt.tiktok.com/abc/"}).json()["job_id"]
    run_jobs(store, monkeypatch, recipe=pasta())

    done = client.get(f"/api/jobs/{first}").json()
    assert done["status"] == "done"
    saved = store.get_recipe(done["recipe_id"])
    assert saved.recipe.title == "Tomato Pasta"
    assert saved.source.transcript == "spoken words" and saved.source.raw_caption == "caption text"
    assert client.get(f"/api/jobs/{second}").json()["recipe_id"] == done["recipe_id"]
    assert store.get_job(second).notes == ["already saved"]
    assert len(store.search()) == 1


def test_failed_import_is_listed_and_retried(client, store, monkeypatch):
    job_id = client.post("/api/import", json={"url": "https://vt.tiktok.com/abc/"}).json()["job_id"]
    run_jobs(store, monkeypatch, recipe=None)
    assert store.get_job(job_id).status == "failed"
    assert "no recipe found" in client.get("/").text

    client.post(f"/jobs/{job_id}/retry")
    run_jobs(store, monkeypatch, recipe=pasta())
    assert store.get_job(job_id).status == "done"


def test_fetch_error_fails_job(client, store, monkeypatch):
    def broken(url):
        raise FetchError("could not fetch post: 404")
    job_id = client.post("/api/import", json={"url": "https://vt.tiktok.com/abc/"}).json()["job_id"]
    run_jobs(store, monkeypatch, fetch=broken)
    assert store.get_job(job_id).error == "could not fetch post: 404"


def test_paste_text_import(client, store, monkeypatch):
    text = "Garlic Butter Toast\nIngredients:\n- 2 slices bread\n- 1 tbsp butter\n- 1 clove garlic"
    client.post("/import", data={"source": text})
    run_jobs(store, monkeypatch)
    (saved,) = store.search()
    assert saved.recipe.title == "Garlic Butter Toast"
    assert [i.item for i in saved.recipe.ingredients] == ["bread", "butter", "garlic"]
    assert saved.recipe.steps_missing


def test_search_by_title_ingredient_and_tag(client, store):
    store.add_recipe(pasta(), tags=["dinner"])
    store.add_recipe(Recipe("Pancakes", [Ingredient("flour", "flour")], ["Mix."]), tags=["breakfast"])
    titles = lambda q: [s.recipe.title for s in store.search(q)]
    assert titles("tomato") == ["Tomato Pasta"]
    assert titles("flour") == ["Pancakes"]
    assert titles("dinner") == ["Tomato Pasta"]
    assert titles("pasta dinner") == ["Tomato Pasta"]
    assert titles("") == ["Pancakes", "Tomato Pasta"]
    resp = client.get("/?q=flour", headers={"HX-Request": "true", "HX-Target": "results"})
    assert "Pancakes" in resp.text and "Tomato Pasta" not in resp.text and "<html" not in resp.text


def test_edit_round_trip_keeps_untouched_ingredients(client, store):
    recipe_id = store.add_recipe(pasta())
    form = client.get(f"/recipes/{recipe_id}/edit").text
    assert "200 g pasta\n2 tbsp tomato paste" in form

    resp = client.post(f"/recipes/{recipe_id}", data={
        "title": "Tomato Pasta", "servings": "2", "tags": "Dinner, #quick",
        "ingredients": "200 g pasta\n3 tbsp tomato paste (double)\n1 tsp salt",
        "steps": "1. Boil the pasta.\n2. Stir in the paste.\n3. Salt it.",
    }, follow_redirects=False)
    assert resp.status_code == 303

    saved = store.get_recipe(recipe_id)
    pasta_ing, paste, salt = saved.recipe.ingredients
    assert pasta_ing.raw_text == "200g pasta"  # untouched: original text kept
    assert (paste.quantity, paste.unit, paste.item, paste.note) == (3, "tbsp", "tomato paste", "double")
    assert (salt.quantity, salt.unit, salt.item) == (1, "tsp", "salt")
    assert saved.recipe.steps == ["Boil the pasta.", "Stir in the paste.", "Salt it."]
    assert saved.tags == ["dinner", "quick"] and saved.recipe.servings == "2"


def test_manual_recipe_and_delete(client, store):
    resp = client.post("/recipes", data={"title": "Toast", "ingredients": "1 slice bread",
                                         "steps": ""}, follow_redirects=False)
    recipe_id = int(resp.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/recipes/{recipe_id}").text
    assert "Toast" in page and "No steps were found" in page
    assert store.get_recipe(recipe_id).recipe.parse_method == "manual"

    client.post(f"/recipes/{recipe_id}/delete")
    assert client.get(f"/recipes/{recipe_id}").status_code == 404


def test_thumbnail_paths_are_checked(client):
    assert client.get("/thumbs/..%2Frecipes.db").status_code == 404
    assert client.get("/thumbs/1.jpg").status_code == 404


def test_interrupted_jobs_are_requeued(store):
    job_id = store.add_job(url="https://vt.tiktok.com/abc/")
    store.claim_job()
    store.set_job_status(job_id, "transcribing")
    assert store.requeue_interrupted() == 1
    assert store.get_job(job_id).status == "queued"
