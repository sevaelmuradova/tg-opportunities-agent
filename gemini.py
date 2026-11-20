"""Shared Gemini plumbing: Pydantic -> responseSchema conversion, retries/model fallback, structured calls."""
import asyncio
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel

from config import GEMINI_MODELS, REQUEST_TIMEOUT, gemini_api_url, get_api_key

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_ROUNDS = 3  # each round tries every model in GEMINI_MODELS once
BASE_DELAY = 5  # seconds to wait after a round where all models failed; doubles: 5, 10
# Overloaded / out of quota / temporary server error / model not available -> try the next model.
FALLBACK_STATUSES = {404, 429, 500, 502, 503, 504}


def to_gemini_schema(schema: dict) -> dict:
    """Convert a Pydantic JSON schema into Gemini's OpenAPI-style responseSchema."""
    if "anyOf" in schema:  # `X | None` -> X with nullable
        non_null = next(s for s in schema["anyOf"] if s.get("type") != "null")
        merged = {**non_null, **{k: v for k, v in schema.items() if k != "anyOf"}}
        return {**to_gemini_schema(merged), "nullable": True}

    out: dict = {"type": schema["type"].upper()}
    if "enum" in schema:
        out["enum"] = schema["enum"]
    if "description" in schema:
        out["description"] = schema["description"]
    if "items" in schema:
        out["items"] = to_gemini_schema(schema["items"])
    if "properties" in schema:
        names = list(schema["properties"])
        out["properties"] = {name: to_gemini_schema(schema["properties"][name]) for name in names}
        out["required"] = names
        out["propertyOrdering"] = names
    return out


async def post_with_retries(payload: dict) -> httpx.Response:
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


async def generate_structured(system_prompt: str, user_text: str, output_model: type[T]) -> T:
    """Ask Gemini for JSON matching `output_model` and return it as a validated instance."""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": to_gemini_schema(output_model.model_json_schema()),
        },
    }
    response = await post_with_retries(payload)

    parts = response.json()["candidates"][0]["content"]["parts"]
    raw_json = "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))
    return output_model.model_validate_json(raw_json)
