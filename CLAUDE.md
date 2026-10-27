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

- Port: **8000** (from `PORT`). Health: `GET /health`.
- Visibility: **private** (tailnet only).
- Memory: **well above the 256 MB default.** The LLM fallback (3B model, Q4, ~2.5 GB)
  plus whisper (~300 MB) means roughly **4 GB**. To be agreed in the server session,
  including whether the LLM runs inside the app container or as a separate service.

## Layout

- `recipe_saver/parse/` caption parser: `rules.py` first; `llm.py` fallback when the rules
  don't trust their result (run-on unbulleted lists) or the title is weak.
  The LLM is any OpenAI-compatible server (`LLM_BASE_URL`, `LLM_MODEL`), so the server
  can run Ollama or llama.cpp's `llama-server`.
- `tests/fixtures/captions/` the 10 sample captions with hand-written expected results.
- `spike/` milestone throwaway scripts (`uv run spike/fetch.py URL`)
- `data/` gitignored; `data/samples/` holds raw fetched captions.

## Commands

- `uv run pytest` (LLM tests skip when no LLM is reachable)
- `uv run scripts/eval_parser.py [--no-llm] [-v]` scores the parser on the fixtures
- Local LLM: `brew install ollama && brew services start ollama && ollama pull qwen2.5:3b`
