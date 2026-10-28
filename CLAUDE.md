# recipe-saver

Self-hosted app that turns a shared TikTok link into a saved, structured recipe.
Full spec: `docs/PRD.md`.

## Decisions (from PRD open questions, 2026-10-27)

- **Phone:** iPhone. Share flow is an Apple Shortcut that POSTs the URL over Tailscale.
- **Language:** English only, so English-only models (`whisper base.en` / `small.en`).
- **Database:** SQLite at `$DATA_DIR/recipes.db` (default `/data`). Single user. Backups
  come from the homelab's `/data` backup, so the app needs no backup code of its own.
- **Job queue:** an `import_jobs` table in SQLite plus one worker thread in the same
  process. No Redis.
- **Notifications:** none in v1. The Shortcut shows "Saving…" and the web app shows
  job status and failed imports. (F10 cut down to the in-app failed-imports list.)
- **Users:** single user, no accounts. Friends/public access out of scope.

## Stack

FastAPI + Jinja/HTMX (no frontend build step), yt-dlp (pinned), recipe-scrapers,
whisper.cpp, local LLM for parse fallback (JSON-constrained).

## Hosting

- Port: **8000** (from `PORT`). Health: `GET /health`. Dockerfile at the repo root.
- Visibility: **private** (tailnet only).
- Memory, measured in Docker (2026-10-28): the app container idles at ~95 MB and peaks
  at ~450 MB while whisper transcribes, so it needs **512 MB to 1 GB**, not the 256 MB
  default. The LLM is **not** in the image: it is any OpenAI-compatible server at
  `LLM_BASE_URL` (Ollama with `qwen2.5:3b` needs ~2.5 GB more). Where it runs is still to
  be decided in the server session. Without a reachable LLM, imports still work on the
  rule parser and websites, but voiceovers aren't parsed.
- `/data` holds `recipes.db` (+ WAL files) and `thumbs/<recipe id>.jpg`. Runs as UID 1000.

## Layout

- `recipe_saver/parse/` caption parser: `rules.py` first; `llm.py` fallback when the rules
  don't trust their result (run-on unbulleted lists) or the title is weak.
  The LLM is any OpenAI-compatible server (`LLM_BASE_URL`, `LLM_MODEL`), so the server
  can run Ollama or llama.cpp's `llama-server`. `LLM_TIMEOUT` (default 120 s) needs
  raising on the CPU-only server. Output is bounded (max_tokens, schema
  maxLength/maxItems) because a 3B model sometimes loops.
- `recipe_saver/fetch.py` yt-dlp caption/metadata and audio (decoded to 16 kHz WAV with
  PyAV in a temp dir, deleted after; no ffmpeg binary). Audio comes from the smallest
  video format: TikTok's audio-only format is the post's "sound" (often just background
  music), not the voiceover. yt-dlp needs its `curl-cffi` extra: without browser
  impersonation TikTok serves a bot challenge in Docker.
  `transcribe.py` runs the whisper.cpp CLI (`WHISPER_BIN`, `WHISPER_MODEL`).
- `recipe_saver/website.py` schema.org Recipe data from a linked page (F6), via recipe-scrapers.
- `recipe_saver/pipeline.py` one import: caption, then a linked website (replaces the
  caption result), then the voiceover (caption ingredients + spoken steps, F7). Under 15
  spoken words counts as no speech (music, silence).
- `recipe_saver/db.py` SQLite store (recipes, ingredients, import_jobs). `worker.py` the
  single import thread; jobs left mid-run by a restart are requeued at startup.
- `recipe_saver/web.py` FastAPI routes; `templates/` Jinja + HTMX (vendored in `static/`).
  `static/app.js` is optional polish on the home page (link/text toggle, tag chips, import
  feedback); everything works without it.
  The editor shows one ingredient per line (`format.py`) and parses edited lines back
  with the rule parser; untouched lines keep their stored ingredient.
- `tests/fixtures/captions/` the 14 sample captions with hand-written expected results;
  `tests/fixtures/transcripts/` the 13 sample voiceovers with expected ingredients and key actions.
- `spike/` milestone throwaway scripts (`uv run spike/fetch.py URL`)
- `data/` gitignored; `data/samples/` holds raw fetched captions.

## Commands

- `DATA_DIR=data/dev uv run python -m recipe_saver` runs the app locally on :8000
- `docker compose up --build` runs the image locally (data in `data/docker`, LLM on the Mac's Ollama)
- `uv run pytest` (LLM tests skip when no LLM is reachable; `-m "not llm"` for the fast ones)
- `uv run scripts/eval_parser.py [--no-llm] [-v]` scores the parser on the fixtures
- `uv run scripts/eval_audio.py [-v]` scores voiceover parsing and caption+voiceover merging
- `uv run spike/import_post.py URL` full import without the database; `spike/transcribe.py URL` saves transcripts
- Whisper on the Mac: `brew install whisper-cpp`; model
  `curl -L -o models/ggml-base.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin`
- Local LLM: `brew install ollama && brew services start ollama && ollama pull qwen2.5:3b`
