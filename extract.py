from datetime import date

from gemini import generate_structured
from models import OpportunityData

SYSTEM_PROMPT = """You extract structured data from Telegram posts about opportunities \
(scholarships, internships, competitions, grants, fellowships, programs, conferences, research positions).

Posts may be in English, Uzbek (Latin or Cyrillic), Russian, or a mix.

Rules:
- Only use information stated in the post. Never invent URLs, dates, fees, or amounts.
- Use null for missing optional fields and [] for missing lists.
- deadline_raw: copy the deadline text verbatim, in its original language.
- deadline_parsed: the same deadline as YYYY-MM-DD. Month names may be English, Uzbek \
(yanvar, fevral, mart, aprel, may, iyun, iyul, avgust, sentabr, oktabr, noyabr, dekabr; \
or Cyrillic) or Russian (января, февраля, марта, ...).
  - Full date: parse it ("January 15, 2027" -> 2027-01-15; "2027-yil 28-fevral" -> 2027-02-28).
  - Day + month without a year ("5-dekabr"): the next occurrence on or after today's date.
  - Month only ("dekabr", "until March"): the last day of the next occurrence of that month.
  - Rolling, open, no deadline, or not a date: null.
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
    # Today's date is needed to resolve year-less deadlines like "5-dekabr".
    prompt = f"Today's date: {date.today().isoformat()}\n\n{SYSTEM_PROMPT}"
    return await generate_structured(prompt, text, OpportunityData)
