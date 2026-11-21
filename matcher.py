"""Layer 2: decide whether an extracted opportunity is worth sending to the user.

Stage 1 - hard_filter(): pure Python, no LLM. Rejects only on clear-cut facts.
Stage 2 - llm_score(): Gemini scores relevance for opportunities that survive Stage 1.

Everything in Stage 1 is deliberately conservative: when a value can't be parsed with
confidence (e.g. no deadline_parsed, an unknown currency, an unfamiliar region),
the opportunity passes and Stage 2 judges it.
"""
import re
from datetime import date

from pydantic import BaseModel, Field

from gemini import generate_structured
from models import MatchAction, MatchResult, OpportunityData, UserProfile

# ---------------------------------------------------------------- text helpers


def _normalize(text: str) -> str:
    """Lowercase and unify the many apostrophe variants used in Uzbek Latin (o‘, oʻ, o’ -> o')."""
    return re.sub(r"[‘’ʻʼ`]", "'", text.lower())


# ---------------------------------------------------------------- fee parsing

_FREE_WORDS = ("free", "no fee", "bepul", "tekin", "бесплатн", "бепул")
# Approximate rates to USD; only used to compare against a limit, so rough is fine.
_CURRENCIES = [
    (("$", "usd", "dollar", "доллар"), 1.0),
    (("€", "eur", "евро", "yevro"), 1.08),
    (("£", "gbp", "pound", "фунт"), 1.27),
    (("₽", "rub", "руб"), 0.011),
    (("so'm", "uzs", "сум", "сўм"), 0.000078),
]


def parse_fee_usd(raw: str | None) -> float | None:
    """Convert application_fee text to USD. None if missing or unparseable (unknown currency)."""
    if not raw:
        return None
    text = _normalize(raw)
    if any(word in text for word in _FREE_WORDS):
        return 0.0

    m = re.search(r"\d[\d\s,.]*", text)
    if not m:
        return None
    number = re.sub(r"\s", "", m.group()).rstrip(".,")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", number):  # 1,000 / 1,000.50
        number = number.replace(",", "")
    else:  # 15,50 -> 15.50
        number = number.replace(",", ".")
    try:
        amount = float(number)
    except ValueError:
        return None
    if amount == 0:
        return 0.0

    for symbols, usd_rate in _CURRENCIES:
        if any(s in text for s in symbols):
            return amount * usd_rate
    return None  # number without a known currency: don't guess


# ---------------------------------------------------------------- country eligibility

# If any eligible_countries entry contains one of these, Uzbekistan can apply (or might) -> keep.
_INCLUDES_UZBEKISTAN = (
    "uzbekistan", "o'zbekiston", "узбекистан", "ўзбекистон", "worldwide", "global", "international",
    "all countries", "any country", "all nationalities", "any nationality", "central asia", "cis",
    "asia", "developing countries", "low- and middle-income", "lmic", "global south",
)
# Entries we are sure do NOT include Uzbekistan. Unknown entries are treated as "not explicit".
_EXCLUDES_UZBEKISTAN = {
    "eu", "european union", "eea", "schengen", "europe", "nato", "oecd", "g7", "nordic countries",
    "usa", "us", "united states", "uk", "united kingdom", "great britain", "canada", "mexico",
    "australia", "new zealand", "japan", "south korea", "korea", "china", "india", "pakistan",
    "bangladesh", "indonesia", "malaysia", "singapore", "philippines", "vietnam", "thailand",
    "germany", "france", "italy", "spain", "portugal", "netherlands", "belgium", "austria",
    "switzerland", "poland", "czech republic", "czechia", "hungary", "romania", "bulgaria", "greece",
    "sweden", "norway", "denmark", "finland", "ireland", "estonia", "latvia", "lithuania",
    "ukraine", "turkey", "türkiye", "israel", "egypt", "saudi arabia", "uae", "qatar",
    "nigeria", "kenya", "ghana", "south africa", "africa", "latin america", "brazil", "argentina",
    "russia", "kazakhstan", "kyrgyzstan", "tajikistan", "turkmenistan", "azerbaijan", "georgia",
    "armenia", "belarus", "moldova", "mongolia", "afghanistan", "iran",
}


def _clean_country(entry: str) -> str:
    entry = re.sub(r"\b(citizens?|residents?|nationals?|only|students?|from)\b", " ", _normalize(entry))
    return re.sub(r"\s+", " ", entry).strip(" .,()")


def country_excluded(opp: OpportunityData) -> bool:
    """True only when eligibility is explicitly limited to places that don't include Uzbekistan."""
    entries = [_clean_country(c) for c in opp.eligible_countries if c.strip()]
    if not entries:
        return False  # not stated -> not explicit
    if any(term in entry for entry in entries for term in _INCLUDES_UZBEKISTAN):
        return False
    return all(entry in _EXCLUDES_UZBEKISTAN for entry in entries)


# ---------------------------------------------------------------- enrollment proof

# Only documents that prove *current university enrollment*. A plain "transcript" is NOT here:
# a gap-year applicant has a school transcript, so that case goes to Stage 2 instead.
_ENROLLMENT_PROOF = (
    "enrollment letter", "enrolment letter", "proof of enrollment", "proof of enrolment",
    "enrollment certificate", "enrolment certificate", "certificate of enrollment",
    "certificate of enrolment", "enrollment verification", "enrolment verification",
    "university transcript", "transcript of records", "student id", "student card",
    "справка с места учебы", "справка об обучении", "студенческий билет",
    "o'qish joyidan ma'lumotnoma", "talabalik guvohnomasi", "talaba guvohnomasi",
)


# ---------------------------------------------------------------- Stage 1


def hard_filter(opp: OpportunityData, profile: UserProfile, today: date | None = None) -> list[str]:
    """Deterministic checks. Returns failure reasons; an empty list means the opportunity passed."""
    today = today or date.today()
    failures: list[str] = []

    if not opp.is_opportunity:
        failures.append(f"is_not_opportunity: post type is '{opp.type}'")

    # deadline_parsed is resolved by the LLM in Layer 1; None (rolling/unknown) passes to Stage 2.
    if opp.deadline_parsed is not None and opp.deadline_parsed < today:
        failures.append(f"deadline_passed: expired {opp.deadline_parsed.isoformat()}")

    if country_excluded(opp):
        failures.append(f"country_excluded: only for {', '.join(opp.eligible_countries)}")

    fee = parse_fee_usd(opp.application_fee)
    if fee is not None and profile.max_application_fee is not None and fee > profile.max_application_fee:
        failures.append(
            f"fee_too_high: {opp.application_fee} (~${fee:.2f}) > ${profile.max_application_fee:.2f}"
        )

    requirements = _normalize(opp.eligibility_requirements or "")
    if proof := next((p for p in _ENROLLMENT_PROOF if p in requirements), None):
        failures.append(f"education_hard_reject: requires proof of university enrollment ('{proof}')")

    return failures


# ---------------------------------------------------------------- Stage 2

SCORING_PROMPT = """You decide how relevant one opportunity is for one specific person.

You receive the person's profile and the opportunity as JSON (extracted earlier from a Telegram post; \
some text fields may be in Uzbek or Russian). The opportunity already passed hard filters (deadline, \
country, fee, proof of enrollment), so do not reject it for those reasons.

relevance_score (0-100):
- 80-100: strong match with their fields_of_interest, skills and goals, and they can realistically apply.
- 50-79: partial match, or clearly useful for their goals but not central.
- 0-49: a different field, the wrong level, or little value for their goals.
Weigh: field match; whether it builds toward university applications or professional ML experience; \
age limits vs their age; language requirements vs their languages; cost (they prefer no fee; \
check whether travel is funded if it is offline).

match_explanation: 2-3 sentences in English, talking to the person ("you"), with concrete reasons.

education_flag: the person is on a gap year (finished school, not enrolled at a university).
- If the opportunity is aimed at undergraduates / university students / "currently enrolled" \
students but does NOT explicitly say gap-year students or school graduates can apply, write one \
short sentence naming the concern and end it with "consider emailing to confirm gap year eligibility".
- If it is open to anyone, explicitly allows gap-year students or school graduates, or states no \
education level, use null.

action (follow these rules exactly):
- "apply_but_email_first": education_flag is set and relevance_score >= 50
- "highly_relevant": no education_flag and relevance_score >= 80
- "worth_a_look": no education_flag and relevance_score 50-79
- "not_for_you": relevance_score < 50
"""


class LLMScore(BaseModel):
    relevance_score: int = Field(ge=0, le=100, description="0-100 relevance for this person")
    match_explanation: str = Field(description="2-3 English sentences explaining the score")
    education_flag: str | None = Field(description="Gap-year eligibility concern, or null")
    action: MatchAction


async def llm_score(opp: OpportunityData, profile: UserProfile) -> LLMScore:
    """Gemini call: score an opportunity that already passed the hard filters."""
    user_text = (
        f"Today's date: {date.today().isoformat()}\n\n"
        f"PERSON PROFILE:\n{profile.model_dump_json(indent=2)}\n\n"
        f"OPPORTUNITY:\n{opp.model_dump_json(indent=2)}"
    )
    return await generate_structured(SCORING_PROMPT, user_text, LLMScore)


# ---------------------------------------------------------------- entry point


async def match_opportunity(opp: OpportunityData, profile: UserProfile) -> MatchResult:
    failures = hard_filter(opp, profile)
    if failures:
        return MatchResult(
            opportunity_title=opp.title,
            passed_hard_filters=False,
            filter_failures=failures,
            match_explanation="Rejected by hard filters: " + "; ".join(failures),
            action="not_for_you",
        )

    score = await llm_score(opp, profile)
    return MatchResult(
        opportunity_title=opp.title,
        passed_hard_filters=True,
        relevance_score=score.relevance_score,
        match_explanation=score.match_explanation,
        education_flag=score.education_flag,
        action=score.action,
    )
