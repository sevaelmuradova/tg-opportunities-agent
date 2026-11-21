from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

OpportunityType = Literal[
    "scholarship", "internship", "competition", "grant", "fellowship",
    "program", "conference", "research", "reminder", "other",
]
EducationLevel = Literal["high school", "gap-year", "undergraduate", "masters", "phd", "anyone"]
Language = Literal["en", "uz", "ru", "other"]


class OpportunityData(BaseModel):
    title: str = Field(description="Official name of the opportunity, in the post's original language")
    type: OpportunityType
    deadline_raw: str | None = Field(description="Deadline exactly as written in the post")
    deadline_parsed: date | None = Field(
        default=None,
        description="The deadline as a YYYY-MM-DD date; null for rolling/open/no deadline",
    )
    eligibility_requirements: str | None = Field(
        description="Age limits, citizenship, GPA, language scores, experience, etc."
    )
    eligible_countries: list[str] = Field(
        description="Countries/regions whose citizens or residents may apply, in English, only if the post "
        "restricts this (e.g. ['EU'], ['Uzbekistan'], ['USA', 'Canada']); ['worldwide'] if explicitly "
        "open to all countries; [] if not stated"
    )
    education_level: list[EducationLevel] = Field(
        description="Levels that can apply; 'anyone' only if explicitly open to all; empty if not stated"
    )
    eligible_fields: list[str] = Field(description="Fields of study/work, e.g. ['ML', 'CS', 'engineering']")
    benefits: list[str] = Field(
        description="What participants get, e.g. '$500 cash prize', 'travel covered', 'publication'"
    )
    funding_details: str | None = Field(description="What specifically is funded/covered")
    country: str = Field(description="Host country, 'online', or 'unknown' if not stated")
    apply_url: str | None
    application_fee: str | None = Field(description="e.g. '$15' or 'free'; null if not mentioned")
    summary: str = Field(
        description="2-3 sentences in the post's original language: what it is, where (online/offline + location), key dates"
    )
    is_opportunity: bool = Field(description="False for news, updates, ads, or promos")
    original_language: Language = Field(description="Dominant language of the post")
    # Set from Telegram metadata (e.g. "@EduGrandsUz"), never by the LLM: hidden from Gemini's schema.
    source_channel: SkipJsonSchema[str | None] = None

    @field_validator("deadline_parsed", mode="before")
    @classmethod
    def _invalid_date_to_none(cls, value):
        """A malformed LLM date shouldn't fail the whole extraction; deadline_raw is still kept."""
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                return None
        return value


# ---------- Layer 2: matching ----------

MatchAction = Literal["highly_relevant", "worth_a_look", "not_for_you", "apply_but_email_first"]


class UserProfile(BaseModel):
    location: str
    age: int
    education_level: str
    fields_of_interest: list[str]
    skills: list[str]
    languages: list[str]
    max_application_fee: float | None = None  # USD; None means no limit
    no_fee_preferred: bool = False
    goals: str


class MatchResult(BaseModel):
    opportunity_title: str
    passed_hard_filters: bool
    filter_failures: list[str] = []  # e.g. ["deadline_passed: expired 2026-01-15"]
    relevance_score: int | None = Field(default=None, ge=0, le=100)  # only set if passed filters
    match_explanation: str
    education_flag: str | None = None  # eligibility concern, if any
    action: MatchAction
