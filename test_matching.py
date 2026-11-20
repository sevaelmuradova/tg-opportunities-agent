"""Run: python test_matching.py  (cases that pass the hard filters call the real Gemini API)"""
import asyncio

from matcher import match_opportunity
from models import MatchResult, OpportunityData
from my_profile import MY_PROFILE


def make_opp(**overrides) -> OpportunityData:
    """A neutral, valid opportunity; each test case overrides only what it's about."""
    base = dict(
        title="Test Opportunity",
        type="program",
        deadline_raw="March 1, 2027",
        eligibility_requirements=None,
        eligible_countries=[],
        education_level=[],
        eligible_fields=[],
        benefits=[],
        funding_details=None,
        country="online",
        apply_url="https://example.com/apply",
        application_fee="free",
        summary="A test opportunity.",
        is_opportunity=True,
        original_language="en",
    )
    return OpportunityData(**(base | overrides))


PERFECT_MATCH = make_opp(
    title="Global ML Research Internship 2027",
    type="internship",
    deadline_raw="February 15, 2027",
    eligibility_requirements="Open to students and recent school graduates from any country, ages 18+. "
    "Strong Python and PyTorch skills; experience with LLMs or speech models is a plus.",
    eligible_countries=["worldwide"],
    education_level=["anyone"],
    eligible_fields=["ML", "NLP", "AI"],
    benefits=["$3,000 monthly stipend", "mentorship from ML researchers", "co-authorship on papers"],
    funding_details="Fully remote, stipend paid monthly",
    summary="A 12-week remote research internship on LLM inference and speech recognition, "
    "open worldwide with no application fee.",
)

DEADLINE_PASSED = make_opp(
    title="AI Summer School 2026",
    deadline_raw="January 15, 2026",
    eligible_fields=["AI"],
)

EU_ONLY = make_opp(
    title="Erasmus Tech Fellowship",
    type="fellowship",
    eligibility_requirements="Only EU citizens may apply.",
    eligible_countries=["EU"],
    eligible_fields=["CS", "AI"],
)

AMBIGUOUS_EDUCATION = make_opp(
    title="NLP Summer Research Program",
    type="research",
    deadline_raw="2027-yil 28-fevralgacha",
    eligibility_requirements="Open to undergraduate students interested in NLP. Basic Python required.",
    eligible_countries=["worldwide"],
    education_level=["undergraduate"],
    eligible_fields=["NLP", "ML", "CS"],
    benefits=["research mentorship", "certificate", "travel and housing covered"],
    country="Germany",
    summary="An 8-week in-person NLP research program in Berlin for undergraduates, fully funded.",
)

HIGH_FEE = make_opp(
    title="International Data Science Olympiad",
    type="competition",
    application_fee="$50",
    eligible_fields=["data science", "ML"],
)

IRRELEVANT_FIELD = make_opp(
    title="Global Nursing Excellence Scholarship",
    type="scholarship",
    eligibility_requirements="For students planning to study nursing or midwifery.",
    eligible_countries=["worldwide"],
    education_level=["undergraduate"],
    eligible_fields=["nursing", "midwifery", "healthcare"],
    benefits=["full tuition for a nursing degree"],
    summary="A scholarship covering tuition for nursing and midwifery degrees.",
)

# (name, opportunity, checks: list of (description, predicate on MatchResult))
CASES = [
    ("1. perfect match", PERFECT_MATCH, [
        ("passes filters", lambda r: r.passed_hard_filters),
        ("score >= 80", lambda r: (r.relevance_score or 0) >= 80),
        ("action highly_relevant", lambda r: r.action == "highly_relevant"),
    ]),
    ("2. deadline passed", DEADLINE_PASSED, [
        ("rejected", lambda r: not r.passed_hard_filters),
        ("deadline_passed failure", lambda r: any(f.startswith("deadline_passed") for f in r.filter_failures)),
    ]),
    ("3. EU citizens only", EU_ONLY, [
        ("rejected", lambda r: not r.passed_hard_filters),
        ("country_excluded failure", lambda r: any(f.startswith("country_excluded") for f in r.filter_failures)),
    ]),
    ("4. ambiguous education", AMBIGUOUS_EDUCATION, [
        ("passes filters", lambda r: r.passed_hard_filters),
        ("education_flag set", lambda r: r.education_flag is not None),
        ("action apply_but_email_first", lambda r: r.action == "apply_but_email_first"),
    ]),
    ("5. high fee ($50)", HIGH_FEE, [
        ("rejected", lambda r: not r.passed_hard_filters),
        ("fee_too_high failure", lambda r: any(f.startswith("fee_too_high") for f in r.filter_failures)),
    ]),
    ("6. irrelevant field (nursing)", IRRELEVANT_FIELD, [
        ("passes filters", lambda r: r.passed_hard_filters),
        ("score < 50", lambda r: r.relevance_score is not None and r.relevance_score < 50),
        ("action not_for_you", lambda r: r.action == "not_for_you"),
    ]),
]


async def main() -> None:
    passed = total = 0
    for name, opp, checks in CASES:  # sequential: gentler on the free-tier rate limit
        result: MatchResult = await match_opportunity(opp, MY_PROFILE)
        print(f"\n===== {name} =====")
        print(result.model_dump_json(indent=2))
        for description, check in checks:
            ok = check(result)
            passed += ok
            total += 1
            print(f"  {'PASS' if ok else 'FAIL'}  {description}")
    print(f"\n{passed}/{total} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
