"""Userbot engine: your personal account joins voice chats and plays audio.

Why a userbot?
--------------
Telegram does not let *bots* start voice chats
(`phone.CreateGroupCall -> BOT_METHOD_INVALID`) and strips custom-emoji
entities from bot messages. Every music bot works around this the same
way: a hidden **user account** joins the voice chat and streams the audio
while the bot account stays the control panel.

Login — 100% inside the bot DM, no terminal prompts
---------------------------------------------------
The owner sends /login in the bot's DM and then just follows the buttons:

    /login  ->  send phone number  ->  send the Telegram code
            ->  (2FA password if enabled)  ->  connected ✔

The session string is saved to .env automatically, so every future start
of the bot logs the account in silently — the flow above only ever runs
once.
"""
from __future__ import annotations

import re

from pyrogram import Client
from pyrogram.errors import ApiIdInvalid
from pyrogram.errors import AuthKeyUnregistered
from pyrogram.errors import FloodWait
from pyrogram.errors import PhoneCodeExpired
from pyrogram.errors import PhoneCodeInvalid
from pyrogram.errors import PhoneNumberBanned
from pyrogram.errors import PhoneNumberFlood
from pyrogram.errors import PhoneNumberInvalid
from pyrogram.errors import PasswordHashInvalid
from pyrogram.errors import SessionPasswordNeeded
from pyrogram.errors import UserDeactivated

# Where Telegram says the login code was delivered (SentCodeType).
_CODE_WHERE = {
    "APP": (
        "inside your Telegram app — look for a service message from the "
        "official “Telegram” account containing the login code"
    ),
    "SMS": "by SMS to this phone number",
    "CALL": "with a phone call — the code will be read out loud",
    "FLASH_CALL": "as a flash call (a brief ring)",
    "MISSCALL": "via a missed call — the last digits of the caller's number are the code",
}

import config
from config import API_HASH
from config import API_ID
from config import PROJECT_ROOT
from config import USERBOT_SESSION
from tgemoji import console as _console  # terminal output: clean ASCII

USER_SESSION_NAME = "vcbot_user"


class UserbotError(RuntimeError):
    """Friendly failure: the message is safe to show the owner."""


# --------------------------------------------------------------------------
# .env session persistence
# --------------------------------------------------------------------------

def save_session_to_env(session_string: str) -> None:
    """Persist the session string into .env (replacing any old value)."""
    env_path = PROJECT_ROOT / ".env"
    lines: list[str] = []
    replaced = False
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("USERBOT_SESSION="):
                lines.append(f"USERBOT_SESSION={session_string}")
                replaced = True
            else:
                lines.append(raw)
    if not replaced:
        lines += ["", "# ==== Userbot session (saved automatically) ====",
                  f"USERBOT_SESSION={session_string}"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config.USERBOT_SESSION = session_string
    config.USE_USERBOT = True


def remove_session_from_env() -> None:
    """Clear USERBOT_SESSION from .env (used by logout)."""
    env_path = PROJECT_ROOT / ".env"
    config.USERBOT_SESSION = ""
    config.USE_USERBOT = False
    if not env_path.exists():
        return
    kept = [
        raw for raw in env_path.read_text(encoding="utf-8").splitlines()
        if not raw.strip().startswith("USERBOT_SESSION=")
    ]
    env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Interactive (in-chat) login manager
# --------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    phone = re.sub(r"[ \-().]", "", (raw or "").strip())
    if not phone:
        raise UserbotError("That doesn't look like a phone number.")
    if not phone.startswith("+"):
        phone = f"+{phone}"
    if not re.fullmatch(r"\+\d{6,15}", phone):
        raise UserbotError(
            "Invalid phone format. Send it in international form, e.g. "
            "<code>+919876543210</code>."
        )
    return phone


class LoginManager:
    """Drives Telegram's step-by-step login entirely from chat messages."""

    def __init__(self) -> None:
        self.client: Client | None = None
        self.phone: str = ""
        self.phone_code_hash: str = ""
        self.stage: str = ""          # phone -> code -> password -> done
        self.needs_password: bool = False

    @property
    def active(self) -> bool:
        return self.client is not None

    async def begin(self, phone_raw: str) -> tuple[str, str]:
        """Step 1: connect + send the login code to the given phone.

        Returns ``(phone, where)`` where *where* is a human-readable
        description of where Telegram is delivering the code (app / SMS /
        call). Telegram decides the channel — with APP delivery the code
        shows up as a service message inside the Telegram app, NOT by SMS.
        """
        if self.active:
            await self.cancel()
        phone = _normalize_phone(phone_raw)
        app = Client(
            USER_SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
        )
        await app.connect()
        try:
            sent = await app.send_code(phone)
        except PhoneNumberInvalid as exc:
            await app.disconnect()
            raise UserbotError(
                "Telegram rejected that number. Use international format, "
                "e.g. +919876543210."
            ) from exc
        except ApiIdInvalid as exc:
            await app.disconnect()
            raise UserbotError(
                "API_ID/API_HASH rejected by Telegram. Get a fresh pair at "
                "my.telegram.org."
            ) from exc
        except PhoneNumberFlood as exc:
            await app.disconnect()
            raise UserbotError(
                "This phone number is temporarily blocked from requesting "
                "login codes (too many attempts). Wait a few hours and "
                "try again."
            ) from exc
        except PhoneNumberBanned as exc:
            await app.disconnect()
            raise UserbotError("This phone number is banned by Telegram.") from exc
        except FloodWait as exc:
            await app.disconnect()
            raise UserbotError(
                f"Telegram is rate-limiting login requests right now. "
                f"Please wait {int(getattr(exc, 'value', 60))} seconds and "
                f"send /login again."
            ) from exc
        except Exception as exc:
            await app.disconnect()
            raise UserbotError(f"Could not send the login code: {exc}") from exc

        self.client = app
        self.phone = phone
        self.phone_code_hash = sent.phone_code_hash
        self.stage = "code"
        where = _CODE_WHERE.get(
            getattr(getattr(sent, "type", None), "name", ""),
            "by Telegram (app or SMS — check both)",
        )
        return phone, where

    async def submit_code(self, code: str) -> str:
        """Step 2: verify the code. Returns 'password' or 'done'."""
        if not self.active or self.stage != "code":
            raise UserbotError("No login in progress — send /login first.")
        clean = re.sub(r"\D", "", code)
        if not clean:
            raise UserbotError("Send the numeric code Telegram gave you.")
        try:
            signed = await self.client.sign_in(
                self.phone, self.phone_code_hash, clean
            )
        except SessionPasswordNeeded:
            self.stage = "password"
            self.needs_password = True
            return "password"
        except PhoneCodeInvalid as exc:
            raise UserbotError("Wrong code — check the Telegram app and send it again.") from exc
        except PhoneCodeExpired as exc:
            await self.cancel()
            raise UserbotError("That code expired — tap Resend or send /login to restart.") from exc
        except FloodWait as exc:
            raise UserbotError(
                f"Too many attempts — wait {int(getattr(exc, 'value', 60))} seconds "
                f"and send the code again."
            ) from exc
        except Exception as exc:
            raise UserbotError(f"Sign-in failed: {exc}") from exc
        if not hasattr(signed, "id"):
            await self.cancel()
            raise UserbotError("Sign-in incomplete — tap Resend or send /login to try again.")
        self.stage = "done"
        return "done"

    async def resend(self) -> tuple[str, str]:
        """Ask Telegram to deliver a fresh code (keeps the same session).

        Returns ``(phone, where)`` like :meth:`begin`.
        """
        if not self.active or not self.phone:
            raise UserbotError("No login in progress — send /login first.")
        try:
            sent = await self.client.resend_code(self.phone, self.phone_code_hash)
        except FloodWait as exc:
            raise UserbotError(
                f"Telegram says to wait {int(getattr(exc, 'value', 60))} seconds "
                f"before resending. Try again shortly."
            ) from exc
        except PhoneNumberFlood as exc:
            await self.cancel()
            raise UserbotError(
                "This number is temporarily blocked from requesting codes. "
                "Wait a few hours and send /login again."
            ) from exc
        except Exception as exc:
            raise UserbotError(f"Resend failed: {exc}") from exc
        self.phone_code_hash = sent.phone_code_hash
        self.stage = "code"
        where = _CODE_WHERE.get(
            getattr(getattr(sent, "type", None), "name", ""),
            "by Telegram (app or SMS — check both)",
        )
        return self.phone, where

    async def submit_password(self, password: str) -> str:
        """Step 3 (only when 2FA is on): verify the cloud password."""
        if not self.active or self.stage != "password":
            raise UserbotError("No password step in progress — send /login first.")
        try:
            await self.client.check_password(password)
        except PasswordHashInvalid as exc:
            raise UserbotError("Wrong password — try again.") from exc
        except Exception as exc:
            raise UserbotError(f"Password check failed: {exc}") from exc
        self.stage = "done"
        return "done"

    def _reset(self) -> None:
        self.client = None
        self.phone = ""
        self.phone_code_hash = ""
        self.stage = ""
        self.needs_password = False

    async def finish(self) -> tuple[Client, str, object]:
        """Success: return (client, session_string, me).

        The client is detached but NOT disconnected — the playback engine
        takes ownership of the live connection.
        """
        if not self.active or self.stage != "done":
            raise UserbotError("Login is not finished yet.")
        session_string = await self.client.export_session_string()
        me = await self.client.get_me()
        save_session_to_env(session_string)
        app = self.client
        self._reset()
        return app, session_string, me

    async def cancel(self) -> None:
        """Abort any in-flight login and disconnect cleanly."""
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._reset()


login_manager = LoginManager()


# --------------------------------------------------------------------------
# Silent login at startup (saved session)
# --------------------------------------------------------------------------

def _build_client_from_session(session_string: str) -> Client:
    return Client(
        USER_SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
    )


async def ensure_userbot() -> Client:
    """Start the user client from the saved session (silent, no prompts).

    Raises :class:`UserbotError` when there is no session or it is invalid —
    the owner then connects the account with /login inside the bot DM.
    """
    if not USERBOT_SESSION:
        raise UserbotError("No saved session — the owner must send /login in DM.")
    print("[userbot] Logging in with the saved session ...")
    app = _build_client_from_session(USERBOT_SESSION)
    try:
        await app.start()
    except (AuthKeyUnregistered, KeyError, ValueError) as exc:
        try:
            await app.disconnect()
        except Exception:  # noqa: BLE001
            pass
        remove_session_from_env()
        raise UserbotError(
            "The saved session is invalid or expired. Connect the account "
            "again with /login in the bot's DM."
        ) from exc
    except UserDeactivated as exc:
        raise UserbotError("That account was deactivated by Telegram.") from exc
    except Exception as exc:
        raise UserbotError(f"Userbot login failed: {exc}") from exc

    me = await app.get_me()
    name = getattr(me, "first_name", "?") if me else "?"
    uid = getattr(me, "id", 0) if me else 0
    premium = " (Premium)" if getattr(me, "is_premium", False) else ""
    print(_console(f"[userbot] Logged in as {name} (ID {uid}){premium}"))
    return app


# --------------------------------------------------------------------------
# Engine state (who is currently playing audio)
# --------------------------------------------------------------------------

_engine = {"client": None, "player": None}


def mark_connected(client: Client, player) -> None:
    _engine["client"] = client
    _engine["player"] = player


def mark_disconnected() -> None:
    _engine["client"] = None
    _engine["player"] = None


def is_connected() -> bool:
    return _engine["client"] is not None


def get_player():
    return _engine["player"]


def get_client():
    return _engine["client"]


async def disconnect_userbot() -> None:
    """Stop the userbot client and clear engine state."""
    client = _engine["client"]
    mark_disconnected()
    if client is not None:
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            pass
