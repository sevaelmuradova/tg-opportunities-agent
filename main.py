"""Entry point: init DB, then run the bot (python-telegram-bot) and the channel monitor (Telethon)
in one asyncio loop.  Run: .venv/bin/python main.py
"""
import asyncio
import logging
import os
from functools import partial

import config  # noqa: F401  -- loads .env into os.environ
import db
from bot import build_application, send_notification
from monitor import ChannelMonitor

REQUIRED = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "GEMINI_API_KEY")


def _settings() -> dict:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)} (see README.md)")
    allowed = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_USERS", "").replace(" ", "").split(",") if x}
    return {
        "token": os.environ["TELEGRAM_BOT_TOKEN"],
        "api_id": int(os.environ["TELEGRAM_API_ID"]),
        "api_hash": os.environ["TELEGRAM_API_HASH"],
        "phone": os.environ.get("TELEGRAM_PHONE"),
        "threshold": int(os.environ.get("NOTIFICATION_THRESHOLD", "60")),
        "allowed": allowed,
    }


async def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # PTB polls constantly; keep the log readable
    settings = _settings()
    if not settings["allowed"]:
        logging.warning("TELEGRAM_ALLOWED_USERS is empty: anyone who finds the bot can use it")

    await db.init_db()
    app = build_application(settings["token"], settings["allowed"])
    monitor = ChannelMonitor(
        settings["api_id"], settings["api_hash"], settings["phone"],
        notify=partial(send_notification, app.bot), threshold=settings["threshold"],
    )
    app.bot_data["monitor"] = monitor  # so /channels can join newly added channels

    await monitor.start()  # first run: asks for the Telegram login code in this terminal
    async with app:
        await app.start()
        await app.updater.start_polling()
        logging.info("Bot and monitor running. Ctrl+C to stop.")
        try:
            await monitor.client.run_until_disconnected()
        finally:
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
