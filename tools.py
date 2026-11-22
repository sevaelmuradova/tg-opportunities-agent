"""Agent tools: plain async data operations over db.py. No LLM calls in here.

Every tool takes user_id first (injected by the agent, never chosen by the model) and returns a
JSON-serializable dict. Problems are returned as {"error": "..."} so the agent can explain them.
"""
from datetime import date

from pydantic import ValidationError

import db
from models import UserProfile

LIST_PROFILE_FIELDS = {"fields_of_interest", "skills", "languages"}
NONE_WORDS = {"none", "null", "no limit", "unlimited", ""}


def _brief(opp: dict) -> dict:
    """The fields worth showing in a list view."""
    keys = ("id", "title", "type", "deadline_raw", "deadline_parsed", "country", "application_fee",
            "summary", "apply_url", "relevance_score", "education_flag", "saved_notes")
    return {k: opp[k] for k in keys if opp.get(k) is not None}


async def _require_opportunity(opportunity_id: int) -> dict:
    opp = await db.get_opportunity_by_id(int(opportunity_id))
    if opp is None:
        raise LookupError(f"No opportunity with ID {opportunity_id}")
    return opp


async def search_opportunities(user_id: str, query: str = "", type: str | None = None, min_score: int = 50) -> dict:
    results = await db.search_opportunities(user_id, query or "", type, int(min_score))
    return {"query": query, "type": type, "min_score": min_score,
            "count": len(results), "opportunities": [_brief(o) for o in results]}


async def get_saved(user_id: str) -> dict:
    saved = await db.get_saved_opportunities(user_id)
    return {"count": len(saved), "opportunities": [_brief(o) for o in saved]}


async def get_deadlines(user_id: str, days: int = 7) -> dict:
    days = int(days)
    upcoming = await db.get_upcoming_deadlines(user_id, days)
    today = date.today()
    items = []
    for opp in upcoming:
        item = _brief(opp)
        item["days_left"] = (date.fromisoformat(opp["deadline_parsed"]) - today).days
        items.append(item)
    return {"today": today.isoformat(), "days": days, "count": len(items), "opportunities": items}


async def get_opportunity_details(opportunity_id: int) -> dict:
    try:
        opp = await _require_opportunity(opportunity_id)
    except LookupError as exc:
        return {"error": str(exc)}
    opp.pop("raw_text", None)  # the structured fields already cover it; keeps the prompt small
    return {"opportunity": opp}


async def _record(user_id: str, opportunity_id: int, action: str) -> dict:
    try:
        opp = await _require_opportunity(opportunity_id)
    except LookupError as exc:
        return {"error": str(exc)}
    await db.record_action(opp["id"], user_id, action)
    return {"ok": True, "action": action, "opportunity_id": opp["id"], "title": opp["title"],
            "deadline_parsed": opp["deadline_parsed"]}


async def save_opportunity(user_id: str, opportunity_id: int) -> dict:
    return await _record(user_id, opportunity_id, "saved")


async def dismiss_opportunity(user_id: str, opportunity_id: int) -> dict:
    return await _record(user_id, opportunity_id, "dismissed")


async def update_profile(user_id: str, field: str, value: str) -> dict:
    profile = await db.get_user_profile(user_id)
    if profile is None:
        return {"error": f"No profile stored for user '{user_id}'"}
    if field not in UserProfile.model_fields:
        return {"error": f"Unknown profile field '{field}'", "allowed_fields": list(UserProfile.model_fields)}

    data = profile.model_dump()
    value = value.strip()
    if field in LIST_PROFILE_FIELDS:
        if value.lower() in (item.lower() for item in data[field]):
            return {"ok": True, "unchanged": True, "field": field, "value": data[field],
                    "note": f"'{value}' was already there"}
        data[field] = [*data[field], value]
    elif field == "max_application_fee" and value.lower() in NONE_WORDS:
        data[field] = None
    else:
        data[field] = value  # Pydantic converts "20" -> 20, "true" -> True, "15.5" -> 15.5

    try:
        updated = UserProfile.model_validate(data)
    except ValidationError as exc:
        return {"error": f"Invalid value for {field}: {exc.errors()[0]['msg']}"}
    await db.store_user_profile(user_id, updated)
    return {"ok": True, "field": field, "old_value": getattr(profile, field), "new_value": getattr(updated, field)}


async def general_question(user_id: str, question: str) -> dict:
    """No data lookup beyond the profile: the agent's second LLM call answers using this context."""
    profile = await db.get_user_profile(user_id)
    return {"question": question, "profile": profile.model_dump() if profile else None}


TOOLS = {
    "search_opportunities": search_opportunities,
    "get_saved": get_saved,
    "get_deadlines": get_deadlines,
    "get_opportunity_details": get_opportunity_details,
    "save_opportunity": save_opportunity,
    "dismiss_opportunity": dismiss_opportunity,
    "update_profile": update_profile,
    "general_question": general_question,
}
