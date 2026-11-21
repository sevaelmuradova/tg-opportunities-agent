# tg-agent

Personal Telegram agent: monitors opportunity channels (scholarships, internships, competitions, grants — mostly Uzbek/Russian/English posts, e.g. @EduGrandsUz), extracts structured data from each post with Gemini, scores it against the owner's profile, and will eventually notify and converse with the owner inside Telegram. Single user for now (`user_id` 'seva'), but keep data structures multi-user ready.

## Architecture (5 layers)
1. **Extraction** — post text → `OpportunityData`. BUILT (`extract.py`)
2. **Matching** — `OpportunityData` + `UserProfile` → `MatchResult`. Stage 1 deterministic hard filters (deadline passed, country excluded, not an opportunity, fee > max, explicit proof of university enrollment); Stage 2 Gemini relevance score. BUILT (`matcher.py`)
3. **State/memory** — SQLite (aiosqlite, raw SQL): opportunities, match_results, user_actions, user_profiles; `process_post()` pipeline. BUILT (`db.py`, `pipeline.py`)
4. **Conversational interface** — NOT BUILT
5. **Telegram monitoring** — NOT BUILT

## Tech stack
Python 3.11+ (venv runs 3.12) · raw `httpx` to the Gemini REST API · Pydantic v2 · asyncio · aiosqlite. No frameworks, no ORM, no LangChain, no Gemini SDK.

## File map
- `config.py` — loads `.env` with a small built-in loader (python-dotenv is NOT used); `GEMINI_MODELS` fallback list (default `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`; override via `GEMINI_MODELS` env), `REQUEST_TIMEOUT`, `get_api_key()`
- `gemini.py` — shared Gemini plumbing: Pydantic schema → Gemini `responseSchema`, retries + model fallback on 404/429/5xx/timeouts, `generate_structured(system_prompt, text, Model)`
- `models.py` — `OpportunityData` (Layer 1 output; Field descriptions are sent to Gemini as part of the schema; `deadline_parsed` is LLM-resolved, `source_channel` is hidden from the LLM schema and set from Telegram), `UserProfile`, `MatchResult`, `MatchAction`
- `extract.py` — extraction system prompt + `extract_opportunity(text)`
- `matcher.py` — `hard_filter()` (pure Python: deadline check on `deadline_parsed` — None passes; multilingual fee parsing, country lists, enrollment-proof keywords), `llm_score()`, `match_opportunity(opp, profile)`
- `my_profile.py` — hardcoded `MY_PROFILE`
- `db.py` — SQLite schema + all queries (raw SQL). DB path: `DB_PATH` env, default `./opportunity_bot.db`. Lists stored as JSON text, dates as ISO text. Dedup: exact `raw_text`, or expression unique index on (title, COALESCE(deadline_raw,''), COALESCE(country,'')). "Saved" = the user's latest action on it is 'saved'
- `pipeline.py` — `process_post(text, source_channel, user_id)`: extract → store → match (profile loaded from DB) → store match
- `test_db.py` — temp-file DB; full pipeline, dedup, saved, upcoming deadlines
- `test_extraction.py`, `test_matching.py` — run against the real Gemini API; print PASS/FAIL per check
- `pipeline_test.py` — one hardcoded post through Layer 1 → Layer 2, prints both as JSON
- `reports/`, `research_notes/` — unrelated research, gitignored; ignore

## Conventions
- Raw HTTP API calls; only our own thin helpers in `gemini.py`, no SDK/wrapper libraries
- Raw SQL, no ORM
- Every LLM output is validated through a Pydantic model (Gemini structured output + `model_validate_json`)
- Secrets in `.env` (gitignored); never print or commit `GEMINI_API_KEY`
- Async throughout
- Hard filters stay deterministic and conservative: reject only on explicit facts; anything unparseable passes to Stage 2
- Extracted `title`/`summary` stay in the post's original language; `eligible_countries` in English
- Copied Telegram text loses hyperlinks — in tests, add URLs by hand (`Havola (https://...)`)

## Running
Always use the venv: `.venv/bin/python test_extraction.py`, `test_matching.py`, `test_db.py`. Install deps with `.venv/bin/python -m pip install ...` and add them to `requirements.txt`. Gemini free tier is often overloaded (503/429/timeouts); the fallback list handles it — don't hammer the API with repeated probes.

## Do NOT
- Add frameworks (LangChain etc.), an ORM, or an LLM SDK
- Refactor or change existing layers without asking
- Add `Co-Authored-By` / Claude attribution to commits — owner's explicit preference
- Commit `.env`, `.venv`, `*.db`, `reports/`, `research_notes/`

## Current status
Layers 1–3 built and tested (repo: github.com/sevaelmuradova/tg-opportunities-agent). Next: Layer 4 (conversational interface), then Layer 5 (Telegram monitoring).
