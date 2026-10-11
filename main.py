"""Entrypoint: starts the userbot engine + the bot control panel together.

Startup order:
1. FFmpeg check (playback is impossible without it).
2. Userbot login from the saved session (silent). If there is no session yet
   the bot still starts and the owner connects the account with /login in DM.
3. Bot client + handlers (commands, owner panel).
4. Restore any calls that were active before the last restart.

Everything that touches pyrogram happens inside the event loop: constructing
clients outside it triggers pyrogram's "Future attached to a different loop"
crash on Windows + Python 3.13.
"""
from __future__ import annotations

import asyncio
import sys

from pyrogram import Client
from pyrogram import idle

# Windows consoles default to cp1252; make emoji printing safe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
import database as db
import handlers
import tgemoji as te
import userbot as ub
from config import API_HASH
from config import API_ID
from config import BOT_TOKEN
from config import CONTACT_USERNAME
from config import OWNER_ID
from config import OWNER_USERNAME
from config import PREMIUM_EMOJI
from config import SESSION_NAME
from config import USERBOT_ENABLED
from player import Player
from player import PyTgCalls
from player import ffmpeg_exe


def _make_bot_client() -> Client:
    return Client(
        SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=False,
    )


async def run() -> None:
    # ---- 1. userbot engine (silent login from the saved session) ----------
    user_client = None
    if USERBOT_ENABLED:
        try:
            user_client = await ub.ensure_userbot()
        except ub.UserbotError as exc:
            print(te.console(f"[!] Userbot not connected yet: {exc}"))
            print(te.console("    The owner can connect it with /login in DM."))
            user_client = None
        except Exception as exc:  # noqa: BLE001
            print(te.console(f"[!] Userbot crashed on login: {exc}"))
            user_client = None

    # ---- 2. bot client (the control panel) --------------------------------
    bot_client = _make_bot_client()

    if user_client is not None:
        user_calls = PyTgCalls(user_client)
        await user_calls.start()
        player = Player(tg_calls=user_calls, download_client=user_client)
        print(te.console("Userbot engine ready (your account plays the audio)."))
    else:
        bot_calls = PyTgCalls(bot_client)
        player = Player(tg_calls=bot_calls, download_client=bot_client)
        print(te.console(
            "Bot-only mode: the owner can enable the userbot with /login."
        ))

    handlers.register(bot_client, player)
    await bot_client.start()
    await player.start()

    if user_client is not None:
        ub.mark_connected(user_client, player)

    await player.restore_sessions()

    me = await bot_client.get_me()
    config.BOT_USERNAME = me.username or ""
    mode = "Userbot mode" if user_client is not None else "Bot-only mode"
    print("=" * 60)
    print(te.console(f"@{me.username} is live"))
    print(f"   Owner   : {OWNER_USERNAME} (ID {OWNER_ID})")
    print(f"   Paywall : {CONTACT_USERNAME}")
    print(f"   Engine  : {mode}")
    print(f"   Premium emoji: {'on' if PREMIUM_EMOJI else 'off'}")
    print("=" * 60)

    # Welcome the owner with the full premium guide on every boot.
    try:
        from handlers import welcome_markup  # noqa: PLC0415

        await bot_client.send_message(
            OWNER_ID,
            te.emo(
                "✅ <b>Bot is online!</b>\n\n"
                + (
                    "🎵 <b>Userbot mode active</b> — your account joins voice "
                    "chats and plays audio, exactly like the big music bots.\n\n"
                    if user_client is not None else
                    "⚠️ <b>Userbot not connected yet</b> — bots can't start "
                    "voice chats on their own.\n\n"
                    "💎 Send <code>/login</code> here and follow the steps to "
                    "connect your account (~30 seconds, one time only).\n\n"
                )
            ),
            reply_markup=welcome_markup(),
        )
    except Exception:  # noqa: BLE001 — owner hasn't started the bot yet
        pass

    try:
        await idle()  # runs until Ctrl+C
    finally:
        try:
            await bot_client.stop()
        except Exception:  # noqa: BLE001
            pass
        await ub.disconnect_userbot()


def main() -> None:
    try:
        ffmpeg_exe()
    except FileNotFoundError as exc:
        print(f"[!] {exc}")
        sys.exit(1)
    db.init()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
