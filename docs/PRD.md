# Free Recipe Saver: PRD

Oct 27, 2026 · Vincent

> Open questions resolved 2026-10-27; answers are at the bottom and the resulting
> decisions are in `CLAUDE.md`.

## Overview

A free, self-hosted app that turns a shared TikTok link into a clean, saved recipe, running entirely on my Oracle server at zero cost.

**Problem.** I find most of my recipes on TikTok, then lose them in saved videos or screenshots. Apps that import recipes from videos (ReciMe, Honeydew, Pestle, Deglaze) work well but put full use behind subscriptions.

**Who it's for.** Me first, as a single user. Friends and family may follow if it works well, so the design shouldn't rule that out.

**How it works.** I share a TikTok to the app. My server reads the caption, transcribes the audio when the caption lacks ingredients or steps, merges the two into a recipe, and saves it to my library.

## Goals, non-goals and success metrics

The app succeeds if saving a TikTok recipe takes one share and costs nothing to run.

**Goals**

- Save a recipe from a TikTok link in one share action from my phone.
- Extract title, ingredients with quantities, and steps accurately enough that I rarely need to edit.
- Run at zero monthly cost on Oracle Cloud's Always Free tier.
- Keep my recipes searchable and available on my phone and laptop.

**Non-goals for v1**

- Public sign-ups, payments, or an app store release.
- Meal planning, nutrition tracking, or AI meal suggestions.
- Reading on-screen text from video frames (deferred to a later phase).
- Instagram, YouTube, and other sources (later phase).

**Success metrics**

| Metric | Target |
| --- | --- |
| Imports producing a usable recipe without edits | 80% or more |
| Time from share to saved recipe | Under 2 minutes |
| Monthly running cost | $0 |
| Recipes I actually save and cook from in the first month | 10 or more |

## User stories and core flow

The core flow is share, wait under 2 minutes, then cook from a clean recipe card.

**User stories**

- As a cook scrolling TikTok, I share a recipe video to the app so I can save it without leaving TikTok.
- As a cook, I get a notification when the recipe is ready, so I don't have to keep the app open. *(Deferred: no notifications in v1.)*
- As a cook, I open a recipe and see ingredients and steps, with a link back to the original video.
- As a cook, I fix a wrong quantity or step myself when the import gets something wrong.
- As a cook, I search my recipes by name, ingredient, or tag.
- As a cook, I see clearly when an import failed and why, and can retry it or paste the recipe in manually.
- As a cook, I add a recipe's ingredients to a shopping list (phase 2).

**Core flow**

1. I tap Share on a TikTok and choose the app's shortcut.
2. The shortcut sends the link to my server over Tailscale and shows "Saving…".
3. The server queues an import job and returns immediately.
4. The worker fetches the caption; if it lacks ingredients or steps, it also transcribes the audio and merges the two.
5. The parser structures the text into a recipe and saves it.
6. ~~I get a notification with a link to the recipe card.~~ The recipe appears in the web app; failures show in a failed-imports list.
7. I review the recipe and edit anything wrong.

## Functional requirements

Import must work for v1; library features are minimal; shopping list waits for phase 2.

| ID | Requirement | Priority |
| --- | --- | --- |
| F1 | Accept a TikTok URL via an HTTP endpoint (from the phone share shortcut) and via a paste box in the web app | v1 |
| F2 | Process imports as background jobs; the endpoint returns a job ID immediately | v1 |
| F3 | Fetch the post's caption and metadata (title, author, thumbnail) | v1 |
| F4 | Check whether the caption has both an ingredient list and steps | v1 |
| F5 | If the caption lacks ingredients or steps, download the audio track and transcribe it locally | v1 |
| F6 | If the caption links a recipe website, fetch it and read its schema.org Recipe data first | v1 |
| F7 | Parse text into a recipe: title, servings, ingredients (quantity, unit, item), steps. When both caption and transcript are used, take ingredients and quantities from the caption and steps from the transcript | v1 |
| F8 | Try rule-based parsing first; fall back to the local LLM with JSON-constrained output | v1 |
| F9 | Save the source URL, raw caption, and transcript with each recipe so imports can be re-run | v1 |
| F10 | Show finished and failed imports in the web app, with the failure reason, and flag recipes saved without steps. (Push notifications deferred.) | v1 |
| F11 | Recipe card view, inline editing, delete | v1 |
| F12 | Search by title, ingredient, and tag | v1 |
| F13 | Manual recipe entry and "paste text" import as a fallback | v1 |
| F14 | Re-run an import with the latest parser | Phase 2 |
| F15 | Shopping list built from selected recipes, merging duplicate ingredients | Phase 2 |
| F16 | Instagram Reels and YouTube Shorts imports | Phase 2 |
| F17 | On-screen text extraction from video frames | Phase 3 |

## Architecture and tech stack

Everything runs on the Oracle ARM instance; the phone only sends a link.

The worker tries the caption first, then a linked recipe website, then the audio, and keeps going until it has both ingredients and steps, merging what each source found. The web app reads the same database.

| Component | Tool | Notes |
| --- | --- | --- |
| Share trigger | Apple Shortcut | iPhone |
| Private network | Tailscale | No public endpoint in v1 |
| API and web app | FastAPI with Jinja + HTMX | Served on the tailnet only |
| Job queue | `import_jobs` table in SQLite, one worker thread | No Redis |
| Video fetching | yt-dlp | Pinned (2026.10.26); update when TikTok breaks it |
| Website recipes | recipe-scrapers or a schema.org JSON-LD parser | No AI needed |
| Transcription | whisper.cpp, `base.en` or `small.en` | English only; runs on ARM CPU |
| Parsing | Rules plus an ingredient-parsing library; local 3B to 8B model | JSON-constrained output |
| Database | SQLite under `/data` | Backed up by the homelab `/data` backup |
| Notifications | None in v1 | |

## Data model

Three tables cover v1: recipes, their ingredients, and import jobs (SQLite).

| Table | Key fields | Notes |
| --- | --- | --- |
| `recipes` | id, title, servings, steps (ordered text list), tags, source_url, source_platform, author, thumbnail_path, raw_caption, transcript, parse_method (rules / llm / website / merged / manual), steps_missing, created_at, updated_at | Raw caption and transcript are kept so a better parser can re-run later |
| `ingredients` | id, recipe_id, position, quantity, unit, item, note, raw_text | raw_text keeps the original line in case parsing split it wrongly |
| `import_jobs` | id, url, status (queued / fetching / transcribing / parsing / done / failed), error, recipe_id, created_at, finished_at | Drives the job status and failed-imports list |

Phase 2 adds `shopping_list_items` (item, quantity, unit, checked, recipe_ids).

## Non-functional requirements and risks

The biggest risk is TikTok fetching breaking; keeping the raw data and a manual fallback contains it.

**Non-functional requirements**

- **Cost:** $0 per month. No paid APIs; free-tier APIs only as an optional fallback.
- **Privacy:** the app is reachable only over my tailnet. No public endpoint in v1.
- **Performance:** a caption-only import finishes in under 30 seconds; with transcription, under 2 minutes.
- **Resources:** fits within the Oracle ARM instance alongside my other projects; one import job at a time. Needs roughly 4 GB memory (LLM + whisper), not the 256 MB default.
- **Reliability:** failed jobs are recorded with a reason and never lost silently.
- **Maintainability:** each stage (fetch, transcribe, parse) is a separate module with tests on saved sample captions.

**Risks**

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| yt-dlp breaks when TikTok changes its site | High | Pin and update yt-dlp regularly; keep paste-text import as a fallback |
| Fetching TikTok content conflicts with TikTok's terms | Medium | Personal use only; store text, not videos; delete downloaded audio after transcribing |
| Local LLM output is wrong or slow on CPU | Medium | Rules first; JSON-constrained output; keep raw text for re-runs; optional free-tier API fallback |
| Recipe only appears as on-screen text, not caption or audio | Medium | Flag as "no recipe found" and offer paste-text; frame text extraction in phase 3 |
| Oracle reclaims an idle Always Free instance | Low | Account on pay-as-you-go within free limits; backups off the instance |

## Milestones

1. **Fetch spike.** Script that takes a TikTok URL and prints caption and metadata with yt-dlp. Done when it works on 10 saved recipe links. (`spike/fetch.py`)
2. **Parser.** Rule-based parser plus local LLM fallback (small model, JSON output). Done when 8 of 10 sample captions parse correctly.
3. **Audio fallback.** whisper.cpp transcription for captions with no recipe. Done when 3 voiceover-only videos and 3 ingredients-only captions all produce recipes with steps.
4. **Backend and web app.** FastAPI job queue, database, recipe card, edit, search, paste import. Done when I can browse and edit everything from my phone over Tailscale.
5. **Share shortcut.** iPhone share Shortcut posting to the server. Done when saving from TikTok takes one share and I use it for a week.

After v1: phase 2 adds the shopping list, re-run imports, Instagram and YouTube, and maybe notifications. Phase 3 adds on-screen text extraction.

## Assumptions and open questions

**Assumptions**

- The Oracle instance has enough RAM for a small model (about 3B to 8B parameters, quantised) plus whisper.cpp.
- The phone reaches the server through the Tailscale app, so no public domain is needed in v1.

**Open questions (resolved 2026-10-27)**

- [x] iPhone or Android? **iPhone.**
- [x] Which languages do the recipe videos use? **English.**
- [x] Postgres or SQLite? **Either is fine → SQLite.**
- [x] Notifications? **Not needed for now.**
- [x] Should friends get access later? **Not needed for now.**
