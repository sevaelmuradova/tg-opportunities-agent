import asyncio
import logging

import httpx

from config import GEMINI_MODELS, REQUEST_TIMEOUT, gemini_api_url, get_api_key
from models import OpportunityData

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract structured data from Telegram posts about opportunities \
(scholarships, internships, competitions, grants, fellowships, programs, conferences, research positions).

Posts may be in English, Uzbek (Latin or Cyrillic), Russian, or a mix.

Rules:
- Only use information stated in the post. Never invent URLs, dates, fees, or amounts.
- Use null for missing optional fields and [] for missing lists.
- deadline_raw: copy the deadline text verbatim, in its original language.
- title and summary: write in the post's original language (the same one you report as \
original_language). Do not translate them.
- is_opportunity: false for news, results announcements, course/service ads, channel promos, \
or reminders. For non-opportunities still fill every field as best you can (type 'other' if unsure).
- type 'reminder': a digest/list post that only recaps several earlier opportunities or their \
upcoming deadlines (e.g. "deadlines ending this week") instead of announcing one opportunity. \
Reminders are always is_opportunity false.
- original_language: the dominant language; if heavily mixed, choose the one most of the text is in.
"""


def _to_gemini_schema(schema: dict) -> dict:
    """Convert a Pydantic JSON schema into Gemini's OpenAPI-style responseSchema."""
    if "anyOf" in schema:  # `X | None` -> X with nullable
        non_null = next(s for s in schema["anyOf"] if s.get("type") != "null")
        merged = {**non_null, **{k: v for k, v in schema.items() if k != "anyOf"}}
        return {**_to_gemini_schema(merged), "nullable": True}

    out: dict = {"type": schema["type"].upper()}
    if "enum" in schema:
        out["enum"] = schema["enum"]
    if "description" in schema:
        out["description"] = schema["description"]
    if "items" in schema:
        out["items"] = _to_gemini_schema(schema["items"])
    if "properties" in schema:
        names = list(schema["properties"])
        out["properties"] = {name: _to_gemini_schema(schema["properties"][name]) for name in names}
        out["required"] = names
        out["propertyOrdering"] = names
    return out


RESPONSE_SCHEMA = _to_gemini_schema(OpportunityData.model_json_schema())

MAX_ROUNDS = 3  # each round tries every model in GEMINI_MODELS once
BASE_DELAY = 5  # seconds to wait after a round where all models failed; doubles: 5, 10
# Overloaded / out of quota / temporary server error / model not available -> try the next model.
FALLBACK_STATUSES = {404, 429, 500, 502, 503, 504}


async def _post_with_retries(payload: dict) -> httpx.Response:
    """POST to Gemini, falling back through GEMINI_MODELS, then waiting and repeating the round."""
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for round_no in range(1, MAX_ROUNDS + 1):
            for model in GEMINI_MODELS:
                try:
                    response = await client.post(
                        gemini_api_url(model), headers={"x-goog-api-key": get_api_key()}, json=payload
                    )
                except httpx.TransportError as exc:  # includes timeouts and connection errors
                    last_error, reason = exc, type(exc).__name__
                else:
                    if response.status_code not in FALLBACK_STATUSES:
                        response.raise_for_status()  # e.g. 400 bad request, 403 bad key: fail immediately
                        if round_no > 1 or model != GEMINI_MODELS[0]:
                            logger.warning("Gemini answered using fallback model %s", model)
                        return response
                    last_error = httpx.HTTPStatusError(
                        f"HTTP {response.status_code} from {model}", request=response.request, response=response
                    )
                    reason = f"HTTP {response.status_code}"
                logger.warning("Gemini %s failed: %s (round %d/%d)", model, reason, round_no, MAX_ROUNDS)

            if round_no < MAX_ROUNDS:
                delay = BASE_DELAY * 2 ** (round_no - 1)
                logger.warning("All models failed, retrying in %ds", delay)
                await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


async def extract_opportunity(text: str) -> OpportunityData:
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    response = await _post_with_retries(payload)

    parts = response.json()["candidates"][0]["content"]["parts"]
    raw_json = "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))
    return OpportunityData.model_validate_json(raw_json)
