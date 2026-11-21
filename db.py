"""Layer 3: SQLite storage (aiosqlite, raw SQL).

DB path: DB_PATH env var (from .env or the shell), default ./opportunity_bot.db.
It is read on every connection, so tests can point it at a temp file.
List fields are stored as JSON arrays in TEXT columns; dates as ISO 'YYYY-MM-DD' text.
"""
import json
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta

import aiosqlite

import config  # noqa: F401  -- importing it loads .env into os.environ
from models import MatchResult, OpportunityData, UserProfile

DEFAULT_DB_PATH = "./opportunity_bot.db"
JSON_COLUMNS = ("benefits", "eligible_countries", "education_level", "eligible_fields", "filter_failures")
BOOL_COLUMNS = ("is_opportunity", "passed_hard_filters")

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id                       INTEGER PRIMARY KEY,
    title                    TEXT NOT NULL,
    type                     TEXT,
    deadline_raw             TEXT,
    deadline_parsed          DATE,
    source_channel           TEXT,
    country                  TEXT,
    apply_url                TEXT,
    application_fee          TEXT,
    summary                  TEXT,
    benefits                 TEXT,  -- JSON array
    eligible_countries       TEXT,  -- JSON array
    education_level          TEXT,  -- JSON array
    eligible_fields          TEXT,  -- JSON array
    eligibility_requirements TEXT,
    funding_details          TEXT,
    original_language        TEXT,
    is_opportunity           BOOLEAN,
    raw_text                 TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Dedup on (title, deadline_raw, country). An expression index instead of a table-level
-- UNIQUE(...) because SQLite treats NULLs as distinct in UNIQUE: two identical posts with
-- no deadline would otherwise both be inserted.
CREATE UNIQUE INDEX IF NOT EXISTS opportunities_dedup
    ON opportunities (title, COALESCE(deadline_raw, ''), COALESCE(country, ''));
CREATE INDEX IF NOT EXISTS opportunities_raw_text ON opportunities (raw_text);

CREATE TABLE IF NOT EXISTS match_results (
    id                  INTEGER PRIMARY KEY,
    opportunity_id      INTEGER REFERENCES opportunities(id),
    user_id             TEXT DEFAULT 'seva',
    relevance_score     INTEGER,
    match_explanation   TEXT,
    education_flag      TEXT,
    action              TEXT,
    passed_hard_filters BOOLEAN,
    filter_failures     TEXT,  -- JSON array
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_actions (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER REFERENCES opportunities(id),
    user_id        TEXT DEFAULT 'seva',
    action         TEXT CHECK (action IN ('saved', 'dismissed', 'applied', 'snoozed')),
    notes          TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id      TEXT PRIMARY KEY,
    profile_data TEXT,  -- full UserProfile as JSON
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Opportunities whose MOST RECENT action by this user is 'saved'
# (saved and later dismissed/applied -> no longer counts as saved).
SAVED_OPPORTUNITIES_SQL = """
SELECT o.*, ua.created_at AS saved_at, ua.notes AS saved_notes
FROM opportunities o
JOIN user_actions ua ON ua.opportunity_id = o.id
WHERE ua.user_id = :user_id
  AND ua.action = 'saved'
  AND ua.id = (
      SELECT MAX(id) FROM user_actions
      WHERE opportunity_id = o.id AND user_id = :user_id
  )
"""


def _db_path() -> str:
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


@asynccontextmanager
async def _connect():
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")  # SQLite doesn't enforce REFERENCES without this
        yield db


def _row_to_dict(row: aiosqlite.Row) -> dict:
    data = dict(row)
    for col in JSON_COLUMNS:
        if col in data and data[col] is not None:
            data[col] = json.loads(data[col])
    for col in BOOL_COLUMNS:
        if col in data and data[col] is not None:
            data[col] = bool(data[col])
    return data


async def init_db() -> None:
    async with _connect() as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def store_opportunity(opp: OpportunityData, raw_text: str, source_channel: str | None) -> int:
    """Insert an opportunity and return its id. Duplicates return the existing id without inserting.

    Duplicate = exact same raw_text, or same (title, deadline_raw, country).
    The raw_text check matters because extraction can word a title slightly differently on a
    second run (e.g. when a fallback model answers), which would slip past the field-based check.
    """
    async with _connect() as db:
        async with db.execute("SELECT id FROM opportunities WHERE raw_text = ?", (raw_text,)) as cur:
            if row := await cur.fetchone():
                return row["id"]

        cur = await db.execute(
            """
            INSERT INTO opportunities (
                title, type, deadline_raw, deadline_parsed, source_channel, country, apply_url,
                application_fee, summary, benefits, eligible_countries, education_level,
                eligible_fields, eligibility_requirements, funding_details, original_language,
                is_opportunity, raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                opp.title, opp.type, opp.deadline_raw,
                opp.deadline_parsed.isoformat() if opp.deadline_parsed else None,
                source_channel or opp.source_channel, opp.country, opp.apply_url,
                opp.application_fee, opp.summary, json.dumps(opp.benefits, ensure_ascii=False),
                json.dumps(opp.eligible_countries, ensure_ascii=False),
                json.dumps(opp.education_level, ensure_ascii=False),
                json.dumps(opp.eligible_fields, ensure_ascii=False),
                opp.eligibility_requirements, opp.funding_details, opp.original_language,
                opp.is_opportunity, raw_text,
            ),
        )
        if cur.rowcount == 1:
            await db.commit()
            return cur.lastrowid

        async with db.execute(  # conflict on the dedup index: fetch the existing row
            """
            SELECT id FROM opportunities
            WHERE title = ? AND COALESCE(deadline_raw, '') = COALESCE(?, '')
              AND COALESCE(country, '') = COALESCE(?, '')
            """,
            (opp.title, opp.deadline_raw, opp.country),
        ) as cur:
            row = await cur.fetchone()
        return row["id"]


async def store_match_result(opp_id: int, result: MatchResult, user_id: str) -> int:
    async with _connect() as db:
        cur = await db.execute(
            """
            INSERT INTO match_results (
                opportunity_id, user_id, relevance_score, match_explanation, education_flag,
                action, passed_hard_filters, filter_failures
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opp_id, user_id, result.relevance_score, result.match_explanation,
                result.education_flag, result.action, result.passed_hard_filters,
                json.dumps(result.filter_failures, ensure_ascii=False),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def record_action(opp_id: int, user_id: str, action: str, notes: str | None = None) -> int:
    """action must be one of: saved, dismissed, applied, snoozed (enforced by a CHECK constraint)."""
    async with _connect() as db:
        cur = await db.execute(
            "INSERT INTO user_actions (opportunity_id, user_id, action, notes) VALUES (?, ?, ?, ?)",
            (opp_id, user_id, action, notes),
        )
        await db.commit()
        return cur.lastrowid


async def get_saved_opportunities(user_id: str) -> list[dict]:
    async with _connect() as db:
        async with db.execute(
            SAVED_OPPORTUNITIES_SQL + " ORDER BY ua.created_at DESC, ua.id DESC", {"user_id": user_id}
        ) as cur:
            return [_row_to_dict(row) for row in await cur.fetchall()]


async def get_upcoming_deadlines(user_id: str, days: int) -> list[dict]:
    """Saved opportunities whose deadline_parsed is between today and today + `days` (inclusive)."""
    today = date.today()
    async with _connect() as db:
        async with db.execute(
            SAVED_OPPORTUNITIES_SQL
            + " AND o.deadline_parsed BETWEEN :start AND :end ORDER BY o.deadline_parsed",
            {"user_id": user_id, "start": today.isoformat(), "end": (today + timedelta(days=days)).isoformat()},
        ) as cur:
            return [_row_to_dict(row) for row in await cur.fetchall()]


async def get_opportunity_by_id(opp_id: int) -> dict | None:
    async with _connect() as db:
        async with db.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)) as cur:
            row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def store_user_profile(user_id: str, profile: UserProfile) -> None:
    """Insert or replace the user's profile; created_at is kept, updated_at is refreshed."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO user_profiles (user_id, profile_data) VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                profile_data = excluded.profile_data,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (user_id, profile.model_dump_json()),
        )
        await db.commit()


async def get_user_profile(user_id: str) -> UserProfile | None:
    async with _connect() as db:
        async with db.execute("SELECT profile_data FROM user_profiles WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    return UserProfile.model_validate_json(row["profile_data"]) if row else None
