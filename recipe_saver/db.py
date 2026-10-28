"""SQLite storage: recipes, their ingredients, and import jobs (PRD data model).

One connection per call; WAL lets the web app read while the worker writes.
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from recipe_saver.models import Ingredient, Recipe

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    servings TEXT,
    steps TEXT NOT NULL DEFAULT '[]',          -- JSON list of strings, in order
    tags TEXT NOT NULL DEFAULT '[]',           -- JSON list of strings
    source_url TEXT,
    source_platform TEXT,
    source_id TEXT,                            -- the platform's post id, to spot re-shares
    author TEXT,
    thumbnail_path TEXT,
    raw_caption TEXT,
    transcript TEXT,
    parse_method TEXT NOT NULL DEFAULT 'manual',
    steps_missing INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS recipes_source ON recipes(source_platform, source_id);

CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    quantity REAL,
    quantity_max REAL,
    unit TEXT,
    item TEXT NOT NULL,
    note TEXT,
    raw_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingredients_recipe ON ingredients(recipe_id, position);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY,
    url TEXT,                                  -- a post to fetch, or
    text TEXT,                                 -- pasted recipe text (F13)
    status TEXT NOT NULL DEFAULT 'queued',     -- queued/fetching/transcribing/parsing/done/failed
    error TEXT,
    notes TEXT NOT NULL DEFAULT '[]',          -- which sources were tried, for the imports page
    recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
"""

ACTIVE = ("queued", "fetching", "transcribing", "parsing")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Source:
    url: str | None = None
    platform: str | None = None
    id: str | None = None
    author: str | None = None
    raw_caption: str | None = None
    transcript: str | None = None


@dataclass
class StoredRecipe:
    id: int
    recipe: Recipe
    tags: list[str] = field(default_factory=list)
    source: Source = field(default_factory=Source)
    thumbnail_path: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Job:
    id: int
    url: str | None
    text: str | None
    status: str
    error: str | None
    notes: list[str]
    recipe_id: int | None
    recipe_title: str | None
    created_at: str
    finished_at: str | None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:  # commit on success, roll back on error
                yield db
        finally:
            db.close()

    # Recipes

    def add_recipe(self, recipe: Recipe, source: Source | None = None,
                   tags: list[str] | None = None) -> int:
        source = source or Source()
        stamp = now()
        with self._conn() as db:
            cur = db.execute(
                """INSERT INTO recipes (title, servings, steps, tags, source_url, source_platform,
                       source_id, author, raw_caption, transcript, parse_method, steps_missing,
                       created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (recipe.title, recipe.servings, json.dumps(recipe.steps), json.dumps(tags or []),
                 source.url, source.platform, source.id, source.author, source.raw_caption,
                 source.transcript, recipe.parse_method, recipe.steps_missing, stamp, stamp),
            )
            self._write_ingredients(db, cur.lastrowid, recipe.ingredients)
            return cur.lastrowid

    def update_recipe(self, recipe_id: int, recipe: Recipe, tags: list[str]) -> None:
        with self._conn() as db:
            db.execute(
                """UPDATE recipes SET title=?, servings=?, steps=?, tags=?, steps_missing=?,
                       updated_at=? WHERE id=?""",
                (recipe.title, recipe.servings, json.dumps(recipe.steps), json.dumps(tags),
                 recipe.steps_missing, now(), recipe_id),
            )
            db.execute("DELETE FROM ingredients WHERE recipe_id=?", (recipe_id,))
            self._write_ingredients(db, recipe_id, recipe.ingredients)

    def set_thumbnail(self, recipe_id: int, path: str) -> None:
        with self._conn() as db:
            db.execute("UPDATE recipes SET thumbnail_path=? WHERE id=?", (path, recipe_id))

    def delete_recipe(self, recipe_id: int) -> str | None:
        """Returns the thumbnail path so the caller can remove the file."""
        with self._conn() as db:
            row = db.execute("SELECT thumbnail_path FROM recipes WHERE id=?", (recipe_id,)).fetchone()
            db.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
            return row["thumbnail_path"] if row else None

    def get_recipe(self, recipe_id: int) -> StoredRecipe | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
            return self._stored(db, row) if row else None

    def find_by_source(self, platform: str, source_id: str) -> int | None:
        with self._conn() as db:
            row = db.execute("SELECT id FROM recipes WHERE source_platform=? AND source_id=?",
                             (platform, source_id)).fetchone()
            return row["id"] if row else None

    def search(self, query: str = "", limit: int = 200) -> list[StoredRecipe]:
        """Every word must match the title, a tag, or an ingredient (F12)."""
        where, params = [], []
        for word in query.lower().split()[:10]:
            like = f"%{word}%"
            where.append("""(lower(r.title) LIKE ? OR lower(r.tags) LIKE ? OR EXISTS (
                SELECT 1 FROM ingredients i WHERE i.recipe_id = r.id AND lower(i.item) LIKE ?))""")
            params += [like, like, like]
        sql = "SELECT r.* FROM recipes r"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.created_at DESC, r.id DESC LIMIT ?"
        with self._conn() as db:
            return [self._stored(db, row) for row in db.execute(sql, [*params, limit])]

    def library_stats(self, top_tags: int = 12) -> tuple[int, list[str]]:
        """Recipe count and the most used tags, for the home page's header and filters."""
        with self._conn() as db:
            total = db.execute("SELECT count(*) FROM recipes").fetchone()[0]
            tags = [row[0] for row in db.execute(
                """SELECT t.value FROM recipes r, json_each(r.tags) t
                   GROUP BY t.value ORDER BY count(*) DESC, t.value LIMIT ?""", (top_tags,))]
        return total, tags

    @staticmethod
    def _write_ingredients(db, recipe_id: int, ingredients: list[Ingredient]) -> None:
        db.executemany(
            """INSERT INTO ingredients (recipe_id, position, quantity, quantity_max, unit, item,
                   note, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(recipe_id, pos, i.quantity, i.quantity_max, i.unit, i.item, i.note, i.raw_text)
             for pos, i in enumerate(ingredients)],
        )

    @staticmethod
    def _stored(db, row) -> StoredRecipe:
        ingredients = [
            Ingredient(raw_text=i["raw_text"], item=i["item"], quantity=i["quantity"],
                       quantity_max=i["quantity_max"], unit=i["unit"], note=i["note"])
            for i in db.execute("SELECT * FROM ingredients WHERE recipe_id=? ORDER BY position",
                                (row["id"],))
        ]
        return StoredRecipe(
            id=row["id"],
            recipe=Recipe(title=row["title"], ingredients=ingredients, steps=json.loads(row["steps"]),
                          servings=row["servings"], parse_method=row["parse_method"]),
            tags=json.loads(row["tags"]),
            source=Source(url=row["source_url"], platform=row["source_platform"], id=row["source_id"],
                          author=row["author"], raw_caption=row["raw_caption"],
                          transcript=row["transcript"]),
            thumbnail_path=row["thumbnail_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # Import jobs

    def add_job(self, url: str | None = None, text: str | None = None) -> int:
        with self._conn() as db:
            return db.execute("INSERT INTO import_jobs (url, text, created_at) VALUES (?, ?, ?)",
                              (url, text, now())).lastrowid

    def claim_job(self) -> Job | None:
        """Take the oldest queued job; the single worker thread is the only caller."""
        with self._conn() as db:
            row = db.execute("SELECT id FROM import_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE import_jobs SET status='fetching' WHERE id=?", (row["id"],))
        return self.get_job(row["id"])

    def set_job_status(self, job_id: int, status: str) -> None:
        with self._conn() as db:
            db.execute("UPDATE import_jobs SET status=? WHERE id=?", (status, job_id))

    def finish_job(self, job_id: int, recipe_id: int, notes: list[str]) -> None:
        with self._conn() as db:
            db.execute("""UPDATE import_jobs SET status='done', error=NULL, recipe_id=?, notes=?,
                              finished_at=? WHERE id=?""",
                       (recipe_id, json.dumps(notes), now(), job_id))

    def fail_job(self, job_id: int, error: str, notes: list[str] | None = None) -> None:
        with self._conn() as db:
            db.execute("""UPDATE import_jobs SET status='failed', error=?, notes=?, finished_at=?
                              WHERE id=?""",
                       (error, json.dumps(notes or []), now(), job_id))

    def retry_job(self, job_id: int) -> None:
        with self._conn() as db:
            db.execute("""UPDATE import_jobs SET status='queued', error=NULL, notes='[]',
                              finished_at=NULL WHERE id=? AND status='failed'""", (job_id,))

    def delete_job(self, job_id: int) -> None:
        with self._conn() as db:
            db.execute("DELETE FROM import_jobs WHERE id=?", (job_id,))

    def requeue_interrupted(self) -> int:
        """Jobs left mid-run by a restart go back in the queue rather than vanish."""
        with self._conn() as db:
            return db.execute(
                f"UPDATE import_jobs SET status='queued' WHERE status IN ({','.join('?' * 3)})",
                ACTIVE[1:]).rowcount

    def get_job(self, job_id: int) -> Job | None:
        jobs = self._jobs("WHERE j.id=?", (job_id,))
        return jobs[0] if jobs else None

    def list_jobs(self, limit: int = 100) -> list[Job]:
        return self._jobs("ORDER BY j.id DESC LIMIT ?", (limit,))

    def visible_jobs(self) -> list[Job]:
        """For the home page: anything running and failures nobody has dismissed. Finished
        imports drop off; the recipe shows up in the list instead."""
        return self._jobs("WHERE j.status != 'done' ORDER BY j.id DESC", ())

    def _jobs(self, tail: str, params: tuple) -> list[Job]:
        sql = f"""SELECT j.*, r.title AS recipe_title FROM import_jobs j
                  LEFT JOIN recipes r ON r.id = j.recipe_id {tail}"""
        with self._conn() as db:
            return [
                Job(id=r["id"], url=r["url"], text=r["text"], status=r["status"], error=r["error"],
                    notes=json.loads(r["notes"]), recipe_id=r["recipe_id"],
                    recipe_title=r["recipe_title"], created_at=r["created_at"],
                    finished_at=r["finished_at"])
                for r in db.execute(sql, params)
            ]
