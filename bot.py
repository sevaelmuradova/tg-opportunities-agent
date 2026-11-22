"""Layer 5, Part B: bot interface (python-telegram-bot v21+, async).

Commands: /start (profile setup), /channels, /saved, /deadlines, /cancel, /help.
Plain text -> agent.handle_message(). @channel or t.me link -> add a monitored channel.
send_notification() is called by the monitor for relevant new posts.

user_id everywhere = the Telegram user id as a string (in private chats it is also the chat id).
"""
import html
import logging
import re

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

import db
import tools
from agent import handle_message
from models import UserProfile

logger = logging.getLogger(__name__)

EDUCATION, INTERESTS, LOCATION, AGE, GOALS = range(5)
EDUCATION_CHOICES = {
    "high school": "high_school", "gap year": "gap_year", "undergraduate": "undergraduate",
    "masters": "masters", "phd": "phd", "working": "working",
}
CHANNEL_RE = re.compile(r"^\s*(?:@|(?:https?://)?t\.me/)([A-Za-z][A-Za-z0-9_]{3,31})/?\s*$")
MAX_MESSAGE = 4000  # Telegram's limit is 4096 characters

# ---------------------------------------------------------------- helpers


def _uid(update: Update) -> str:
    return str(update.effective_user.id)


def _allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If TELEGRAM_ALLOWED_USERS is set, only those Telegram ids may use the bot."""
    allowed = context.application.bot_data.get("allowed_users")
    return not allowed or update.effective_user.id in allowed


def to_html(text: str) -> str:
    """Agent replies may contain Markdown **bold**; Telegram HTML is the safest way to render it."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text), flags=re.S)


def _chunks(text: str) -> list[str]:
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > MAX_MESSAGE:  # one huge line: hard split
            parts.append(line[:MAX_MESSAGE])
            line = line[MAX_MESSAGE:]
        if len(current) + len(line) + 1 > MAX_MESSAGE:
            parts.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return [p for p in parts + [current] if p.strip()]


async def _send(message, text: str, reply_markup=None) -> None:
    """Reply with Markdown-ish bold rendered; fall back to plain text if Telegram rejects the HTML."""
    chunks = _chunks(text)
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            await message.reply_text(to_html(chunk), parse_mode=ParseMode.HTML, reply_markup=markup,
                                     disable_web_page_preview=True)
        except BadRequest:
            await message.reply_text(chunk, reply_markup=markup, disable_web_page_preview=True)


def action_keyboard(opp_id: int, state: str | None = None) -> InlineKeyboardMarkup:
    """[Save] [Dismiss] [Details]; the chosen action is shown as done."""
    save = InlineKeyboardButton("✓ Saved" if state == "saved" else "Save",
                                callback_data="noop" if state == "saved" else f"save:{opp_id}")
    dismiss = InlineKeyboardButton("Dismissed" if state == "dismissed" else "Dismiss",
                                   callback_data="noop" if state == "dismissed" else f"dismiss:{opp_id}")
    return InlineKeyboardMarkup([[save, dismiss, InlineKeyboardButton("Details", callback_data=f"details:{opp_id}")]])


def format_opportunity_line(opp: dict) -> str:
    deadline = opp.get("deadline_parsed") or opp.get("deadline_raw") or "no deadline"
    parts = [f"#{opp['id']} **{opp['title']}**", f"   deadline: {deadline}"]
    if "days_left" in opp:
        parts[-1] += f" ({opp['days_left']} days left)"
    if opp.get("apply_url"):
        parts.append(f"   {opp['apply_url']}")
    return "\n".join(parts)


def format_details(opp: dict) -> str:
    def join(items):
        return ", ".join(items) if items else None

    rows = [
        ("Type", opp.get("type")),
        ("Deadline", " / ".join(str(v) for v in (opp.get("deadline_raw"), opp.get("deadline_parsed")) if v) or None),
        ("Country", opp.get("country")),
        ("Fee", opp.get("application_fee")),
        ("Education", join(opp.get("education_level"))),
        ("Fields", join(opp.get("eligible_fields"))),
        ("Eligible countries", join(opp.get("eligible_countries"))),
        ("Requirements", opp.get("eligibility_requirements")),
        ("Benefits", join(opp.get("benefits"))),
        ("Funding", opp.get("funding_details")),
        ("Apply", opp.get("apply_url")),
        ("Source", opp.get("source_channel")),
    ]
    lines = [f"#{opp['id']} **{opp['title']}**", "", opp.get("summary") or "", ""]
    lines += [f"**{label}:** {value}" for label, value in rows if value]
    return "\n".join(lines).strip()


def format_score(result: dict) -> str:
    match, opp = result["match"], result["opportunity"]
    lines = [
        f"🎯 Relevance {match['relevance_score']}/100 · {match['action'].replace('_', ' ')}",
        f"#{result['opportunity_id']} {opp['title']}",
    ]
    if opp.get("deadline_parsed") or opp.get("deadline_raw"):
        lines.append(f"Deadline: {opp.get('deadline_parsed') or opp.get('deadline_raw')}")
    lines += ["", match["match_explanation"]]
    if match.get("education_flag"):
        lines += ["", f"⚠️ {match['education_flag']}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- /start: profile setup


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _allowed(update, context):
        await update.message.reply_text("Sorry, this is a private bot.")
        return ConversationHandler.END
    context.user_data["profile"] = {}
    keyboard = [["high school", "gap year"], ["undergraduate", "masters"], ["phd", "working"]]
    await update.message.reply_text(
        "Hi! I watch opportunity channels and send you the posts that fit you.\n\n"
        "Let's set up your profile (5 questions, /cancel to stop).\n\n"
        "1/5 What's your education level?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return EDUCATION


async def got_education(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    context.user_data["profile"]["education_level"] = EDUCATION_CHOICES.get(answer, answer)
    await update.message.reply_text(
        "2/5 What fields interest you? Separate with commas, e.g. ML, NLP, entrepreneurship",
        reply_markup=ReplyKeyboardRemove(),
    )
    return INTERESTS


async def got_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    interests = [item.strip() for item in re.split(r"[,;\n]", update.message.text) if item.strip()]
    if not interests:
        await update.message.reply_text("Please list at least one field, e.g. ML, CS")
        return INTERESTS
    context.user_data["profile"]["fields_of_interest"] = interests
    await update.message.reply_text("3/5 Which country do you live in?")
    return LOCATION


async def got_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["profile"]["location"] = update.message.text.strip()
    await update.message.reply_text("4/5 How old are you?")
    return AGE


async def got_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or not 10 <= int(text) <= 100:
        await update.message.reply_text("Please send your age as a number, e.g. 19")
        return AGE
    context.user_data["profile"]["age"] = int(text)
    await update.message.reply_text(
        "5/5 What are your goals? One or two sentences, e.g. \"international ML programs and research "
        "to strengthen my university applications\""
    )
    return GOALS


async def got_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = _uid(update)
    answers = context.user_data.pop("profile", {})
    existing = await db.get_user_profile(user_id)
    # Keep settings /start doesn't ask about (skills, languages, fee limits) from an earlier profile.
    kept = existing.model_dump(include={"skills", "languages", "max_application_fee", "no_fee_preferred"}) \
        if existing else {"skills": [], "languages": []}
    profile = UserProfile(**kept, **answers, goals=update.message.text.strip())
    await db.store_user_profile(user_id, profile)
    await update.message.reply_text(
        f"Saved ✓\n\nEducation: {profile.education_level}\nInterests: {', '.join(profile.fields_of_interest)}\n"
        f"Location: {profile.location}\nAge: {profile.age}\nGoals: {profile.goals}\n\n"
        "Next: add channels to watch with /channels. You can also just talk to me, e.g. "
        "\"add Python to my skills\", \"show me ML internships\", \"what deadlines do I have?\""
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("profile", None)
    await update.message.reply_text("Cancelled. Send /start to try again.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------------------------------------------------------- /channels


async def _channels_view(user_id: str) -> tuple[str, InlineKeyboardMarkup | None]:
    channels = await db.get_user_channels(user_id)
    if channels:
        text = "Monitored channels:\n" + "\n".join(f"• {c}" for c in channels)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"✖ Remove {c}", callback_data=f"rmchan:{c}")]
                                       for c in channels])
    else:
        text, markup = "You're not monitoring any channels yet.", None
    return text + "\n\nTo add one, send its username, e.g. @EduGrandsUz", markup


async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    text, markup = await _channels_view(_uid(update))
    await update.message.reply_text(text, reply_markup=markup)


async def add_channel_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User sent '@SomeChannel' or a t.me link: validate it's a public channel, then monitor it."""
    if not _allowed(update, context):
        return
    username = CHANNEL_RE.match(update.message.text)[1]
    try:
        chat = await context.bot.get_chat(f"@{username}")
    except TelegramError:
        await update.message.reply_text(f"I couldn't find a public channel @{username}. Check the spelling.")
        return
    if chat.type != "channel" or not chat.username:
        await update.message.reply_text(f"@{username} isn't a public channel.")
        return

    channel = f"@{chat.username}"  # Telegram's own capitalisation
    added = await db.add_channel(_uid(update), channel)
    monitor = context.application.bot_data.get("monitor")
    if added and monitor is not None:
        await monitor.watch(channel)
    await update.message.reply_text(
        f"Now monitoring {channel} ✓ New posts will be checked against your profile."
        if added else f"You're already monitoring {channel}."
    )


# ---------------------------------------------------------------- /saved, /deadlines, /help


async def saved_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    result = await tools.get_saved(_uid(update))
    if not result["count"]:
        await update.message.reply_text("You haven't saved anything yet. Tap Save under a notification.")
        return
    body = "\n\n".join(format_opportunity_line(o) for o in result["opportunities"])
    await _send(update.message, f"Saved ({result['count']}):\n\n{body}")


async def deadlines_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/deadlines [days] - saved opportunities due within N days (default 7)."""
    if not _allowed(update, context):
        return
    days = int(context.args[0]) if context.args and context.args[0].isdigit() else 7
    result = await tools.get_deadlines(_uid(update), days)
    if not result["count"]:
        await update.message.reply_text(f"No saved deadlines in the next {days} days. Try /deadlines 30")
        return
    body = "\n\n".join(format_opportunity_line(o) for o in result["opportunities"])
    await _send(update.message, f"Deadlines in the next {days} days:\n\n{body}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start - set up your profile\n/channels - channels I monitor for you\n/saved - your saved "
        "opportunities\n/deadlines [days] - upcoming deadlines (default 7)\n\nOr just write to me in "
        "Uzbek, Russian or English."
    )


# ---------------------------------------------------------------- free text -> agent


async def text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await handle_message(_uid(update), update.message.text)
    except Exception:
        logger.exception("Agent failed")
        reply = "Sorry, something went wrong (the AI service may be busy). Please try again in a minute."
    await _send(update.message, reply or "…")


# ---------------------------------------------------------------- inline buttons


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _allowed(update, context):
        await query.answer("Private bot")
        return
    user_id = _uid(update)
    kind, _, value = (query.data or "").partition(":")

    if kind in ("save", "dismiss"):
        tool = tools.save_opportunity if kind == "save" else tools.dismiss_opportunity
        result = await tool(user_id, int(value))
        if "error" in result:
            await query.answer(result["error"], show_alert=True)
            return
        state = "saved" if kind == "save" else "dismissed"
        await query.answer("Saved ✓" if kind == "save" else "Dismissed")
        await query.edit_message_reply_markup(reply_markup=action_keyboard(int(value), state))
    elif kind == "details":
        await query.answer()
        result = await tools.get_opportunity_details(int(value))
        text = result["error"] if "error" in result else format_details(result["opportunity"])
        await _send(query.message, text)
    elif kind == "rmchan":
        removed = await db.remove_channel(user_id, value)
        await query.answer(f"Removed {value}" if removed else "Already removed")
        text, markup = await _channels_view(user_id)
        await query.edit_message_text(text, reply_markup=markup)
    else:  # "noop": a button showing a finished state
        await query.answer()


# ---------------------------------------------------------------- notifications (called by monitor)


async def send_notification(bot, user_id: str, post_text: str, post_entities: list[MessageEntity],
                            channel_username: str, message_id: int, result: dict) -> None:
    """1) the original post, formatting intact, + 'View in channel'; 2) score/explanation + actions."""
    chat_id = int(user_id)
    link = f"https://t.me/{channel_username.lstrip('@')}/{message_id}"
    view = InlineKeyboardMarkup([[InlineKeyboardButton("View in channel", url=link)]])
    try:
        await bot.send_message(chat_id=chat_id, text=post_text, entities=post_entities or None, reply_markup=view)
    except BadRequest:  # an entity Telegram won't accept from a bot: send the text without formatting
        logger.warning("Resending post %s without entities", link)
        await bot.send_message(chat_id=chat_id, text=post_text, reply_markup=view)
    await bot.send_message(
        chat_id=chat_id, text=to_html(format_score(result)), parse_mode=ParseMode.HTML,
        reply_markup=action_keyboard(result["opportunity_id"]),
    )


# ---------------------------------------------------------------- wiring


def build_application(token: str, allowed_users: set[int] | None = None) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["allowed_users"] = allowed_users or set()

    text_only = filters.TEXT & ~filters.COMMAND
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            EDUCATION: [MessageHandler(text_only, got_education)],
            INTERESTS: [MessageHandler(text_only, got_interests)],
            LOCATION: [MessageHandler(text_only, got_location)],
            AGE: [MessageHandler(text_only, got_age)],
            GOALS: [MessageHandler(text_only, got_goals)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("saved", saved_cmd))
    app.add_handler(CommandHandler("deadlines", deadlines_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.Regex(CHANNEL_RE) & text_only, add_channel_msg))
    app.add_handler(MessageHandler(text_only, text_msg))
    app.add_handler(CallbackQueryHandler(button))
    return app
