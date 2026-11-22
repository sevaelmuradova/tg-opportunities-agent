"""Layer 4: conversational agent. String in -> string out, exactly 2 Gemini calls per message.

1. Tool selection: message + function declarations -> Gemini picks ONE tool and its arguments
   (native function calling, forced with mode ANY, so the output is always a valid tool call).
2. Code runs the tool (tools.py, pure data operations; user_id injected here, never by the model).
3. Response: message + tool name/args/result -> Gemini writes the reply in the user's language.
"""
import inspect
import json
import logging
from datetime import date

from pydantic import BaseModel

from gemini import post_with_retries
from tool_schemas import FUNCTION_DECLARATIONS
from tools import TOOLS

logger = logging.getLogger(__name__)

TOOL_LIST = "\n".join(f"- {d['name']}: {d['description']}" for d in FUNCTION_DECLARATIONS)

SELECTION_PROMPT = f"""You are an opportunity assistant inside a personal Telegram bot. The bot \
collects opportunities (scholarships, internships, competitions, grants, fellowships, programs, \
research) from Telegram channels, scores them against the user's profile and stores them.

Your job in this step: read the user's message and call exactly ONE tool that best serves it.

Tools:
{TOOL_LIST}

Rules:
- Pick ONE tool per message, never more.
- The user may write in Uzbek (Latin or Cyrillic), Russian or English. Tool arguments are in English.
- "#3", "3-raqam", "№3", "number 3" all refer to opportunity ID 3.
- You never need a user_id; it is filled in automatically.
- search_opportunities is for requests to SEE stored opportunities ("show me", "find", "any ML \
internships?"). Questions asking for advice about what kinds of opportunities suit someone \
("what's good for gap year students?", "should I apply to research programs?") are general_question.
- If nothing else fits (advice, explanations, greetings), use general_question.
"""

RESPONSE_PROMPT = """You are an opportunity assistant inside a personal Telegram bot. A tool has \
already been run for the user's message; write the reply to the user from its result.

Rules:
- LANGUAGE: reply in the language of the USER MESSAGE only. English message -> English reply; \
Uzbek -> Uzbek (same script, Latin or Cyrillic); Russian -> Russian. The tool result and the \
profile often contain Uzbek or Russian text: that must NOT change your reply language. Translate \
descriptive text (summaries, requirements, benefits) into the reply language; keep only official \
opportunity names and URLs unchanged. Don't mix scripts (no Cyrillic words in a Latin-Uzbek reply).
- Whenever you mention an opportunity, include its ID as "#<id>" so the user can act on it \
(e.g. "save #3").
- Use only facts from the tool result; never invent opportunities, dates, links or scores. For \
general_question, answer from general knowledge, tailored to the profile in the result.
- If the result is empty, say so plainly and suggest a next step (e.g. a broader search).
- If the result has an "error", explain it simply and say how to fix it.
- Short and scannable: plain text for Telegram, short lines or "-" lists, no tables, no headings.
- When dates matter, mention the deadline and how many days are left if given.
"""


class AgentTurn(BaseModel):
    """Everything that happened for one message (handy for tests and debugging)."""
    tool: str
    args: dict
    result: dict
    response: str


def _parts(response) -> list[dict]:
    return response.json()["candidates"][0]["content"].get("parts", [])


async def _select_tool(message: str) -> tuple[str, dict]:
    """LLM call 1: forced function call."""
    payload = {
        "systemInstruction": {"parts": [{"text": f"Today's date: {date.today().isoformat()}\n\n{SELECTION_PROMPT}"}]},
        "contents": [{"role": "user", "parts": [{"text": message}]}],
        "tools": [{"functionDeclarations": FUNCTION_DECLARATIONS}],
        "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
        "generationConfig": {"temperature": 0},
    }
    for part in _parts(await post_with_retries(payload)):
        if call := part.get("functionCall"):
            if call["name"] in TOOLS:
                return call["name"], call.get("args") or {}
            logger.warning("Model picked unknown tool %r", call["name"])
    logger.warning("No valid tool call returned; falling back to general_question")
    return "general_question", {"question": message}


async def _execute_tool(user_id: str, name: str, args: dict) -> dict:
    """Run a tool with only the arguments it accepts; user_id always comes from us."""
    fn = TOOLS[name]
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in args.items() if k in params and k != "user_id"}
    if "user_id" in params:
        kwargs["user_id"] = user_id
    try:
        return await fn(**kwargs)
    except (TypeError, ValueError) as exc:  # missing/garbled arguments from the model
        logger.warning("Tool %s failed with args %r: %s", name, args, exc)
        return {"error": f"Could not run {name}: {exc}"}


async def _write_response(message: str, tool: str, args: dict, result: dict) -> str:
    """LLM call 2: plain-text reply in the user's language."""
    user_text = (
        f"Today's date: {date.today().isoformat()}\n\n"
        f"TOOL CALLED: {tool}\nARGUMENTS: {json.dumps(args, ensure_ascii=False)}\n\n"
        f"TOOL RESULT:\n{json.dumps(result, ensure_ascii=False, indent=2, default=str)}\n\n"
        # The message goes last, right before the reply, so its language dominates.
        f"USER MESSAGE:\n{message}\n\n"
        "Write the reply to this message, in the same language as this message."
    )
    payload = {
        "systemInstruction": {"parts": [{"text": RESPONSE_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.3},
    }
    text = "".join(p["text"] for p in _parts(await post_with_retries(payload))
                   if "text" in p and not p.get("thought"))
    return text.strip()


async def run_agent(user_id: str, message: str) -> AgentTurn:
    tool, args = await _select_tool(message)
    result = await _execute_tool(user_id, tool, args)
    response = await _write_response(message, tool, args, result)
    return AgentTurn(tool=tool, args=args, result=result, response=response)


async def handle_message(user_id: str, message: str) -> str:
    """Main entry point: user's message in, agent's reply out."""
    return (await run_agent(user_id, message)).response
