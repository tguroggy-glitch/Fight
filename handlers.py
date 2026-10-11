"""Command and callback handlers: access control, panels, uploads, playback.

Access model
------------
- owner      : full control (panel, uploads, admins, subscriptions)
- admin      : playback commands everywhere (added by the owner)
- subscriber : playback commands while the subscription is valid
- stranger   : paywall message (private) / silent ignore (groups)
"""

from __future__ import annotations

import asyncio
import time

from pyrogram import Client
from pyrogram import enums
from pyrogram import filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import CallbackQuery
from pyrogram.types import Message

import animations as anim
import database as db
import panels
import tgemoji as te
import userbot as ub
from config import BOT_USERNAME
from config import CONTACT_USERNAME
from config import DOWNLOAD_DIR
from config import MAX_UPLOAD_MB
from config import OWNER_ID
from config import OWNER_USERNAME
from config import PAYWALL_TEXT
from config import SPEED_MAX
from config import SPEED_MIN
from config import VOLUME_MAX
from config import VOLUME_MIN
from player import Player

MEDIA_DIR = DOWNLOAD_DIR / "media"

pending: dict[int, dict] = {}
player: Player | None = None
pyro: Client | None = None
bot_client: Client | None = None

AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".wma", ".m4b")

ROLE_LABEL = {
    "owner": "Owner 👑",
    "admin": "Admin 🛡",
    "sub": "Subscriber ⏳",
    "stranger": "Stranger",
}


# ------------------------------------------------------------- access ----

def role_of(user_id: int) -> str:
    if user_id == OWNER_ID:
        return "owner"
    if db.is_admin(user_id):
        return "admin"
    if db.is_subscribed(user_id):
        return "sub"
    return "stranger"


async def _allow_playback(m: Message) -> bool:
    """Guard for playback commands. Replies with the paywall in private."""
    user = m.from_user
    if user is None:
        return False
    if role_of(user.id) == "stranger":
        if m.chat.type == enums.ChatType.PRIVATE:
            await m.reply_text(_paywall())
        return False
    return True


# -------------------------------------------------------------- media ----

def _extract_audio(message: Message | None):
    if message is None:
        return None
    if message.audio:
        return message.audio
    if message.voice:
        return message.voice
    if message.document:
        doc = message.document
        mime = (doc.mime_type or "").lower()
        name = (doc.file_name or "").lower()
        if mime.startswith("audio/") or name.endswith(AUDIO_EXTS):
            return doc
    return None


def register_track(message: Message) -> int | None:
    audio = _extract_audio(message)
    if audio is None:
        return None
    title = (
        getattr(audio, "title", None)
        or getattr(audio, "file_name", None)
        or ("Voice note" if message.voice else "Audio")
    )
    if message.caption:
        title = message.caption
    duration = int(getattr(audio, "duration", 0) or 0)
    return db.add_track(title[:120], message.chat.id, message.id, duration)


async def download_track_to(client: Client, chat_id: int, message_id: int, path) -> bool:
    try:
        msg = await client.get_messages(chat_id, message_id)
    except Exception:  # noqa: BLE001
        return False
    if not msg or msg.empty or _extract_audio(msg) is None:
        return False
    try:
        out = await client.download_media(msg, file_name=str(path))
        return bool(out)
    except Exception:  # noqa: BLE001
        return False


async def download_track_anywhere(chat_id: int, message_id: int, path) -> bool:
    """Download with the userbot first, fall back to the bot client.

    The userbot may not be a member of the chat where the audio was posted;
    the bot (promoted admin) usually is — try both before giving up.
    """
    for candidate in (pyro, bot_client):
        if candidate is None:
            continue
        if await download_track_to(candidate, chat_id, message_id, path):
            return True
    return False


# ------------------------------------------------------------- helpers ----

async def _act(m: Message, coro) -> None:
    """Run a player coroutine and reply with its result or the error."""
    try:
        result = await coro
    except Exception as exc:  # noqa: BLE001
        result = te.emo(f"❌ {te.esc(str(exc))}")
    await m.reply_text(te.emo(str(result)))


def _welcome_text(role: str) -> str:
    """Premium welcome message with full usage guide."""
    lines = [
        te.emo("🎧 <b>Voice-Chat Audio Bot</b>"),
        "",
        te.emo(f"👑 <b>Owner:</b> {OWNER_USERNAME}"),
        "",
    ]

    if role == "stranger":
        lines += [
            te.emo("🔒 <b>Private bot — access required</b>"),
            "",
            "This bot plays audio in group & channel voice chats",
            "with loop, speed and equalizer control.",
            "",
            te.emo(f"💰 <b>To get access:</b> DM {CONTACT_USERNAME}"),
            "",
            te.emo("✨ <b>What you get:</b>"),
            "• 🎚 8 equalizer presets",
            "• 🚀 Speed 0.25x–3x",
            "• 🔁 Loop one track or the whole queue",
            "• 🔊 Volume 0–200%",
            "• 📚 Personal audio library",
            "",
            te.emo("🆔 Send /id to get your Telegram ID"),
        ]
        return "\n".join(lines)

    # Authenticated users
    lines += [
        te.emo(f"👋 Welcome back — role: <b>{ROLE_LABEL.get(role, role)}</b>"),
        "",
        te.emo("📖 <b>Quick start</b>"),
        "1. Add the bot to your group/channel as admin",
        "2. Send /join there",
        "3. Reply to any audio with /play",
        "",
        te.emo("🎛 <b>Controls</b>"),
        "/pause · /resume · /skip · /stop",
        "/loop off|one|queue · /speed 0.25–3.0",
        "/volume 0–200 · /seek seconds",
        "/equalizer · /bass · /treble · /vocal",
        "/status · /controls · /queue",
        "",
    ]

    if role == "owner":
        lines += [
            te.emo("👑 <b>Owner</b>"),
            "/panel — upload, library, admins, subscriptions",
            "/stats — bot dashboard (tracks, subs, chats)",
            "/broadcast — message all subscribers + admins",
            "/login — connect your account (userbot engine)",
            "/userbot — engine status",
            "/cancel — cancel any pending input",
            "",
        ]

    ub_active = ub.is_connected()
    lines += [
        te.emo("💎 <b>Engine:</b> " + (
            "Userbot active — your account is playing"
            if ub_active else
            "Bot-only — owner: /login to unlock full power"
        )),
    ]
    return "\n".join(lines)


def _help_text(role: str) -> str:
    return _welcome_text(role)


def welcome_markup():
    """Keyboard for the boot-time welcome DM (used by main.py)."""
    if ub.is_connected():
        return panels.main_menu()
    return panels.userbot_menu(False, ub.login_manager.active)


async def _resolve_user_id(text: str) -> int | None:
    """Parse a numeric ID or an @username from text."""
    text = text.strip()
    if text.startswith("@"):
        try:
            user = await pyro.get_users(text)
            return user.id
        except Exception:  # noqa: BLE001
            return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


# ------------------------------------------------------------ userbot ----

def _userbot_text() -> str:
    """Status report for the userbot engine."""
    if ub.is_connected():
        return (
            "💎 <b>Userbot Engine — Active</b>\n\n"
            "🎵 Your account is connected and plays audio in voice chats.\n"
            "🚀 This is what lets playback work even where a bot alone can't\n"
            "start the call.\n"
            "💾 The session is saved in <code>.env</code> on this PC only.\n\n"
            "▶️ Use /join in a group or channel (or 🌐 Connected Chats in the "
            "panel), then play a track."
        )
    if ub.login_manager.active:
        step = {
            "phone": "1/3 — waiting for your phone number",
            "code": "2/3 — waiting for the login code",
            "password": "3/3 — waiting for your 2FA password",
        }.get(ub.login_manager.stage, "in progress")
        return (
            "💎 <b>Userbot Engine — login in progress</b>\n\n"
            f"📍 Step {step}.\nFollow the instructions above and send the "
            "next step here, or cancel below."
        )
    return (
        "💎 <b>Userbot Engine — Not connected</b>\n\n"
        "Bots alone can't start voice chats (Telegram blocks it) — that's "
        "why every music bot secretly uses a real account as the player.\n\n"
        "Connect your account once with /login — 3 easy steps, all inside "
        "this chat — and it becomes the playback engine. The session is "
        "saved locally, so this is one-time only."
    )


async def _handle_login_input(m: Message, action: str, text: str) -> None:
    """Route DM text through the in-chat login steps."""
    if action == "login_phone":
        tracker = anim.StepTracker()
        pending[m.from_user.id]["tracker"] = tracker
        try:
            phone = await ub.login_manager.begin(text)
        except ub.UserbotError as exc:
            await m.reply_text(
                te.emo(f"❌ {te.esc(str(exc))}\n\nSend the number again, or /cancel.")
            )
            return
        pending[m.from_user.id]["action"] = "login_code"
        await tracker.attach(
            await m.reply_text(
                tracker._frame("Connect your account"),
                reply_markup=panels.cancel_login(),
            ),
            "Connect your account",
        )
        await tracker.complete_step("Connect your account")
        await m.reply_text(
            te.emo(
                f"📲 <b>Code sent to {te.esc(phone)}</b> — check Telegram on "
                "your phone and send the code here (e.g. <code>12345</code>)."
            )
        )
        return

    if action == "login_code":
        tracker = pending[m.from_user.id].get("tracker")
        try:
            result = await ub.login_manager.submit_code(text)
        except ub.UserbotError as exc:
            if tracker:
                await tracker.fail("Connect your account", str(exc))
            await m.reply_text(te.emo("🔄 Send the code again, or /cancel."))
            return
        if tracker:
            await tracker.complete_step("Connect your account")
        if result == "password":
            pending[m.from_user.id]["action"] = "login_password"
            await m.reply_text(
                te.emo(
                    "🔑 <b>Two-factor verification is on.</b>\n"
                    "Send your Telegram cloud password now.\n\n"
                    "🔒 It is used once, in this chat, and never stored."
                ),
                reply_markup=panels.cancel_login(),
            )
            return
        await _finish_login(m, tracker)
        return

    if action == "login_password":
        tracker = pending[m.from_user.id].get("tracker")
        try:
            await ub.login_manager.submit_password(text)
        except ub.UserbotError as exc:
            if tracker:
                await tracker.fail("Connect your account", str(exc))
            await m.reply_text(te.emo("🔄 Send the password again, or /cancel."))
            return
        await _finish_login(m, tracker)
        return


async def _finish_login(m: Message, tracker: "anim.StepTracker | None" = None) -> None:
    """Activate the freshly-logged-in account as the playback engine."""
    global pyro
    pending.pop(m.from_user.id, None)
    if player is None:
        await m.reply_text(te.emo("❌ Engine not ready — restart the bot."))
        return
    if tracker:
        await tracker.render("Connecting the engine…")
    try:
        app, _session, me = await ub.login_manager.finish()
    except ub.UserbotError as exc:
        if tracker:
            await tracker.fail("Connect your account", str(exc))
        await m.reply_text(te.emo(f"❌ {te.esc(str(exc))}"))
        return
    try:
        from pytgcalls import PyTgCalls  # noqa: PLC0415

        calls = PyTgCalls(app)
        await calls.start()
        player.rebind(calls, app)
        ub.mark_connected(app, player)
        pyro = app  # the account downloads media (it sees more chats)
        name = getattr(me, "first_name", "?")
        premium = bool(getattr(me, "is_premium", False))
        if tracker:
            await tracker.finish("Connected!", str(name), premium)
        await m.reply_text(
            te.emo(
                f"💎 <b>Account connected: {te.esc(str(name))}{' ✨' if premium else ''}</b>\n\n"
                "🎵 <b>Userbot engine is now ACTIVE</b> — your account joins "
                "voice chats and plays the audio like a real music bot.\n\n"
                "▶️ Add me to a group/channel, send /join there, and play "
                "something!\n\n"
                "💾 Session saved — every future start of the bot is "
                "instant, no login needed."
            ),
            reply_markup=panels.main_menu(),
        )
    except Exception as exc:  # noqa: BLE001
        ub.mark_disconnected()
        if tracker:
            await tracker.fail("Connect your account", str(exc))
        await m.reply_text(
            te.emo(f"❌ Logged in, but the engine failed to start: {te.esc(str(exc))}")
        )


# ---------------------------------------------------------- commands ----

async def _seek_cmd(m: Message, seconds: float) -> None:
    if not await _allow_playback(m):
        return
    await _act(m, player.seek(m.chat.id, seconds))


def _register_message_handlers(client: Client) -> None:

    @client.on_message(filters.command("start"))
    async def cmd_start(_, m: Message):
        if m.from_user is None:
            return
        role = role_of(m.from_user.id)
        if role == "stranger":
            if m.chat.type == enums.ChatType.PRIVATE:
                await m.reply_text(
                    _welcome_text("stranger")
                )
            return  # silent in groups: don't reveal the bot to strangers
        if m.chat.type != enums.ChatType.PRIVATE:
            await m.reply_text(
                te.emo("👋 <b>Voice-Chat Audio Bot</b> — controls work here!\n\n"),
                reply_markup=panels.pm_me_button(),
            )
            return
        text = _welcome_text(role)
        markup = panels.main_menu() if role == "owner" else None
        if markup is None:
            markup = panels.back_to_panel()  # harmless: panel callback is owner-guarded
        await m.reply_text(text, reply_markup=markup)

    @client.on_message(filters.command("help"))
    async def cmd_help(_, m: Message):
        if m.from_user is None:
            return
        role = role_of(m.from_user.id)
        if role == "stranger":
            if m.chat.type == enums.ChatType.PRIVATE:
                await m.reply_text(_welcome_text("stranger"))
            return
        await m.reply_text(_welcome_text(role))

    @client.on_message(filters.command("panel"))
    async def cmd_panel(_, m: Message):
        if m.chat.type != enums.ChatType.PRIVATE or m.from_user is None:
            return
        if role_of(m.from_user.id) != "owner":
            await m.reply_text(_paywall())
            return
        await m.reply_text(te.emo("🛠 <b>Owner panel</b>"), reply_markup=panels.main_menu())

    @client.on_message(filters.command("login"))
    async def cmd_login(_, m: Message):
        if m.chat.type != enums.ChatType.PRIVATE or m.from_user is None:
            return
        if role_of(m.from_user.id) != "owner":
            await m.reply_text(_paywall())
            return
        if ub.is_connected():
            await m.reply_text(
                te.emo("💎 <b>Userbot is already connected!</b>\n\n"
                       "Send /userbot to manage or disconnect it."),
                reply_markup=panels.userbot_menu(True, False),
            )
            return
        if ub.login_manager.active:
            hint = {
                "phone": "📞 Send your phone number now (e.g. <code>+919876543210</code>).",
                "code": "🔢 Send the login code Telegram sent you.",
                "password": "🔑 Send your Telegram 2FA password.",
            }.get(ub.login_manager.stage, "Continue with the last step.")
            await m.reply_text(
                te.emo(f"⚠️ A login is already in progress.\n\n{hint}"),
                reply_markup=panels.cancel_login(),
            )
            return
        pending[m.from_user.id] = {"action": "login_phone"}
        await m.reply_text(
            te.emo(
                "💎 <b>Connect your Telegram account</b>\n\n"
                "Your account becomes the playback engine — it joins voice "
                "chats and plays the audio, exactly like the big music bots.\n\n"
                "📞 <b>Step 1 of 3:</b> Send your phone number now.\n"
                "(international format, e.g. <code>+919876543210</code>)\n\n"
                "🔒 The code and password stay in this chat; the session is "
                "saved locally on this PC only."
            ),
            reply_markup=panels.cancel_login(),
        )

    @client.on_message(filters.command("userbot"))
    async def cmd_userbot(_, m: Message):
        if m.chat.type != enums.ChatType.PRIVATE or m.from_user is None:
            return
        if role_of(m.from_user.id) != "owner":
            await m.reply_text(_paywall())
            return
        await m.reply_text(
            te.emo(_userbot_text()),
            reply_markup=panels.userbot_menu(ub.is_connected(), ub.login_manager.active),
        )

    @client.on_message(filters.command("stats"))
    async def cmd_stats(_, m: Message):
        if m.chat.type != enums.ChatType.PRIVATE or m.from_user is None:
            return
        if role_of(m.from_user.id) != "owner":
            await m.reply_text(_paywall())
            return
        await m.reply_text(_stats_text(), reply_markup=panels.stats_menu())

    @client.on_message(filters.command("broadcast"))
    async def cmd_broadcast(_, m: Message):
        if m.chat.type != enums.ChatType.PRIVATE or m.from_user is None:
            return
        if role_of(m.from_user.id) != "owner":
            await m.reply_text(_paywall())
            return
        pending[m.from_user.id] = {"action": "broadcast"}
        await m.reply_text(
            te.emo("📣 <b>Broadcast</b>\n\nSend the message text now — it goes to "
                   "all active subscribers + admins.\n/cancel to abort."),
            reply_markup=panels.back_to_panel(),
        )

    @client.on_message(filters.command("cancel"))
    async def cmd_cancel(_, m: Message):
        if m.from_user is None:
            return
        state = pending.pop(m.from_user.id, None)
        tracker = state.get("tracker") if state else None
        if tracker:
            tracker.reset()
        had_pending = state is not None
        if ub.login_manager.active:
            await ub.login_manager.cancel()
            had_pending = True
        await m.reply_text(te.emo("❎ Cancelled." if had_pending else "Nothing to cancel."))

    @client.on_message(filters.command("id"))
    async def cmd_id(_, m: Message):
        # Intentionally open to everyone: buyers need their ID to pay.
        if m.from_user is None:
            return
        text = f"Your ID: <code>{m.from_user.id}</code>"
        if m.chat.type != enums.ChatType.PRIVATE:
            text += f"\nThis chat's ID: <code>{m.chat.id}</code>"
        await m.reply_text(text)

    @client.on_message(filters.command("join"))
    async def cmd_join(_, m: Message):
        if not await _allow_playback(m):
            return
        if m.chat.type == enums.ChatType.PRIVATE:
            if role_of(m.from_user.id) != "owner":
                return
            await m.reply_text(
                te.emo(
                    "🌐 Pick a chat:"
                ), reply_markup=panels.chats_menu()
            )
            return
        db.upsert_chat(m.chat.id, m.chat.title or "")
        await _act(m, player.join(m.chat.id))

    @client.on_message(filters.command("play"))
    async def cmd_play(_, m: Message):
        if not await _allow_playback(m):
            return
        if m.chat.type == enums.ChatType.PRIVATE:
            await m.reply_text(
                "Use 📚 Library in the panel to start playback, or add me to a "
                "group/channel as admin and reply to an audio file with /play."
            )
            return
        replied = m.reply_to_message
        audio = _extract_audio(replied)
        if audio is None:
            await m.reply_text(
                "Reply to an audio/voice file with /play — or send the file to "
                "me in DM to add it to the library."
            )
            return
        db.upsert_chat(m.chat.id, m.chat.title or "")
        track_id = register_track(replied)
        if track_id is None:
            await m.reply_text("❌ Couldn't read that file.")
            return
        track = db.get_track(track_id)
        status = await m.reply_text(te.emo("⏳ Downloading…"))
        path = MEDIA_DIR / f"track_{track_id}"
        ok = await download_track_to(client, m.chat.id, replied.id, path)
        if not ok:
            await status.edit_text(
                te.emo("⚠️ Couldn't download now — will retry automatically when played.")
            )
        try:
            await player.play(m.chat.id, track_id)
        except Exception as exc:  # noqa: BLE001
            await status.edit_text(f"❌ Playback failed: {exc}")
            return
        await status.edit_text(
            te.emo(f"▶️ <b>{te.esc(track['title'])}</b>"),
            reply_markup=panels.track_controls(m.chat.id),
        )

    @client.on_message(filters.command("pause"))
    async def cmd_pause(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.pause(m.chat.id))

    @client.on_message(filters.command("resume"))
    async def cmd_resume(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.resume(m.chat.id))

    @client.on_message(filters.command("skip"))
    async def cmd_skip(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.skip_now(m.chat.id))

    @client.on_message(filters.command("stop"))
    async def cmd_stop(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.stop(m.chat.id))

    @client.on_message(filters.command("leave"))
    async def cmd_leave(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.leave(m.chat.id))

    @client.on_message(filters.command("status"))
    async def cmd_status(_, m: Message):
        if not await _allow_playback(m):
            return
        try:
            text = await player.status_text(m.chat.id)
        except Exception as exc:  # noqa: BLE001
            text = f"❌ {exc}"
        await m.reply_text(text, reply_markup=panels.track_controls(m.chat.id))

    @client.on_message(filters.command("controls"))
    async def cmd_controls(_, m: Message):
        if not await _allow_playback(m):
            return
        await m.reply_text("🎛 Controls:", reply_markup=panels.track_controls(m.chat.id))

    @client.on_message(filters.command("queue"))
    async def cmd_queue(_, m: Message):
        if not await _allow_playback(m):
            return
        state = db.get_call_state(m.chat.id)
        lines: list[str] = []
        current = state["current_id"]
        if current is not None:
            track = db.get_track(current)
            lines.append(f"▶️ {track['title'] if track else current}")
        for tid in state["queue"]:
            if tid == current:
                continue
            track = db.get_track(tid)
            lines.append(f"• {track['title'] if track else tid}")
        body = "\n".join(lines) if lines else "(empty)"
        await m.reply_text(te.emo("📜 Queue:\n") + te.esc_emo(body))

    @client.on_message(filters.command("loop"))
    async def cmd_loop(_, m: Message):
        if not await _allow_playback(m):
            return
        parts = (m.text or "").split()
        mode = parts[1].lower() if len(parts) > 1 else ""
        if mode not in ("off", "one", "queue"):
            setting = db.get_chat_setting(m.chat.id)
            current = setting["loop_mode"] if setting else "off"
            mode = {"off": "one", "one": "queue", "queue": "off"}[current]
        await _act(m, player.set_loop(m.chat.id, mode))

    @client.on_message(filters.command("speed"))
    async def cmd_speed(_, m: Message):
        if not await _allow_playback(m):
            return
        parts = (m.text or "").split()
        if len(parts) > 1:
            try:
                speed = float(parts[1])
            except ValueError:
                speed = 0.0
            if SPEED_MIN <= speed <= SPEED_MAX:
                await _act(m, player.set_speed(m.chat.id, speed))
                return
        await m.reply_text(
            te.emo("🚀 Pick a speed (applies to this chat):"),
            reply_markup=panels.speed_menu(m.chat.id),
        )

    @client.on_message(filters.command("seek"))
    async def cmd_seek(_, m: Message):
        if not await _allow_playback(m):
            return
        parts = (m.text or "").split()
        if len(parts) > 1 and parts[1].lstrip("-").replace(".", "").isdigit():
            await _act(m, player.seek(m.chat.id, float(parts[1])))
            return
        await m.reply_text("Usage: /seek 90 (seconds from start) or /seek -30 (relative)")

    @client.on_message(filters.command("volume"))
    async def cmd_volume(_, m: Message):
        if not await _allow_playback(m):
            return
        parts = (m.text or "").split()
        if len(parts) > 1 and parts[1].isdigit():
            volume = min(VOLUME_MAX, max(VOLUME_MIN, int(parts[1])))
            await _act(m, player.set_volume(m.chat.id, volume))
            return
        await m.reply_text(
            te.emo("🔊 Volume Control"),
            reply_markup=panels.volume_menu(m.chat.id),
        )

    @client.on_message(filters.command("equalizer"))
    async def cmd_equalizer(_, m: Message):
        if not await _allow_playback(m):
            return
        await m.reply_text(
            te.emo("🎚 Equalizer Presets"),
            reply_markup=panels.equalizer_menu(m.chat.id),
        )

    @client.on_message(filters.command("bass"))
    async def cmd_bass(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.set_equalizer(m.chat.id, "bass"))

    @client.on_message(filters.command("treble"))
    async def cmd_treble(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.set_equalizer(m.chat.id, "treble"))

    @client.on_message(filters.command("vocal"))
    async def cmd_vocal(_, m: Message):
        if not await _allow_playback(m):
            return
        await _act(m, player.set_equalizer(m.chat.id, "vocal"))


# ------------------------------------------------------ text / media ----

def _register_input_handlers(client: Client) -> None:

    @client.on_message(filters.private & filters.text)
    async def on_private_text(_, m: Message):
        if m.from_user is None:
            return
        if (m.text or "").startswith("/"):
            return  # commands already handled
        role = role_of(m.from_user.id)
        state = pending.get(m.from_user.id)
        if state is None:
            if role == "stranger":
                await m.reply_text(_welcome_text("stranger"))
            else:
                await m.reply_text(te.emo("Use /panel (owner) or /help."))
            return
        action = state.get("action")

        if action in ("login_phone", "login_code", "login_password"):
            await _handle_login_input(m, action, m.text or "")
            return

        if action == "upload":
            await m.reply_text(te.emo("📤 Send an audio file now, or /cancel."))
            return

        if action == "addadmin":
            uid = await _resolve_user_id(m.text or "")
            if uid is None:
                await m.reply_text(te.emo("Send a numeric ID or @username, or /cancel."))
                return
            name = ""
            if m.reply_to_message and m.reply_to_message.from_user:
                name = m.reply_to_message.from_user.full_name
            db.add_admin(uid, name)
            pending.pop(m.from_user.id, None)
            await m.reply_text(
                te.emo(f"✅ Added admin <code>{uid}</code>"), reply_markup=panels.admins_menu()
            )
            return

        if action == "grant":
            parts = (m.text or "").split()
            uid = await _resolve_user_id(parts[0]) if parts else None
            if uid is None:
                await m.reply_text(te.emo("Format: <code>user_id days</code> (e.g. 123456789 30)"))
                return
            days = 30
            if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0:
                days = int(parts[1])
            expires = db.grant_sub(uid, days)
            pending.pop(m.from_user.id, None)
            left = max(0, int((expires - time.time()) // 86400))
            try:
                await pyro.send_message(
                    uid,
                    te.emo(f"✅ Your subscription is active for {days} more day(s) "
                           f"(~{left}d left). Add me to your group/channel as admin "
                           f"and send /join there."),
                )
            except Exception:  # noqa: BLE001
                pass  # user hasn't started the bot yet
            await m.reply_text(
                f"✅ Granted <code>{uid}</code> +{days}d "
                f"(until {time.strftime('%Y-%m-%d', time.localtime(expires))})",
                reply_markup=panels.subs_menu(),
            )
            return

        if action == "broadcast":
            text = (m.text or "").strip()
            if not text:
                await m.reply_text(te.emo("Send the message text, or /cancel."))
                return
            pending.pop(m.from_user.id, None)
            status = await m.reply_text(te.emo("📣 Broadcasting…"))
            targets: list[int] = []
            for s in db.list_subs():
                targets.append(s["user_id"])
            for a in db.list_admins():
                if a["user_id"] not in targets:
                    targets.append(a["user_id"])
            sent, failed = 0, 0
            for uid in targets:
                try:
                    await pyro.send_message(uid, te.emo(f"📣 <b>Announcement</b>\n\n{te.esc(text)}"))
                    sent += 1
                except Exception:  # noqa: BLE001
                    failed += 1
                await asyncio.sleep(0.05)  # avoid flood limits
            await status.edit_text(
                te.emo(f"📣 Broadcast done: <b>{sent}</b> sent, <b>{failed}</b> failed."),
                reply_markup=panels.main_menu(),
            )
            return

        if action == "rename":
            title = (m.text or "").strip()[:120]
            if not title:
                await m.reply_text("Send a non-empty title, or /cancel.")
                return
            db.rename_track(state["track_id"], title)
            pending.pop(m.from_user.id, None)
            await m.reply_text(
                f"✏️ Renamed to <b>{title}</b>",
                reply_markup=panels.track_view(state["track_id"]),
            )
            return

        pending.pop(m.from_user.id, None)
        await m.reply_text("🤷 Unknown input state cleared.")

    @client.on_message(filters.private & (filters.audio | filters.voice | filters.document))
    async def on_private_media(_, m: Message):
        if m.from_user is None:
            return
        role = role_of(m.from_user.id)
        if role != "owner":
            if role == "stranger":
                await m.reply_text(_welcome_text("stranger"))
            else:
                await m.reply_text(te.emo("Only the owner can upload to the library. "
                    "Reply to an audio in your group with /play instead."))
            return
        state = pending.get(m.from_user.id)
        if not state or state.get("action") != "upload":
            await m.reply_text(te.emo("Use ⬆️ Upload audio in the panel first (so I know where to "
                "put it), or /cancel."))
            return
        audio = _extract_audio(m)
        if audio is None:
            await m.reply_text(te.emo("❌ That doesn't look like an audio file."))
            return
        size_mb = (getattr(audio, "file_size", 0) or 0) / 1_000_000
        if size_mb > MAX_UPLOAD_MB:
            await m.reply_text(
                te.emo(f"❌ File is {size_mb:.1f} MB — the limit is {MAX_UPLOAD_MB} MB.")
            )
            return
        title = m.caption or getattr(audio, "title", None) \
            or getattr(audio, "file_name", None) or "Audio"
        duration = int(getattr(audio, "duration", 0) or 0)
        track_id = db.add_track(title[:120], m.chat.id, m.id, duration)
        pending.pop(m.from_user.id, None)
        status = await m.reply_text(te.emo("⏳ Downloading to library…"))
        path = MEDIA_DIR / f"track_{track_id}"
        ok = await download_track_anywhere(m.chat.id, m.id, path)
        track = db.get_track(track_id)
        if ok:
            await status.edit_text(
                te.emo(f"✅ Saved to library: <b>{te.esc(track['title'])}</b>"),
                reply_markup=panels.track_view(track_id),
            )
        else:
            await status.edit_text(
                te.emo(
                    f"⚠️ Saved <b>{te.esc(track['title'])}</b>, but the download "
                    "failed — I'll retry automatically when it's played."
                ),
                reply_markup=panels.track_view(track_id),
            )


# ---------------------------------------------------------- callbacks ----

def _paywall() -> str:
    """Paywall text with premium-emoji icons (plain text in callback alerts)."""
    return te.emo(PAYWALL_TEXT)


def _count_expired_subs() -> int:
    now = time.time()
    return sum(1 for s in db.list_subs(include_expired=True) if s["expires_at"] <= now)


def _purge_expired_subs() -> int:
    now = time.time()
    removed = 0
    for s in db.list_subs(include_expired=True):
        if s["expires_at"] <= now and db.revoke_sub(s["user_id"]):
            removed += 1
    return removed


def _stats_text() -> str:
    """Owner stats dashboard (message text — premium emoji allowed)."""
    import userbot as ub  # late import

    n_tracks = db.count_tracks()
    n_admins = len(db.list_admins())
    subs = db.list_subs(include_expired=True)
    now = time.time()
    active = sum(1 for s in subs if s["expires_at"] > now)
    chats = db.list_chats()
    joined = sum(1 for c in chats if c["joined"])
    total_dur = 0
    try:
        for t in db.list_tracks():
            total_dur += int(t["duration"] or 0)
    except Exception:  # noqa: BLE001
        pass
    hours, rem = divmod(total_dur, 3600)
    minutes, _ = divmod(rem, 60)
    engine = "Userbot active" if ub.is_connected() else "Bot-only"
    return te.emo(
        "📊 <b>Bot Stats</b>\n\n"
        f"🎵 Tracks: <b>{n_tracks}</b> (~{hours}h {minutes}m)\n"
        f"👥 Admins: <b>{n_admins}</b>\n"
        f"⏳ Subscriptions: <b>{active}</b> active / <b>{len(subs) - active}</b> expired\n"
        f"🌐 Chats: <b>{len(chats)}</b> connected / <b>{joined}</b> in call\n"
        f"💎 Engine: <b>{engine}</b>"
    )


async def _show_status(cq: CallbackQuery, chat_id: int) -> None:
    try:
        text = await player.status_text(chat_id)
    except Exception as exc:  # noqa: BLE001
        text = te.emo(f"❌ {te.esc(str(exc))}")
    await cq.message.edit_text(text, reply_markup=panels.track_controls(chat_id))


def _register_callback_handlers(client: Client) -> None:

    @client.on_callback_query()
    async def on_callback(_, cq: CallbackQuery):
        user = cq.from_user
        data = cq.data or ""
        if user is None:
            return await cq.answer()
        role = role_of(user.id)
        if role == "stranger":
            return await cq.answer(te.plain(PAYWALL_TEXT), show_alert=True)
        try:
            await _route_callback(cq, data, role)
        except MessageNotModified:
            await cq.answer()
        except Exception as exc:  # noqa: BLE001
            try:
                await cq.answer(f"❌ {exc}", show_alert=True)
            except Exception:  # noqa: BLE001
                pass


async def _route_callback(cq: CallbackQuery, data: str, role: str) -> None:
    parts = data.split(":")
    head = parts[0]
    user = cq.from_user

    if data == "noop":
        return await cq.answer()

    owner_heads = ("panel", "lib", "trk", "up", "adm", "sub", "chats", "chat",
                   "help", "ub", "stats", "cast", "clean")
    if head in owner_heads and role != "owner":
        return await cq.answer("Owner only.", show_alert=True)

    # ---- owner panel -----------------------------------------------------
    if head == "panel":
        await cq.message.edit_text(te.emo("🛠 <b>Owner panel</b>"), reply_markup=panels.main_menu())
        return await cq.answer()

    if head == "help":
        await cq.message.edit_text(_welcome_text("owner"), reply_markup=panels.back_to_panel())
        return await cq.answer()

    if head == "up":
        pending[user.id] = {"action": "upload"}
        await cq.message.edit_text(
            "📤 <b>Upload audio</b>\n\nSend me an audio file, voice note, or audio "
            "document now. The caption (if any) becomes the track title.\n/cancel to abort.",
            reply_markup=panels.back_to_panel(),
        )
        return await cq.answer()

    if head == "lib":
        page = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
        await cq.message.edit_text(te.emo("📚 <b>Library</b>"), reply_markup=panels.library_page(page))
        return await cq.answer()

    if head == "trk":
        return await _cb_track(cq, parts)

    if head == "adm":
        if parts[1] == "list":
            await cq.message.edit_text(te.emo("👥 <b>Admins</b>"), reply_markup=panels.admins_menu())
            return await cq.answer()
        if parts[1] == "add":
            pending[user.id] = {"action": "addadmin"}
            await cq.message.edit_text(
                "👥 <b>Add admin</b>\n\nSend their numeric ID (get it with /id), an "
                "@username, or forward a message from them.\n/cancel to abort.",
                reply_markup=panels.back_to_panel(),
            )
            return await cq.answer()
        if parts[1] == "confirmrm":
            uid = int(parts[2])
            await cq.message.edit_text(
                f"Remove admin <code>{uid}</code>?",
                reply_markup=panels.admin_confirm_remove(uid),
            )
            return await cq.answer()
        if parts[1] == "rm":
            uid = int(parts[2])
            db.remove_admin(uid)
            await cq.message.edit_text(te.emo("👥 <b>Admins</b>"), reply_markup=panels.admins_menu())
            return await cq.answer(f"Removed {uid}.")

    if head == "sub":
        if parts[1] == "list":
            await cq.message.edit_text(
                te.emo("⏳ <b>Subscriptions</b>"), reply_markup=panels.subs_menu()
            )
            return await cq.answer()
        if parts[1] == "add":
            pending[user.id] = {"action": "grant"}
            await cq.message.edit_text(
                "⏳ <b>Grant subscription</b>\n\nSend: <code>user_id days</code>\n"
                "Example: <code>123456789 30</code>\n\nIf the user already has time "
                "left, the days are added on top.\n/cancel to abort.",
                reply_markup=panels.back_to_panel(),
            )
            return await cq.answer()
        if parts[1] == "view":
            uid = int(parts[2])
            sub = db.get_sub(uid)
            if sub is None:
                text = f"⛔ <code>{uid}</code> has no active subscription."
            else:
                left = sub["expires_at"] - time.time()
                text = (
                    f"⏳ <code>{uid}</code>\n"
                    f"Expires: {time.strftime('%Y-%m-%d %H:%M', time.localtime(sub['expires_at']))}\n"
                    f"Remaining: {left / 86400:.1f} days"
                )
            await cq.message.edit_text(text, reply_markup=panels.sub_view(uid))
            return await cq.answer()
        if parts[1] == "extend":
            uid, days = int(parts[2]), int(parts[3])
            db.grant_sub(uid, days)
            sub = db.get_sub(uid)
            try:
                await pyro.send_message(uid, f"✅ Your subscription was extended by {days} day(s)!")
            except Exception:  # noqa: BLE001
                pass
            await cq.message.edit_text(
                f"✅ Extended <code>{uid}</code> "
                f"(now until {time.strftime('%Y-%m-%d', time.localtime(sub['expires_at']))})",
                reply_markup=panels.sub_view(uid),
            )
            return await cq.answer()
        if parts[1] == "revoke":
            uid = int(parts[2])
            db.revoke_sub(uid)
            await cq.message.edit_text(
                te.emo("⏳ <b>Subscriptions</b>"), reply_markup=panels.subs_menu()
            )
            return await cq.answer(f"Revoked {uid}.")

    if head == "stats":
        await cq.message.edit_text(_stats_text(), reply_markup=panels.stats_menu())
        return await cq.answer()

    if head == "cast":
        pending[user.id] = {"action": "broadcast"}
        await cq.message.edit_text(
            te.emo("📣 <b>Broadcast</b>\n\nSend the message text now — it goes to "
                   "all active subscribers + admins.\n/cancel to abort."),
            reply_markup=panels.back_to_panel(),
        )
        return await cq.answer()

    if head == "clean":
        if parts[1] in ("subs", "do"):
            removed = _purge_expired_subs()
            await cq.message.edit_text(
                te.emo(f"🧹 Removed <b>{removed}</b> expired subscription(s)."),
                reply_markup=panels.stats_menu() if parts[1] == "do" else panels.subs_menu(),
            )
            return await cq.answer(f"Removed {removed}.")
        expired = _count_expired_subs()
        await cq.message.edit_text(
            te.emo(f"🧹 <b>Cleanup</b>\n\nExpired subscriptions: <b>{expired}</b>\n\n"
                   "This removes expired subscriptions from the list. "
                   "Tracks, admins and chat settings are kept."),
            reply_markup=panels.cleanup_confirm_menu(),
        )
        return await cq.answer()

    if head == "ub":
        return await _cb_userbot(cq, parts)

    if head == "chats":
        await cq.message.edit_text(te.emo("🌐 <b>Connected chats</b>"), reply_markup=panels.chats_menu())
        return await cq.answer()

    if head == "chat":
        chat_id = int(parts[2])
        try:
            if parts[1] == "join":
                result = await player.join(chat_id)
            else:
                result = await player.leave(chat_id)
        except Exception as exc:  # noqa: BLE001
            return await cq.answer(f"❌ {exc}", show_alert=True)
        await cq.message.edit_text(te.emo("🌐 <b>Connected chats</b>"), reply_markup=panels.chats_menu())
        return await cq.answer(result)

    # ---- playback controls (admins + subs too) ---------------------------
    if head == "ctl":
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "close":
            await cq.message.delete()
            return await cq.answer("Closed")
        chat_id = int(parts[2])
        if sub == "toggle":
            state = db.get_call_state(chat_id)
            try:
                if state["paused"]:
                    await player.resume(chat_id)
                else:
                    await player.pause(chat_id)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer()
        if sub == "skip":
            try:
                result = await player.skip_now(chat_id)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer(result)
        if sub == "stop":
            try:
                result = await player.stop(chat_id)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await cq.message.edit_text(te.emo(f"⏹ {te.esc(str(result))}"))
            return await cq.answer()
        if sub == "status":
            await _show_status(cq, chat_id)
            return await cq.answer()
        if sub == "cycleloop":
            setting = db.get_chat_setting(chat_id)
            current = setting["loop_mode"] if setting else "off"
            nxt = {"off": "one", "one": "queue", "queue": "off"}[current]
            await player.set_loop(chat_id, nxt)
            await _show_status(cq, chat_id)
            return await cq.answer(f"Loop: {nxt}")
        if sub == "speed":
            await cq.message.edit_text(
                te.emo("🚀 <b>Playback speed</b>"), reply_markup=panels.speed_menu(chat_id)
            )
            return await cq.answer()
        if sub == "setspeed":
            speed = float(parts[3])
            try:
                result = await player.set_speed(chat_id, speed)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer(result)
        if sub in ("volup", "voldown"):
            setting = db.get_chat_setting(chat_id)
            volume = setting["volume"] if setting else 100
            volume += 10 if sub == "volup" else -10
            volume = min(VOLUME_MAX, max(VOLUME_MIN, volume))
            try:
                result = await player.set_volume(chat_id, volume)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer(result)
        if sub == "seek":
            offset = float(parts[3])
            try:
                state = db.get_call_state(chat_id)
                if state["current_id"] is None:
                    return await cq.answer("Nothing playing", show_alert=True)
                track = db.get_track(state["current_id"])
                duration = track["duration"] if track else 0
                # Live position from the call clock — the DB copy is stale.
                live = await player._elapsed(chat_id)
                position = live if live is not None else state["position"]
                if duration and position + offset > duration:
                    offset = duration - position - 1
                if position + offset < 0:
                    offset = -position
                result = await player.seek(chat_id, position + offset)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer(result)
        if sub == "setvol":
            volume = int(parts[3])
            volume = min(VOLUME_MAX, max(VOLUME_MIN, volume))
            try:
                result = await player.set_volume(chat_id, volume)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer(result)
        if sub == "eq":
            preset = parts[3]
            try:
                result = await player.set_equalizer(chat_id, preset)
            except Exception as exc:  # noqa: BLE001
                return await cq.answer(f"❌ {exc}", show_alert=True)
            await _show_status(cq, chat_id)
            return await cq.answer(result)

    await cq.answer()


async def _cb_userbot(cq: CallbackQuery, parts: list[str]) -> None:
    """Userbot panel buttons: status, connect, cancel, disconnect."""
    sub = parts[1] if len(parts) > 1 else "status"

    if sub == "status":
        await cq.message.edit_text(
            te.emo(_userbot_text()),
            reply_markup=panels.userbot_menu(ub.is_connected(), ub.login_manager.active),
        )
        return await cq.answer()

    if sub == "begin":
        if ub.is_connected():
            return await cq.answer("Already connected.", show_alert=True)
        pending[cq.from_user.id] = {"action": "login_phone"}
        await cq.message.edit_text(
            te.emo(
                "💎 <b>Connect your Telegram account</b>\n\n"
                "Your account becomes the playback engine — it joins voice "
                "chats and plays the audio, exactly like the big music bots.\n\n"
                "📞 <b>Step 1 of 3:</b> Send your phone number now.\n"
                "(international format, e.g. <code>+919876543210</code>)\n\n"
                "🔒 The code and password stay in this chat; the session is "
                "saved locally on this PC only."
            ),
            reply_markup=panels.cancel_login(),
        )
        return await cq.answer()

    if sub == "cancel":
        if ub.login_manager.active:
            await ub.login_manager.cancel()
        pending.pop(cq.from_user.id, None)
        await cq.message.edit_text(
            te.emo(_userbot_text()),
            reply_markup=panels.userbot_menu(ub.is_connected(), False),
        )
        return await cq.answer("Login cancelled.")

    if sub == "logout":
        if not ub.is_connected():
            return await cq.answer("Not connected.", show_alert=True)
        await ub.disconnect_userbot()
        ub.remove_session_from_env()
        await cq.message.edit_text(
            te.emo(
                "🚪 <b>Account disconnected.</b>\n\n"
                "Restart the bot for bot-only mode, or send /login to "
                "connect an account again."
            ),
            reply_markup=panels.userbot_menu(False, False),
        )
        return await cq.answer("Disconnected.")


async def _cb_track(cq: CallbackQuery, parts: list[str]) -> None:
    action = parts[1]
    user = cq.from_user

    if action == "view":
        track_id = int(parts[2])
        track = db.get_track(track_id)
        if track is None:
            return await cq.answer("Track no longer exists.", show_alert=True)
        minutes, seconds = divmod(track["duration"] or 0, 60)
        await cq.message.edit_text(
            te.emo(
                f"🎵 <b>{te.esc(track['title'])}</b>\n"
                f"⏱ {minutes}:{seconds:02d} • id {track_id}\n"
                f"📥 stored in chat <code>{track['chat_id']}</code>"
            ),
            reply_markup=panels.track_view(track_id),
        )
        return await cq.answer()

    if action == "del":
        track_id = int(parts[2])
        db.delete_track(track_id)
        await cq.message.edit_text("🗑 Deleted.", reply_markup=panels.library_page(0))
        return await cq.answer("Deleted.")

    if action == "rename":
        track_id = int(parts[2])
        pending[user.id] = {"action": "rename", "track_id": track_id}
        await cq.message.edit_text(
            "✏️ Send the new title now. /cancel to abort.",
            reply_markup=panels.back_to_panel(),
        )
        return await cq.answer()

    if action == "playpick":
        track_id = int(parts[2])
        await cq.message.edit_text(
            te.emo("▶️ <b>Play in…</b>"), reply_markup=panels.chat_pick(track_id, "play", 0)
        )
        return await cq.answer()

    if action == "qpick":
        track_id = int(parts[2])
        await cq.message.edit_text(
            te.emo("➕ <b>Queue in…</b>"), reply_markup=panels.chat_pick(track_id, "queue", 0)
        )
        return await cq.answer()

    if action == "cpick":
        track_id, pick_action, page = int(parts[2]), parts[3], int(parts[4])
        await cq.message.edit_text(
            te.emo("🌐 <b>Pick a chat</b>"),
            reply_markup=panels.chat_pick(track_id, pick_action, page),
        )
        return await cq.answer()

    if action in ("play", "queue"):
        track_id, chat_id = int(parts[2]), int(parts[3])
        track = db.get_track(track_id)
        if track is None:
            return await cq.answer("Track no longer exists.", show_alert=True)
        await cq.answer("Starting…")
        try:
            chat = await pyro.get_chat(chat_id)
            db.upsert_chat(chat_id, getattr(chat, "title", "") or "")
        except Exception:  # noqa: BLE001
            db.upsert_chat(chat_id)
        try:
            if action == "play":
                await player.play(chat_id, track_id)
            else:
                await player.queue_add(chat_id, track_id)
        except Exception as exc:  # noqa: BLE001
            return await cq.answer(f"❌ {exc}", show_alert=True)
        await cq.message.edit_text(
            te.emo(f"▶️ <b>{te.esc(track['title'])}</b>"),
            reply_markup=panels.track_controls(chat_id),
        )
        return


def register(client: Client, player_instance: Player) -> None:
    global player, pyro, bot_client
    player = player_instance
    bot_client = client
    if pyro is None:
        pyro = client  # bot-only mode: bot downloads for itself
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    _register_message_handlers(client)
    _register_input_handlers(client)
    _register_callback_handlers(client)
