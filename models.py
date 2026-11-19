from typing import Literal

from pydantic import BaseModel, Field

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
    eligibility_requirements: str | None = Field(
        description="Age limits, citizenship, GPA, language scores, experience, etc."
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
