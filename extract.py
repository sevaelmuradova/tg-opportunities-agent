from gemini import generate_structured
from models import OpportunityData

SYSTEM_PROMPT = """You extract structured data from Telegram posts about opportunities \
(scholarships, internships, competitions, grants, fellowships, programs, conferences, research positions).

Posts may be in English, Uzbek (Latin or Cyrillic), Russian, or a mix.

Rules:
- Only use information stated in the post. Never invent URLs, dates, fees, or amounts.
- Use null for missing optional fields and [] for missing lists.
- deadline_raw: copy the deadline text verbatim, in its original language.
- title and summary: write in the post's original language (the same one you report as \
original_language). Do not translate them.
- eligible_countries: write country/region names in English. Fill it only when the post limits who \
can apply by citizenship or residence; ['worldwide'] when it explicitly says all countries/nationalities.
- is_opportunity: false for news, results announcements, course/service ads, channel promos, \
or reminders. For non-opportunities still fill every field as best you can (type 'other' if unsure).
- type 'reminder': a digest/list post that only recaps several earlier opportunities or their \
upcoming deadlines (e.g. "deadlines ending this week") instead of announcing one opportunity. \
Reminders are always is_opportunity false.
- original_language: the dominant language; if heavily mixed, choose the one most of the text is in.
"""


async def extract_opportunity(text: str) -> OpportunityData:
    return await generate_structured(SYSTEM_PROMPT, text, OpportunityData)
