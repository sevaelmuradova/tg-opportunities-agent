"""Run: python test_agent.py  (temp SQLite DB; each message makes 2 real Gemini calls)"""
import asyncio
import os
import re
import tempfile
from datetime import date, timedelta

import db
from agent import run_agent
from models import MatchResult, OpportunityData
from my_profile import MY_PROFILE

USER = "seva"
TODAY = date.today()


def opp(**fields) -> OpportunityData:
    base = dict(
        type="program", deadline_raw=None, deadline_parsed=None, eligibility_requirements=None,
        eligible_countries=["worldwide"], education_level=[], eligible_fields=[], benefits=[],
        funding_details=None, country="online", apply_url=None, application_fee="free",
        is_opportunity=True, original_language="en",
    )
    return OpportunityData(**(base | fields))


def scored(title: str, score: int | None, action: str, flag: str | None = None) -> MatchResult:
    return MatchResult(
        opportunity_title=title, passed_hard_filters=score is not None, relevance_score=score,
        filter_failures=[] if score is not None else ["fee_too_high: $50 (~$50.00) > $15.00"],
        match_explanation="seeded for test", education_flag=flag, action=action,
    )


ML_DEADLINE = TODAY + timedelta(days=5)  # inside "this week"

# (opportunity, match) -> IDs 1..4 in a fresh DB
SEED = [
    (opp(title="Global ML Research Internship 2027", type="internship",
         deadline_raw=ML_DEADLINE.strftime("%B %d, %Y"), deadline_parsed=ML_DEADLINE,
         eligible_fields=["ML", "NLP", "AI"], benefits=["$3,000 monthly stipend", "paper co-authorship"],
         apply_url="https://example.com/ml-intern",
         summary="A 12-week remote ML research internship on LLM inference, open worldwide."),
     scored("Global ML Research Internship 2027", 92, "highly_relevant")),
    (opp(title="NLP Summer Research Program", type="research",
         deadline_raw="2027-yil 28-fevralgacha", deadline_parsed=date(2027, 2, 28), country="Germany",
         eligibility_requirements="Open to undergraduate students interested in NLP.",
         education_level=["undergraduate"], eligible_fields=["NLP", "ML"],
         benefits=["travel and housing covered", "research mentorship"],
         apply_url="https://example.com/nlp-summer", original_language="uz",
         summary="Berlinda bakalavr talabalari uchun 8 haftalik to'liq moliyalashtirilgan NLP tadqiqot dasturi."),
     scored("NLP Summer Research Program", 85, "apply_but_email_first",
            "Targets undergraduates; consider emailing to confirm gap year eligibility")),
    (opp(title="Global Nursing Excellence Scholarship", type="scholarship",
         deadline_raw="March 1, 2027", deadline_parsed=date(2027, 3, 1),
         eligible_fields=["nursing", "midwifery"], benefits=["full tuition"],
         summary="A scholarship covering tuition for nursing and midwifery degrees."),
     scored("Global Nursing Excellence Scholarship", 5, "not_for_you")),
    (opp(title="International Data Science Olympiad", type="competition", application_fee="$50",
         deadline_raw="January 20, 2027", deadline_parsed=date(2027, 1, 20), eligible_fields=["data science", "ML"],
         summary="An online data science olympiad with a $50 entry fee."),
     scored("International Data Science Olympiad", None, "not_for_you")),
]

UZBEK_HINTS = ("muddat", "dedlayn", "saqlangan", "sizning", "kun", "qoldi", "hafta", "imkoniyat", "uchun")


def ids_in(result: dict) -> list[int]:
    return [o["id"] for o in result.get("opportunities", [])]


def looks_uzbek(text: str) -> bool:
    lowered = text.lower()
    return sum(hint in lowered for hint in UZBEK_HINTS) >= 2


ENGLISH = ("reply is in English", lambda t: not looks_uzbek(t.response))


# (message, checks on the AgentTurn) - run in order; later steps depend on earlier ones (save -> deadlines)
CASES = [
    ("show me ML internships", [
        ENGLISH,
        ("tool = search_opportunities", lambda t: t.tool == "search_opportunities"),
        ("finds #1 (ML internship)", lambda t: 1 in ids_in(t.result)),
        ("excludes #3 (nursing) and #4 (failed filters)", lambda t: not {3, 4} & set(ids_in(t.result))),
        ("reply mentions #1", lambda t: re.search(r"#\s?1\b", t.response) is not None),
    ]),
    ("save #1", [
        ENGLISH,
        ("tool = save_opportunity(1)", lambda t: t.tool == "save_opportunity" and int(t.args.get("opportunity_id", 0)) == 1),
        ("saved ok", lambda t: t.result.get("ok") is True),
    ]),
    ("what deadlines do I have?", [
        ENGLISH,
        ("tool = get_deadlines", lambda t: t.tool == "get_deadlines"),
        ("includes saved #1", lambda t: 1 in ids_in(t.result)),
        ("reply mentions #1", lambda t: re.search(r"#\s?1\b", t.response) is not None),
    ]),
    ("bu haftada qanday dedlaynlarim bor?", [
        ("tool = get_deadlines", lambda t: t.tool == "get_deadlines"),
        ("includes saved #1", lambda t: 1 in ids_in(t.result)),
        ("reply is in Uzbek", lambda t: looks_uzbek(t.response)),
    ]),
    ("tell me more about #2", [
        ENGLISH,
        ("tool = get_opportunity_details(2)", lambda t: t.tool == "get_opportunity_details" and int(t.args.get("opportunity_id", 0)) == 2),
        ("returns #2", lambda t: t.result.get("opportunity", {}).get("id") == 2),
    ]),
    ("add forensics to my interests", [
        ENGLISH,
        ("tool = update_profile(fields_of_interest)", lambda t: t.tool == "update_profile" and t.args.get("field") == "fields_of_interest"),
        ("profile updated", lambda t: t.result.get("ok") is True),
    ]),
    ("what's good for gap year students?", [
        ENGLISH,
        ("tool = general_question", lambda t: t.tool == "general_question"),
        ("non-trivial answer", lambda t: len(t.response) > 200),
    ]),
]


async def main() -> None:
    await db.init_db()
    await db.store_user_profile(USER, MY_PROFILE)
    for opportunity, match in SEED:
        opp_id = await db.store_opportunity(opportunity, f"seed: {opportunity.title}", None)
        await db.store_match_result(opp_id, match, USER)
    print(f"Seeded {len(SEED)} opportunities (IDs 1-{len(SEED)}); ML internship deadline {ML_DEADLINE}")

    passed = total = 0
    for message, checks in CASES:
        turn = await run_agent(USER, message)
        print(f"\n===== {message!r} =====")
        print(f"  tool: {turn.tool} {turn.args}")
        print("  reply:\n" + "\n".join(f"    | {line}" for line in turn.response.splitlines()))
        for description, check in checks:
            ok = bool(check(turn))
            passed += ok
            total += 1
            print(f"  {'PASS' if ok else 'FAIL'}  {description}")

    profile = await db.get_user_profile(USER)
    ok = "forensics" in [f.lower() for f in profile.fields_of_interest]
    passed += ok
    total += 1
    print(f"\n  {'PASS' if ok else 'FAIL'}  'forensics' stored in DB profile: {profile.fields_of_interest}")
    print(f"\n{passed}/{total} checks passed")


if __name__ == "__main__":
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    try:
        asyncio.run(main())
    finally:
        os.remove(path)
