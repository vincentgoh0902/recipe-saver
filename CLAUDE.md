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
- `recipe_saver/fetch.py` yt-dlp caption/metadata and audio (to 16 kHz WAV via ffmpeg, temp
  dir, deleted after). Audio comes from the smallest video format: TikTok's audio-only
  format is the post's "sound" (often just background music), not the voiceover.
  `transcribe.py` runs the whisper.cpp CLI (`WHISPER_BIN`, `WHISPER_MODEL`).
- `recipe_saver/pipeline.py` one import: caption first; if it lacks ingredients or steps,
  transcribe the voiceover and merge (caption ingredients + spoken steps, F7). Under 15
  spoken words counts as no speech (music, silence). Website links (F6) not done yet.
- `tests/fixtures/captions/` the 14 sample captions with hand-written expected results;
  `tests/fixtures/transcripts/` the 13 sample voiceovers with expected ingredients and key actions.
- `spike/` milestone throwaway scripts (`uv run spike/fetch.py URL`)
- `data/` gitignored; `data/samples/` holds raw fetched captions.

## Commands

- `uv run pytest` (LLM tests skip when no LLM is reachable)
- `uv run scripts/eval_parser.py [--no-llm] [-v]` scores the parser on the fixtures
- `uv run scripts/eval_audio.py [-v]` scores voiceover parsing and caption+voiceover merging
- `uv run spike/import_post.py URL` full import; `spike/transcribe.py URL` saves transcripts
- Whisper: `brew install whisper-cpp ffmpeg`; model
  `curl -L -o models/ggml-base.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin`
- Local LLM: `brew install ollama && brew services start ollama && ollama pull qwen2.5:3b`
