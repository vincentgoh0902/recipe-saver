"""The single background worker: takes import jobs from SQLite one at a time (F2)."""

import logging
import threading
from pathlib import Path

import httpx

from recipe_saver.db import Source, Store
from recipe_saver.fetch import FetchError, fetch_post
from recipe_saver.pipeline import Importer

log = logging.getLogger(__name__)
THUMB_MAX_BYTES = 2_000_000


class Worker(threading.Thread):
    def __init__(self, store: Store, importer: Importer, thumbs_dir: Path):
        super().__init__(name="import-worker", daemon=True)
        self.store = store
        self.importer = importer
        self.thumbs_dir = thumbs_dir
        self.wake = threading.Event()
        self.stopping = threading.Event()

    def notify(self) -> None:
        """Called after a job is queued so the worker doesn't wait for its next poll."""
        self.wake.set()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()

    def run(self) -> None:
        if n := self.store.requeue_interrupted():
            log.info("requeued %d interrupted import(s)", n)
        while not self.stopping.is_set():
            job = self.store.claim_job()
            if job is None:
                self.wake.wait(timeout=30)
                self.wake.clear()
                continue
            try:
                self.process(job.id, job.url, job.text)
            except Exception as e:  # never let one bad job kill the worker
                log.exception("import %d failed", job.id)
                self.store.fail_job(job.id, f"unexpected error: {e}"[:500])

    def process(self, job_id: int, url: str | None, text: str | None) -> None:
        def status(s: str) -> None:
            self.store.set_job_status(job_id, s)

        if text is not None:
            status("parsing")
            parsed = self.importer.run_text(text)
            if parsed.recipe is None:
                self.store.fail_job(job_id, "no ingredient list found in the pasted text", parsed.notes)
                return
            recipe_id = self.store.add_recipe(parsed.recipe, Source(raw_caption=text))
            self.store.finish_job(job_id, recipe_id, parsed.notes)
            return

        try:
            post = fetch_post(url)
        except FetchError as e:
            self.store.fail_job(job_id, str(e)[:500])
            return
        if existing := self.store.find_by_source(post.platform, post.id):
            self.store.finish_job(job_id, existing, ["already saved"])
            return

        result = self.importer.run(url, on_status=status, post=post)
        if result.recipe is None:
            self.store.fail_job(job_id, "no recipe found in the caption, linked page or voiceover",
                                result.notes)
            return
        source = Source(url=post.url, platform=post.platform, id=post.id, author=post.author,
                        raw_caption=post.caption, transcript=result.transcript)
        recipe_id = self.store.add_recipe(result.recipe, source)
        if post.thumbnail:
            self._save_thumbnail(recipe_id, post.thumbnail, result.notes)
        self.store.finish_job(job_id, recipe_id, result.notes)

    def _save_thumbnail(self, recipe_id: int, url: str, notes: list[str]) -> None:
        # TikTok thumbnail URLs expire, so keep a copy.
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) > THUMB_MAX_BYTES:
                raise ValueError("thumbnail too large")
        except (httpx.HTTPError, ValueError) as e:
            notes.append(f"no thumbnail: {e}")
            return
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        name = f"{recipe_id}.jpg"
        (self.thumbs_dir / name).write_bytes(resp.content)
        self.store.set_thumbnail(recipe_id, name)
