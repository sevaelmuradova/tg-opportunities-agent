"""Layer 5, Part A: channel monitor (Telethon, logged in as the owner's personal account).

New post in a monitored channel -> expand hidden links -> pipeline.process_post() for every user
monitoring that channel -> notify via the bot when relevance_score >= threshold.

Telethon only receives posts from channels the account has joined, so the monitor joins every
monitored channel (on startup and when a channel is added via /channels).
"""
import logging
from typing import Awaitable, Callable

from telegram import MessageEntity
from telethon import TelegramClient, events
from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl import types as tl
from telethon.tl.functions.channels import JoinChannelRequest

import db
from pipeline import process_post

logger = logging.getLogger(__name__)

# notify(user_id, post_text, post_entities, channel_username, message_id, pipeline_result)
Notifier = Callable[[str, str, list[MessageEntity], str, int, dict], Awaitable[None]]

# Telethon (MTProto) entity -> Bot API entity type, so the bot can resend the post with its formatting.
_ENTITY_TYPES = {
    tl.MessageEntityBold: MessageEntity.BOLD,
    tl.MessageEntityItalic: MessageEntity.ITALIC,
    tl.MessageEntityUnderline: MessageEntity.UNDERLINE,
    tl.MessageEntityStrike: MessageEntity.STRIKETHROUGH,
    tl.MessageEntitySpoiler: MessageEntity.SPOILER,
    tl.MessageEntityCode: MessageEntity.CODE,
    tl.MessageEntityPre: MessageEntity.PRE,
    tl.MessageEntityTextUrl: MessageEntity.TEXT_LINK,
    tl.MessageEntityUrl: MessageEntity.URL,
    tl.MessageEntityMention: MessageEntity.MENTION,
    tl.MessageEntityHashtag: MessageEntity.HASHTAG,
    tl.MessageEntityCashtag: MessageEntity.CASHTAG,
    tl.MessageEntityEmail: MessageEntity.EMAIL,
    tl.MessageEntityPhone: MessageEntity.PHONE_NUMBER,
    tl.MessageEntityBlockquote: MessageEntity.BLOCKQUOTE,
}


def expand_links(text: str, entities: list | None, reply_markup=None) -> str:
    """Make hidden URLs visible for the LLM: 'Havola' linked to X -> 'Havola (X)'.

    Entity offsets are in UTF-16 code units (an emoji like 🇬🇧 counts as 4), so we work on the
    surrogate-expanded string. Insert from the end so earlier offsets stay valid. URL buttons under
    the post (e.g. "Rasmiy website") are appended as "text: url" lines.
    """
    s = add_surrogate(text)
    links = [e for e in entities or [] if isinstance(e, tl.MessageEntityTextUrl)]
    for e in sorted(links, key=lambda e: e.offset, reverse=True):
        end = e.offset + e.length
        visible = del_surrogate(s[e.offset:end]).strip()
        if e.url.rstrip("/") in visible:  # the URL is already readable
            continue
        s = s[:end] + add_surrogate(f" ({e.url})") + s[end:]
    expanded = del_surrogate(s)

    buttons = []
    if isinstance(reply_markup, tl.ReplyInlineMarkup):
        for row in reply_markup.rows:
            for b in row.buttons:
                # Older Telethon layers: KeyboardButtonUrl(text, url).
                # Newer (1.46+): KeyboardInlineButton(text, type=InlineButtonTypeUrl(url)).
                url = getattr(b, "url", None) or getattr(getattr(b, "type", None), "url", None)
                if url:
                    buttons.append(f"{b.text}: {url}")
    if buttons:
        expanded += "\n\n" + "\n".join(buttons)
    return expanded


def to_bot_entities(entities: list | None) -> list[MessageEntity]:
    """Convert Telethon entities to Bot API ones (same UTF-16 offsets). Unsupported types are dropped."""
    result = []
    for e in entities or []:
        kind = _ENTITY_TYPES.get(type(e))
        if kind is None:
            continue
        result.append(MessageEntity(
            type=kind, offset=e.offset, length=e.length,
            url=getattr(e, "url", None) if kind == MessageEntity.TEXT_LINK else None,
            language=(getattr(e, "language", None) or None) if kind == MessageEntity.PRE else None,
        ))
    return result


class ChannelMonitor:
    def __init__(self, api_id: int, api_hash: str, phone: str | None, notify: Notifier,
                 threshold: int = 60, session="monitor"):
        self.client = TelegramClient(session, api_id, api_hash)
        self.phone = phone
        self.notify = notify
        self.threshold = threshold

    async def start(self) -> None:
        """Log in (first run asks for the login code in the terminal), listen, join monitored channels."""
        await self.client.start(phone=self.phone)
        self.client.add_event_handler(
            self._on_new_message, events.NewMessage(func=lambda e: e.is_channel and not e.is_group)
        )
        for channel in await db.get_all_monitored_channels():
            await self.watch(channel)
        logger.info("Monitor started")

    async def watch(self, channel_username: str) -> bool:
        """Join a channel so its new posts reach us. Safe to call if already joined."""
        try:
            await self.client(JoinChannelRequest(channel_username))
            logger.info("Watching %s", channel_username)
            return True
        except Exception as exc:  # invalid/private channel, flood wait, ...
            logger.warning("Could not join %s: %s", channel_username, exc)
            return False

    async def _on_new_message(self, event) -> None:
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        message = event.message
        if not username or not message.message:  # no public username, or a media post without text
            return
        await self.handle_post(f"@{username}", message.id, message.message, message.entities, message.reply_markup)

    async def handle_post(self, channel: str, message_id: int, text: str, entities=None, reply_markup=None) -> int:
        """Process one post for every subscriber. Returns how many notifications were sent."""
        subscribers = await db.get_channel_subscribers(channel)
        if not subscribers:
            return 0
        llm_text = expand_links(text, entities, reply_markup)
        bot_entities = to_bot_entities(entities)

        sent = 0
        for user_id in subscribers:
            try:
                result = await process_post(llm_text, channel, user_id)
            except LookupError:  # this user never finished /start (no profile yet)
                logger.info("Skipping %s for user %s: no profile", channel, user_id)
                continue
            except Exception:  # Gemini down, bad output, ... -> don't kill the monitor
                logger.exception("Pipeline failed for %s/%s (user %s)", channel, message_id, user_id)
                continue

            score = result["match"]["relevance_score"]
            logger.info("%s/%s -> #%s score=%s (user %s)", channel, message_id, result["opportunity_id"], score, user_id)
            if score is None or score < self.threshold:
                continue
            if await db.count_match_results(result["opportunity_id"], user_id) > 1:
                continue  # already seen (same post or a repost in another channel): don't notify twice
            await self.notify(user_id, text, bot_entities, channel, message_id, result)
            sent += 1
        return sent
