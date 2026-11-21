"""End-to-end pipeline: post text -> extract -> store opportunity -> match -> store match."""
from db import get_user_profile, store_match_result, store_opportunity
from extract import extract_opportunity
from matcher import match_opportunity


async def process_post(text: str, source_channel: str | None, user_id: str) -> dict:
    """Run one post through Layers 1-3. Requires init_db() and a stored profile for user_id."""
    profile = await get_user_profile(user_id)
    if profile is None:
        raise LookupError(f"No profile stored for user '{user_id}'; call store_user_profile() first")

    opportunity = await extract_opportunity(text)
    opportunity = opportunity.model_copy(update={"source_channel": source_channel})
    opportunity_id = await store_opportunity(opportunity, text, source_channel)

    match = await match_opportunity(opportunity, profile)
    match_result_id = await store_match_result(opportunity_id, match, user_id)

    return {
        "opportunity_id": opportunity_id,
        "match_result_id": match_result_id,
        "opportunity": opportunity.model_dump(mode="json"),
        "match": match.model_dump(mode="json"),
    }
