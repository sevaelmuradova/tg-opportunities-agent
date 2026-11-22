# tg-opportunities-agent

A personal Telegram agent that watches opportunity channels (scholarships, internships,
competitions, grants), scores every new post against your profile with Gemini, and sends you the
relevant ones. You can also chat with it in Uzbek, Russian or English: "show me ML internships",
"save #3", "what deadlines do I have?".

## How it works

1. **Monitor** (`monitor.py`, Telethon): reads new posts from the channels you added, using your
   own Telegram account.
2. **Pipeline** (`pipeline.py`): pulls structured data out of each post with Gemini, scores it
   against your profile, and stores both in SQLite.
3. **Bot** (`bot.py`, python-telegram-bot): sends you posts that score at least
   `NOTIFICATION_THRESHOLD`, each with Save / Dismiss / Details buttons. It also answers your
   messages through the agent (`agent.py`).

## First-run setup

### 1. Create a bot with BotFather
1. In Telegram, open [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Choose a name and a username ending in `bot`.
3. Copy the **token** it gives you (looks like `123456789:AA...`).

### 2. Get `api_id` and `api_hash`
These let the monitor read channels with your account.
1. Log in at <https://my.telegram.org> with your phone number.
2. Open **API development tools** and create an app (any title and short name).
3. Copy **App api_id** (a number) and **App api_hash**.

### 3. Fill `.env`
Create a `.env` file in the project root. It is gitignored; never commit it:

```env
GEMINI_API_KEY=your-gemini-key

TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_PHONE=+998901234567
NOTIFICATION_THRESHOLD=60

# Recommended: only these Telegram user ids may use the bot (comma-separated).
# Get your id from @userinfobot.
TELEGRAM_ALLOWED_USERS=123456789
```

Then install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 4. First run: Telegram login (one time)

```bash
.venv/bin/python main.py
```

Telethon asks in the terminal for the login code Telegram sends you, and your 2FA password if you
have one. It then saves the login to `monitor.session`, so later runs start without asking.
**`monitor.session` gives full access to your Telegram account**: it's gitignored, so keep it
private and never share it.

### 5. Add channels
Open your bot in Telegram:
1. Send `/start` and answer the 5 profile questions.
2. Send `/channels`, then send a channel username such as `@EduGrandsUz` (or a `t.me/...` link).
   Your account joins the channel so it receives new posts. Remove a channel with the button
   under `/channels`.

### 6. It's monitoring
Keep `main.py` running. Every new text post in your channels goes through extraction and scoring.
When a post scores at least `NOTIFICATION_THRESHOLD` (default 60), you get the original post
(with a **View in channel** button), then its score, an explanation, and **Save / Dismiss /
Details** buttons.

## Commands
- `/start`: set up or redo your profile
- `/channels`: list, add and remove monitored channels
- `/saved`: your saved opportunities
- `/deadlines [days]`: saved opportunities due soon (default 7 days)
- `/cancel`: stop the profile setup
- Anything else is a message to the agent, for example "add Python to my skills" or
  "tell me more about #4".

## Notes
- **Text only:** posts with only a photo or video and no text are skipped. The notification
  repeats the post's text and formatting, not its media; use **View in channel** to see those.
- **No repeat notifications:** the same opportunity posted again, even in another channel, is
  not sent twice.
- **Access:** if `TELEGRAM_ALLOWED_USERS` is empty, anyone who finds the bot can use it, spend
  your Gemini quota, and make your account join channels. Set it.

## Tests
```bash
.venv/bin/python test_bot.py         # offline, no API calls
.venv/bin/python test_extraction.py  # these call the real Gemini API
.venv/bin/python test_matching.py
.venv/bin/python test_db.py
.venv/bin/python test_agent.py
```
