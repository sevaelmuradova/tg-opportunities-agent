"""Run: python test_bot.py  (offline: fake Telegram objects, temp DB, agent/pipeline stubbed - no API calls)"""
import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import InlineKeyboardMarkup, MessageEntity
from telegram.ext import ConversationHandler
from telethon.helpers import add_surrogate
from telethon.sessions import StringSession
from telethon.tl import types as tl

import bot
import db
import monitor
from models import MatchResult, OpportunityData

USER_ID = 111222333  # fake Telegram user id
USER = str(USER_ID)
results: list[tuple[str, bool]] = []


def check(description: str, ok: bool) -> None:
    results.append((description, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {description}")


# ---------------------------------------------------------------- fake Telegram objects


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.reply_text = AsyncMock()

    def last_reply(self) -> str:
        return self.reply_text.call_args.args[0]


def make_update(text: str = "", callback_data: str | None = None):
    message = FakeMessage(text)
    query = None
    if callback_data is not None:
        query = SimpleNamespace(data=callback_data, message=FakeMessage(), answer=AsyncMock(),
                                edit_message_reply_markup=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=USER_ID), effective_chat=SimpleNamespace(id=USER_ID),
                           message=message, callback_query=query)


def make_context(args=None):
    return SimpleNamespace(user_data={}, args=args or [], bot=AsyncMock(),
                           application=SimpleNamespace(bot_data={"allowed_users": set()}))


def buttons(markup: InlineKeyboardMarkup) -> list[tuple[str, str | None, str | None]]:
    return [(b.text, b.callback_data, b.url) for row in markup.inline_keyboard for b in row]


# ---------------------------------------------------------------- tests


async def test_start_flow() -> None:
    print("\n===== 1. /start creates a profile =====")
    ctx = make_context()
    state = await bot.start(make_update("/start"), ctx)
    for handler, answer in [(bot.got_education, "gap year"), (bot.got_interests, "ML, NLP, entrepreneurship"),
                            (bot.got_location, "Uzbekistan")]:
        state = await handler(make_update(answer), ctx)
    bad_age = make_update("nineteen")
    check("invalid age is asked again", await bot.got_age(bad_age, ctx) == bot.AGE)
    await bot.got_age(make_update("19"), ctx)
    state = await bot.got_goals(make_update("ML research and international programs"), ctx)
    check("conversation ends", state == ConversationHandler.END)

    profile = await db.get_user_profile(USER)
    check("profile stored", profile is not None)
    check("answers mapped correctly",
          profile.education_level == "gap_year" and profile.fields_of_interest == ["ML", "NLP", "entrepreneurship"]
          and profile.location == "Uzbekistan" and profile.age == 19)


async def test_channels() -> None:
    print("\n===== 2. /channels add and remove =====")
    ctx = make_context()
    ctx.bot.get_chat.return_value = SimpleNamespace(type="channel", username="EduGrandsUz")
    fake_monitor = SimpleNamespace(watch=AsyncMock(return_value=True))
    ctx.application.bot_data["monitor"] = fake_monitor

    for text in ("@edugrandsuz", "https://t.me/EduGrandsUz"):  # same channel, two spellings
        check(f"'{text}' matches the channel pattern", bot.CHANNEL_RE.match(text) is not None)
        await bot.add_channel_msg(make_update(text), ctx)
    check("added once, with Telegram's capitalisation", await db.get_user_channels(USER) == ["@EduGrandsUz"])
    check("monitor told to join it (once)", fake_monitor.watch.await_count == 1)
    check("all-channels list for Telethon", await db.get_all_monitored_channels() == ["@EduGrandsUz"])

    update = make_update("/channels")
    await bot.channels_cmd(update, ctx)
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    check("/channels lists it with a Remove button", ("✖ Remove @EduGrandsUz", "rmchan:@EduGrandsUz", None) in buttons(markup))

    ctx.bot.get_chat.return_value = SimpleNamespace(type="group", username="somegroup")
    not_channel = make_update("@somegroup")
    await bot.add_channel_msg(not_channel, ctx)
    check("groups are rejected", "isn't a public channel" in not_channel.message.last_reply())

    remove = make_update(callback_data="rmchan:@EduGrandsUz")
    await bot.button(remove, ctx)
    check("Remove button deletes it", await db.get_user_channels(USER) == [])
    check("channel message refreshed", remove.callback_query.edit_message_text.await_count == 1)


async def test_text_to_agent() -> None:
    print("\n===== 3. text message -> agent -> reply =====")
    ctx = make_context()
    update = make_update("show me ML internships")
    with patch("bot.handle_message", AsyncMock(return_value="Here is **#1** for you")) as agent:
        await bot.text_msg(update, ctx)
    check("agent called with user id + text", agent.await_args.args == (USER, "show me ML internships"))
    check("reply sent, Markdown bold rendered as HTML", update.message.last_reply() == "Here is <b>#1</b> for you")
    check("typing indicator shown", ctx.bot.send_chat_action.await_count == 1)

    failing = make_update("hello")
    with patch("bot.handle_message", AsyncMock(side_effect=RuntimeError("Gemini 503"))):
        await bot.text_msg(failing, ctx)
    check("agent failure -> friendly message, no crash", "try again" in failing.message.last_reply())


def seeded_result(opp_id: int) -> dict:
    return {
        "opportunity_id": opp_id, "match_result_id": 1,
        "opportunity": {"title": "Global ML Research Internship 2027", "deadline_parsed": "2026-12-05",
                        "deadline_raw": "5-dekabr"},
        "match": {"relevance_score": 88, "action": "highly_relevant", "education_flag": None,
                  "match_explanation": "Strong fit with your ML & NLP interests."},
    }


async def seed_opportunity() -> int:
    opp = OpportunityData(
        title="Global ML Research Internship 2027", type="internship", deadline_raw="5-dekabr",
        eligibility_requirements=None, eligible_countries=["worldwide"], education_level=[],
        eligible_fields=["ML"], benefits=["stipend"], funding_details=None, country="online",
        apply_url="https://example.com", application_fee="free", summary="Remote ML internship.",
        is_opportunity=True, original_language="en",
    )
    opp_id = await db.store_opportunity(opp, "seed post", "@EduGrandsUz")
    await db.store_match_result(opp_id, MatchResult(
        opportunity_title=opp.title, passed_hard_filters=True, relevance_score=88,
        match_explanation="Strong fit", action="highly_relevant"), USER)
    return opp_id


async def test_notification(opp_id: int) -> None:
    print("\n===== 4. notification format =====")
    fake_bot = AsyncMock()
    post = "🎓 ML Internship 🇬🇧\n\nRo'yxatdan o'tish: Havola"
    entities = [MessageEntity(type=MessageEntity.BOLD, offset=0, length=len(add_surrogate("🎓 ML Internship")))]
    await bot.send_notification(fake_bot, USER, post, entities, "@EduGrandsUz", 4521, seeded_result(opp_id))

    first, second = fake_bot.send_message.await_args_list
    check("two messages sent to the user", first.kwargs["chat_id"] == USER_ID == second.kwargs["chat_id"])
    check("message 1 = original post text, unchanged", first.kwargs["text"] == post)
    check("message 1 keeps the post's formatting entities", first.kwargs["entities"] == entities)
    check("message 1 has 'View in channel' -> t.me link",
          buttons(first.kwargs["reply_markup"]) == [("View in channel", None, "https://t.me/EduGrandsUz/4521")])
    text = second.kwargs["text"]
    check("message 2 has score, ID and explanation",
          "88/100" in text and f"#{opp_id}" in text and "Strong fit with your ML &amp; NLP interests." in text)
    check("message 2 buttons: Save / Dismiss / Details",
          [b[0] for b in buttons(second.kwargs["reply_markup"])] == ["Save", "Dismiss", "Details"])


async def test_callbacks(opp_id: int) -> None:
    print("\n===== 5. button callbacks update the DB =====")
    ctx = make_context()

    save = make_update(callback_data=f"save:{opp_id}")
    await bot.button(save, ctx)
    check("Save -> saved in DB", [o["id"] for o in await db.get_saved_opportunities(USER)] == [opp_id])
    markup = save.callback_query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    check("Save button now shows '✓ Saved'", buttons(markup)[0][0] == "✓ Saved")

    details = make_update(callback_data=f"details:{opp_id}")
    await bot.button(details, ctx)
    sent = details.callback_query.message.last_reply()
    check("Details -> full details sent", "Global ML Research Internship 2027" in sent and "https://example.com" in sent)

    dismiss = make_update(callback_data=f"dismiss:{opp_id}")
    await bot.button(dismiss, ctx)
    check("Dismiss -> no longer saved", await db.get_saved_opportunities(USER) == [])
    markup = dismiss.callback_query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    check("Dismiss button now shows 'Dismissed'", buttons(markup)[1][0] == "Dismissed")

    missing = make_update(callback_data="save:9999")
    await bot.button(missing, ctx)
    check("unknown opportunity -> alert, no crash", missing.callback_query.answer.await_args.kwargs.get("show_alert"))


async def test_monitor(opp_id: int) -> None:
    print("\n===== extra: monitor link expansion + threshold =====")
    text = "🇬🇧 Davlat: UK\nRo'yxatdan o'tish uchun: Havola\nSayt: https://x.uz"
    start = len(add_surrogate(text[: text.index("Havola")]))  # UTF-16 offset, after the flag emoji
    entities = [tl.MessageEntityTextUrl(offset=start, length=6, url="https://apply.example.com/reg"),
                tl.MessageEntityUrl(offset=len(add_surrogate(text)) - 12, length=12)]
    markup = tl.ReplyInlineMarkup(rows=[tl.KeyboardInlineButtonRow(buttons=[
        tl.KeyboardInlineButton(text="Rasmiy website", type=tl.InlineButtonTypeUrl(url="https://official.example.com")),
        tl.KeyboardInlineButton(text="Like", type=tl.InlineButtonTypeCallback(data=b"x")),  # no URL -> ignored
    ])])
    expanded = monitor.expand_links(text, entities, markup)
    check("hidden link made visible after the right word",
          "Havola (https://apply.example.com/reg)\n" in expanded)
    check("visible URLs left alone", expanded.count("https://x.uz") == 1)
    check("URL buttons appended", expanded.endswith("Rasmiy website: https://official.example.com"))
    converted = monitor.to_bot_entities(entities)
    check("entities converted for the bot (text_link keeps url)",
          converted[0].type == MessageEntity.TEXT_LINK and converted[0].url == "https://apply.example.com/reg")

    await db.add_channel(USER, "@EduGrandsUz")
    notify = AsyncMock()
    mon = monitor.ChannelMonitor(1, "0" * 32, None, notify=notify, threshold=60, session=StringSession())

    async def fake_pipeline(score):
        """Like process_post: stores a match row for the user and returns the result."""
        result = seeded_result(opp_id)
        result["match"]["relevance_score"] = score
        await db.store_match_result(opp_id, MatchResult(**{**result["match"], "opportunity_title": "x",
                                                             "passed_hard_filters": True}), USER)
        return result

    async with db_reset_matches(opp_id):
        with patch("monitor.process_post", AsyncMock(return_value=await fake_pipeline(75))) as pipeline:
            sent = await mon.handle_post("@edugrandsuz", 77, text, entities, markup)
    check("pipeline got expanded text + channel + user", pipeline.await_args.args == (expanded, "@edugrandsuz", USER))
    check("score 75 >= 60 -> notified", sent == 1 and notify.await_count == 1)
    check("notification gets the ORIGINAL text", notify.await_args.args[1] == text)

    async with db_reset_matches(opp_id):
        with patch("monitor.process_post", AsyncMock(return_value=await fake_pipeline(40))):
            check("score 40 < 60 -> not notified", await mon.handle_post("@EduGrandsUz", 78, text) == 0)

    with patch("monitor.process_post", AsyncMock(return_value=await fake_pipeline(90))):
        # no reset: this is the 2nd match row for the opportunity -> a repost / already seen
        check("already-seen opportunity -> not notified again", await mon.handle_post("@EduGrandsUz", 79, text) == 0)
    check("channel nobody monitors -> ignored", await mon.handle_post("@RandomChannel", 1, text) == 0)


class db_reset_matches:
    """Clear match_results for an opportunity so each monitor case starts as 'first time seen'."""
    def __init__(self, opp_id: int):
        self.opp_id = opp_id

    async def __aenter__(self):
        import aiosqlite
        async with aiosqlite.connect(os.environ["DB_PATH"]) as conn:
            await conn.execute("DELETE FROM match_results WHERE opportunity_id = ?", (self.opp_id,))
            await conn.commit()

    async def __aexit__(self, *exc):
        return False


async def main() -> None:
    await db.init_db()
    await test_start_flow()
    await test_channels()
    await test_text_to_agent()
    opp_id = await seed_opportunity()
    await test_notification(opp_id)
    await test_callbacks(opp_id)
    await test_monitor(opp_id)
    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} checks passed")


if __name__ == "__main__":
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    try:
        asyncio.run(main())
    finally:
        os.remove(path)
