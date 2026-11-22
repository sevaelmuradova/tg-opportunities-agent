# tg-agent

Personal Telegram agent: monitors opportunity channels (scholarships, internships, competitions, grants — mostly Uzbek/Russian/English posts, e.g. @EduGrandsUz), extracts structured data from each post with Gemini, scores it against the owner's profile, and will eventually notify and converse with the owner inside Telegram. Single user for now (`user_id` 'seva'), but keep data structures multi-user ready.

## Architecture (5 layers)
1. **Extraction** — post text → `OpportunityData`. BUILT (`extract.py`)
2. **Matching** — `OpportunityData` + `UserProfile` → `MatchResult`. Stage 1 deterministic hard filters (deadline passed, country excluded, not an opportunity, fee > max, explicit proof of university enrollment); Stage 2 Gemini relevance score. BUILT (`matcher.py`)
3. **State/memory** — SQLite (aiosqlite, raw SQL): opportunities, match_results, user_actions, user_profiles; `process_post()` pipeline. BUILT (`db.py`, `pipeline.py`)
4. **Conversational agent** — text in → text out, exactly 2 Gemini calls per message (forced function call to pick ONE tool → run tool → reply in the user's language). BUILT (`agent.py`, `tools.py`, `tool_schemas.py`); no Telegram code yet
5. **Telegram integration** — Telethon monitor (owner's user session) + python-telegram-bot interface, one asyncio process. BUILT (`monitor.py`, `bot.py`, `main.py`)

## Tech stack
Python 3.11+ (venv runs 3.12) · raw `httpx` to the Gemini REST API · Pydantic v2 · asyncio · aiosqlite · Telethon 1.x (user session) · python-telegram-bot 21+/22. No frameworks, no ORM, no LangChain, no Gemini SDK.

## File map
- `config.py` — loads `.env` with a small built-in loader (python-dotenv is NOT used); `GEMINI_MODELS` fallback list (default `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`; override via `GEMINI_MODELS` env), `REQUEST_TIMEOUT`, `get_api_key()`
- `gemini.py` — shared Gemini plumbing: Pydantic schema → Gemini `responseSchema`, retries + model fallback on 404/429/5xx/timeouts, `generate_structured(system_prompt, text, Model)`
- `models.py` — `OpportunityData` (Layer 1 output; Field descriptions are sent to Gemini as part of the schema; `deadline_parsed` is LLM-resolved, `source_channel` is hidden from the LLM schema and set from Telegram), `UserProfile`, `MatchResult`, `MatchAction`
- `extract.py` — extraction system prompt + `extract_opportunity(text)`
- `matcher.py` — `hard_filter()` (pure Python: deadline check on `deadline_parsed` — None passes; multilingual fee parsing, country lists, enrollment-proof keywords), `llm_score()`, `match_opportunity(opp, profile)`
- `my_profile.py` — hardcoded `MY_PROFILE`
- `db.py` — SQLite schema + all queries (raw SQL), incl. `search_opportunities()` (keyword LIKE over text columns, user's latest score >= min_score, dismissed excluded) and `monitored_channels` (channel_username like '@EduGrandsUz', COLLATE NOCASE). DB path: `DB_PATH` env, default `./opportunity_bot.db`. Lists stored as JSON text, dates as ISO text. Dedup: exact `raw_text`, or expression unique index on (title, COALESCE(deadline_raw,''), COALESCE(country,'')). "Saved" = the user's latest action on it is 'saved'
- `pipeline.py` — `process_post(text, source_channel, user_id)`: extract → store → match (profile loaded from DB) → store match
- `agent.py` — `handle_message(user_id, message) -> str` (and `run_agent()` returning tool/args/result/response for tests). Selection prompt + response prompt live here
- `tools.py` — 8 tools as plain async functions over `db.py` (no LLM), each returns a dict, errors as `{"error": ...}`; `TOOLS` registry
- `tool_schemas.py` — Gemini `functionDeclarations`. `user_id` is never a tool parameter: the agent injects it, so the model can't touch other users' data
- `monitor.py` — `ChannelMonitor`: joins monitored channels (Telethon only gets posts from joined channels), `expand_links()` turns hidden text links + URL buttons into visible URLs for the LLM (UTF-16 offsets!), `to_bot_entities()` keeps post formatting for the bot, `handle_post()` runs `process_post` per subscriber and notifies if score >= threshold and not seen before
- `bot.py` — handlers (/start 5-question ConversationHandler, /channels, /saved, /deadlines, text → agent, inline buttons `save:/dismiss:/details:/rmchan:`), `send_notification()`, `build_application()`. user_id = Telegram user id as str. Optional `TELEGRAM_ALLOWED_USERS` allowlist
- `main.py` — reads Telegram env vars, init DB, starts monitor (first run: interactive login → `monitor.session`) then bot polling in the same loop
- `test_bot.py` — offline (fake Telegram objects, stubbed agent/pipeline): /start, channels, agent routing, notification format, callbacks, link expansion, threshold
- `README.md` — setup for humans (BotFather, my.telegram.org, .env)
- `test_agent.py` — temp DB seeded with 4 opportunities + scores (no LLM), 7 messages incl. Uzbek; checks tool choice, args, reply language
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
Always use the venv: `.venv/bin/python test_extraction.py`, `test_matching.py`, `test_db.py`, `test_agent.py`, `test_bot.py` (offline). Run the bot: `.venv/bin/python main.py`. Install deps with `.venv/bin/python -m pip install ...` and add them to `requirements.txt`. Gemini free tier is often overloaded (503/429/timeouts); the fallback list handles it — don't hammer the API with repeated probes.

## Do NOT
- Add frameworks (LangChain etc.), an ORM, or an LLM SDK
- Refactor or change existing layers without asking
- Add `Co-Authored-By` / Claude attribution to commits — owner's explicit preference
- Commit `.env`, `.venv`, `*.db`, `*.session` (Telegram login = full account access), `reports/`, `research_notes/`

## Current status
All 5 layers built (repo: github.com/sevaelmuradova/tg-opportunities-agent). Layer 5 is tested offline only (`test_bot.py`); first real run needs the Telegram vars in `.env` + one-time Telethon login. Agent is single-turn (no chat history), so "#1" always means opportunity ID 1. Media-only posts are skipped; notifications resend text + formatting, not media.
