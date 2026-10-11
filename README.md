# 🎧 Telegram Voice-Chat Audio Bot (with Owner Panel)

A private, paid-access Telegram bot that **plays audio in group/channel voice
chats** with **loop/repeat**, adjustable **speed (0.5x–2x)** and **volume**, plus a
full **owner panel in DM**: upload audio, manage the library, add admins, and
grant **time-limited subscriptions** to buyers.

Built with Python, `pyrogram` (via `pyrogrammod`), `pytgcalls` and FFmpeg.

---

## 1. Get your keys (5 minutes)

| Key | Where to get it |
|---|---|
| `API_ID` / `API_HASH` | https://my.telegram.org → *API development tools* → create an app |
| `BOT_TOKEN` | DM **@BotFather** → `/newbot` → copy the token |
| `OWNER_ID` | DM **@userinfobot** (or send `/id` to your own bot later) → your numeric ID |

## 2. Configure

```bash
cp .env.example .env
# edit .env: paste API_ID, API_HASH, BOT_TOKEN, OWNER_ID and your
# CONTACT_USERNAME (shown to buyers: "DM @you to buy access")
```

## 3. Install & run

Requires **Python 3.10+** and **FFmpeg** on the PATH
(`sudo apt install ffmpeg` / `brew install ffmpeg` / Windows: winget or a
manual install).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

The first start creates `bot.db` and the session file. Keep the process
running (screen/tmux/systemd) for 24/7 operation.

---

## 4. Using the bot

### Play in a voice chat (group **or** channel)
1. Add the bot to the group/channel and **promote it to admin**
   (it needs at least: delete/invite rights; for channel voice chats it must
   be able to manage them).
2. Start a voice chat in that chat (or let the bot start one —
   `AUTO_START_CALL=true`).
3. In the chat: `/join` → then **reply to any audio file with `/play`**.

Playback commands (owner, admins, subscribers):
`/play` `/pause` `/resume` `/skip` `/stop` `/leave` `/queue` `/status`
`/loop off|one|queue` `/speed 0.5-2.0` `/volume 0-200` `/seek <seconds>`
`/controls` — buttons for all of the above.

Loop modes:
- `off` — queue plays once, bot leaves when done
- `one` — the current track repeats forever
- `queue` — the whole queue cycles forever

### Owner panel (DM the bot, owner only)
`/panel` opens:
- **⬆ Upload audio** — send audio/voice/document files straight into the library
- **📚 Library** — play / queue / rename / delete any track in any connected chat
- **👥 Admins** — add (ID, @username or forwarded message) / remove co-admins
- **⏳ Subscriptions** — grant `user_id days` (extends automatically), revoke, view expiry
- **🌐 Chats** — join/leave the voice chat of every chat the bot is in

### Selling access (time-limited)
1. A stranger DMs the bot → they see: *"🔒 Private bot… contact @your_username"*.
2. They pay you (outside Telegram, however you like).
3. You open the panel → **Subscriptions → Grant** → send `<their_id> <days>`.
4. The bot DMs them that they're activated. Access expires automatically.

Getting a buyer's ID: they send `/id` to the bot (this command is open to
everyone on purpose).

---

## 5. Notes

- Audio files are stored as Telegram references in SQLite (`bot.db`) and
  cached under `downloads/media/track_<id>`; playback re-downloads
  automatically if the cache is cleared.
- Queue, loop mode, speed, volume and the current position survive restarts
  (`python main.py` resumes joined calls).
- Speed uses an FFmpeg `atempo` filter chain, so 0.5x–2.0x works with pitch
  preserved.
- Volume 100% is neutral; 0-200% is passed to the call engine.

## 6. Smoke test (no Telegram account needed)

```bash
python smoke_test.py
```

Validates config loading, database operations, the atempo chain, and the
generated FFmpeg commands.

---

## 💎 Userbot mode (how the audio actually plays)

Bots **cannot start voice chats** — Telegram blocks `phone.CreateGroupCall`
for bot accounts (`BOT_METHOD_INVALID`). Every music bot solves this with a
hidden **user account** that joins the call and streams the audio. This bot
does the same:

1. Start the bot (`start_bot.bat`).
2. DM the bot → send **`/login`** (owner only).
3. **Step 1/3:** send your phone number, e.g. `+919876543210`.
4. **Step 2/3:** send the login code Telegram sends your app.
5. **Step 3/3** (only if 2FA is on): send your cloud password.

Everything happens **inside the bot chat** — no terminal prompts. The session
is saved to `.env` automatically, so this is **one-time only**: every future
start logs the account in silently.

Manage it anytime with **`/userbot`** (status, disconnect) or the
**💎 Userbot** button in `/panel`.

> 🔒 **Account safety:** use your own account, keep playback in your own
> groups/channels, and avoid running massive public calls 24/7 — automated
> accounts can be limited if reported. The session string is stored locally
> on this PC only.

## ✨ Premium emoji

All bot messages and buttons use **animated custom-emoji** icons from the
extracted pack (`premium_emoji.py`, 115 IDs). Senders that may not use custom
emoji automatically fall back to the plain Unicode emoji — nothing ever
breaks. Disable with `PREMIUM_EMOJI=false` in `.env`.
