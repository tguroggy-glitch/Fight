"""Configuration loading for the voice-chat bot.

Reads a `.env` file (hand-rolled parser, no extra dependency) and validates
that every required value is present before the bot starts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _parse_env_file(path: Path) -> None:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_parse_env_file(PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"Missing required setting {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


API_ID: int = int(os.environ.get("API_ID", "22592411"))
API_HASH: str = os.environ.get("API_HASH", "25dd73f28e522cd65db1b246f107351b")
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "8892439365:AAEdp9A-8dL1VFJzFIdfPCghFKE2QEwqcFw")

OWNER_ID: int = int(os.environ.get("OWNER_ID", "8801035660"))
# Userbot: use your personal Telegram account to join voice chats and play
# audio. This lets the music play in channel voice chats where a bot can't
# start a call and gives you premium emoji sending ability.
# Leave USERBOT_SESSION blank to fall back to the bot-only mode.
USERBOT_SESSION: str = os.environ.get("USERBOT_SESSION", "").strip()
USER_PHONE: str = os.environ.get("USER_PHONE", "").strip()

# Master switch for userbot mode (your account joins calls & plays audio).
USERBOT_ENABLED: bool = _bool("USERBOT_ENABLED", True)

# True = use userbot for voice chat joins and playback (updated at runtime
# after a successful login too).
USE_USERBOT: bool = bool(USERBOT_SESSION) or bool(USER_PHONE)

CONTACT_USERNAME: str = os.environ.get("CONTACT_USERNAME", "@pluto_paglu").strip()
if not CONTACT_USERNAME.startswith("@"):
    CONTACT_USERNAME = f"@{CONTACT_USERNAME}" if CONTACT_USERNAME else "@your_username"

# Owner username for display
OWNER_USERNAME: str = os.environ.get("OWNER_USERNAME", "@pluto_paglu").strip()
if not OWNER_USERNAME.startswith("@"):
    OWNER_USERNAME = f"@{OWNER_USERNAME}" if OWNER_USERNAME else "@owner"

# The bot's own @username — filled at runtime after connecting (used for
# "open me in PM" buttons in groups).
BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")

SESSION_NAME: str = os.environ.get("SESSION_NAME", "vcbot").strip() or "vcbot"
DB_PATH: Path = (PROJECT_ROOT / os.environ.get("DB_PATH", "bot.db")).resolve()
DOWNLOAD_DIR: Path = (PROJECT_ROOT / os.environ.get("DOWNLOAD_DIR", "downloads")).resolve()

AUTO_START_CALL: bool = _bool("AUTO_START_CALL", True)
AUTO_LEAVE_EMPTY: bool = _bool("AUTO_LEAVE_EMPTY", True)
MAX_UPLOAD_MB: int = int(os.environ.get("MAX_UPLOAD_MB", "200").strip() or "200")

# Wrap UI icons in <tg-emoji> so Telegram renders the animated premium emoji
# from the extracted pack when allowed; otherwise clients fall back to the
# plain Unicode emoji. Disable with PREMIUM_EMOJI=false.
PREMIUM_EMOJI: bool = _bool("PREMIUM_EMOJI", True)

# Whether the userbot can send custom emoji for you (determined at runtime
# after login — bots strip <tg-emoji> unless they have a Fragment username).
USERBOT_CAN_SEND_EMOJI: bool = False  # set after login

SPEED_MIN: float = 0.25
SPEED_MAX: float = 3.0
VOLUME_MIN: int = 0
VOLUME_MAX: int = 200

PAYWALL_TEXT = (
    "🔒 <b>Private Bot — Paid Access</b>\n\n"
    "🎧 Premium voice-chat music bot: plays audio in group & channel "
    "voice chats with animated premium emoji, loop, speed 0.25x–3x, "
    "equalizer and a personal library.\n\n"
    "💰 <b>Want access?</b>\n"
    f"DM the owner {CONTACT_USERNAME} and buy a time-limited subscription.\n\n"
    "🆔 Send /id here first — you'll need your Telegram ID when you pay."
)

ACCESS_LEVELS = {"stranger": 0, "subscriber": 1, "admin": 2, "owner": 3}