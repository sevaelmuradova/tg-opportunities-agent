"""Run: python test_db.py  (uses a temp SQLite file; the pipeline steps call the real Gemini API)"""
import asyncio
import os
import tempfile
from datetime import date

import aiosqlite

import db
from models import OpportunityData
from my_profile import MY_PROFILE
from pipeline import process_post

USER = "seva"

# Real post from @EduGrandsUz. Deadline has no year -> deadline_parsed = next December 5.
POST = """1 Billion AI Film Award 2027

Davlat: Xalqaro 🇦🇪
Dastur shakli: Onlayn
Yosh toifasi: 17 yoshdan katta

New Media Academy (1 Billion Followers Summit) tomonidan o'tkaziladigan bu onlayn tanlovda ijodkorlar Seedance yordamida yaratilgan 5 dan 7 daqiqagacha bo'lgan original qisqa metrajli AI filmlarini topshiradi.

➡️Imtiyozlari:
- Bosh sovrin 1 000 000 AQSh dollari
- Kirish va ariza topshirish mutlaqo bepul
- Bir nechta film topshirish mumkin, har biri alohida ariza bilan
- Sun'iy intellekt asosidagi kino sohasida o'z ishini xalqaro miqyosda ko'rsatish imkoniyati

🔗Ro'yxatdan o'tish uchun: https://www.1billionsummit.com/ai-award-2027

📌Ro'yxatdan o'tishning so'nggi muddati: 5-dekabr

⚡️@EduGrandsUz
"""

results: list[tuple[str, bool]] = []


def check(description: str, ok: bool) -> None:
    results.append((description, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {description}")


async def count_rows(table: str) -> int:
    async with aiosqlite.connect(os.environ["DB_PATH"]) as conn:
        async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
            return (await cur.fetchone())[0]


def next_occurrence(month: int, day: int) -> date:
    today = date.today()
    this_year = date(today.year, month, day)
    return this_year if this_year >= today else date(today.year + 1, month, day)


async def main() -> None:
    print("\n===== 1. init fresh DB =====")
    await db.init_db()
    await db.store_user_profile(USER, MY_PROFILE)
    check("profile round-trips", await db.get_user_profile(USER) == MY_PROFILE)
    check("unknown user has no profile", await db.get_user_profile("nobody") is None)

    print("\n===== 2. process post through the full pipeline =====")
    first = await process_post(POST, "@EduGrandsUz", USER)
    opp_id = first["opportunity_id"]
    stored = await db.get_opportunity_by_id(opp_id)
    print(f"  title={stored['title']!r} deadline_raw={stored['deadline_raw']!r} "
          f"deadline_parsed={stored['deadline_parsed']!r} score={first['match']['relevance_score']} "
          f"action={first['match']['action']}")
    check("opportunity stored", stored is not None and stored["raw_text"] == POST)
    check("source_channel stored", stored["source_channel"] == "@EduGrandsUz")
    check("JSON list columns decode to lists", isinstance(stored["benefits"], list))
    check("match result stored", await count_rows("match_results") == 1)

    print("\n===== 3. same post again -> dedup =====")
    second = await process_post(POST, "@EduGrandsUz", USER)
    check("same opportunity id returned", second["opportunity_id"] == opp_id)
    check("still exactly 1 opportunity row", await count_rows("opportunities") == 1)

    # Field-based dedup (no LLM): same title/deadline/country but different raw text, incl. NULL deadline.
    base = OpportunityData.model_validate(first["opportunity"])
    check("same fields + different text -> same id",
          await db.store_opportunity(base, POST + " (repost)", None) == opp_id)
    no_deadline = base.model_copy(update={"title": "No-deadline test", "deadline_raw": None})
    a = await db.store_opportunity(no_deadline, "text A", None)
    b = await db.store_opportunity(no_deadline, "text B", None)
    check("NULL deadline still dedups", a == b)

    print("\n===== 4. save it -> get_saved_opportunities =====")
    await db.record_action(opp_id, USER, "saved", "film idea: Whisper subtitles")
    saved = await db.get_saved_opportunities(USER)
    check("saved list contains it", [o["id"] for o in saved] == [opp_id])
    check("note kept", saved[0]["saved_notes"] == "film idea: Whisper subtitles")
    check("other users see nothing", await db.get_saved_opportunities("someone_else") == [])
    try:
        await db.record_action(opp_id, USER, "liked")
        check("invalid action rejected", False)
    except aiosqlite.IntegrityError:
        check("invalid action rejected", True)

    print("\n===== 5. get_upcoming_deadlines =====")
    expected = next_occurrence(12, 5)
    days_left = (expected - date.today()).days
    check(f"LLM parsed '5-dekabr' as {expected}", stored["deadline_parsed"] == expected.isoformat())
    upcoming = await db.get_upcoming_deadlines(USER, days_left + 1)
    check(f"in window of {days_left + 1} days", [o["id"] for o in upcoming] == [opp_id])
    if days_left >= 1:
        check(f"not in window of {days_left - 1} days",
              await db.get_upcoming_deadlines(USER, days_left - 1) == [])

    await db.record_action(opp_id, USER, "dismissed")
    check("dismissed after saving -> no longer saved", await db.get_saved_opportunities(USER) == [])

    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} checks passed")


if __name__ == "__main__":
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    try:
        asyncio.run(main())
    finally:
        print("\n===== 6. cleanup =====")
        os.remove(path)
        print(f"  removed {path}")
