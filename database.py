"""SQLite persistence: admins, subscriptions, audio library, per-chat settings.

Everything the bot needs to survive a restart lives here:
- admins added by the owner
- time-limited subscriptions granted by the owner
- the uploaded audio library (kept as Telegram file references, no disk bloat)
- per-chat playback state (queue, current track, loop mode, speed, volume)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import DB_PATH

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    added_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subs (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    expires_at  INTEGER NOT NULL,
    granted_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    chat_id     INTEGER NOT NULL,          -- chat where the file message lives
    message_id  INTEGER NOT NULL,          -- message holding the audio file
    duration    INTEGER NOT NULL DEFAULT 0,
    uploaded_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id     INTEGER PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    loop_mode   TEXT NOT NULL DEFAULT 'off',   -- off | one | queue
    speed       REAL NOT NULL DEFAULT 1.0,
    volume      INTEGER NOT NULL DEFAULT 100,
    equalizer   TEXT NOT NULL DEFAULT 'flat',  -- flat | bass | treble | vocal | rock | electronic | classical | pop
    joined      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS banned (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    banned_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS call_state (
    chat_id     INTEGER PRIMARY KEY,
    queue       TEXT NOT NULL DEFAULT '[]',    -- JSON list of track ids
    current_id  INTEGER,                       -- currently playing track id
    position    REAL NOT NULL DEFAULT 0.0,     -- offset inside current track (s)
    paused      INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL
);
"""


def init(db_path: Path = DB_PATH) -> None:
    global _conn
    with _lock:
        if _conn is not None:
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        # Migrate older databases that predate the equalizer column.
        cols = [r[1] for r in _conn.execute("PRAGMA table_info(chat_settings)").fetchall()]
        if "equalizer" not in cols:
            _conn.execute(
                "ALTER TABLE chat_settings ADD COLUMN equalizer TEXT NOT NULL DEFAULT 'flat'"
            )
        # Migrate older databases that predate trial subscriptions.
        sub_cols = [r[1] for r in _conn.execute("PRAGMA table_info(subs)").fetchall()]
        if "trial" not in sub_cols:
            _conn.execute("ALTER TABLE subs ADD COLUMN trial INTEGER NOT NULL DEFAULT 0")
        _conn.commit()


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    if _conn is None:
        raise RuntimeError("database not initialised; call database.init() first")
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


# ---------------------------------------------------------------- admins ----

def add_admin(user_id: int, name: str = "") -> bool:
    """Returns False if the admin already existed."""
    cur = _execute(
        "INSERT OR IGNORE INTO admins (user_id, name, added_at) VALUES (?, ?, ?)",
        (user_id, name, int(time.time())),
    )
    return cur.rowcount > 0


def remove_admin(user_id: int) -> bool:
    cur = _execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    return cur.rowcount > 0


def is_admin(user_id: int) -> bool:
    cur = _execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return cur.fetchone() is not None


def list_admins() -> list[sqlite3.Row]:
    return _execute("SELECT * FROM admins ORDER BY added_at").fetchall()


# --------------------------------------------------------- subscriptions ----

def grant_sub(user_id: int, days: int, name: str = "") -> int:
    """Grant (or extend) a subscription. Returns the new expiry timestamp."""
    now = int(time.time())
    expires = now + days * 86400
    existing = _execute("SELECT expires_at FROM subs WHERE user_id = ?", (user_id,)).fetchone()
    if existing and existing["expires_at"] > now:
        expires = existing["expires_at"] + days * 86400  # extend, don't lose paid days
    _execute(
        "INSERT INTO subs (user_id, name, expires_at, granted_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET expires_at = excluded.expires_at, "
        "name = excluded.name",
        (user_id, name, expires, now),
    )
    return expires


def revoke_sub(user_id: int) -> bool:
    cur = _execute("DELETE FROM subs WHERE user_id = ?", (user_id,))
    return cur.rowcount > 0


def get_sub(user_id: int) -> sqlite3.Row | None:
    row = _execute("SELECT * FROM subs WHERE user_id = ?", (user_id,)).fetchone()
    if row and row["expires_at"] <= time.time():
        return None  # expired
    return row


def is_subscribed(user_id: int) -> bool:
    return get_sub(user_id) is not None


def list_subs(include_expired: bool = False) -> list[sqlite3.Row]:
    if include_expired:
        return _execute("SELECT * FROM subs ORDER BY expires_at").fetchall()
    return _execute(
        "SELECT * FROM subs WHERE expires_at > ? ORDER BY expires_at",
        (int(time.time()),),
    ).fetchall()


# ------------------------------------------------------------- tracks ----

def add_track(title: str, chat_id: int, message_id: int, duration: int = 0) -> int:
    cur = _execute(
        "INSERT INTO tracks (title, chat_id, message_id, duration, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, chat_id, message_id, duration, int(time.time())),
    )
    return int(cur.lastrowid or 0)


def get_track(track_id: int) -> sqlite3.Row | None:
    return _execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def list_tracks() -> list[sqlite3.Row]:
    return _execute("SELECT * FROM tracks ORDER BY id").fetchall()


def rename_track(track_id: int, title: str) -> None:
    _execute("UPDATE tracks SET title = ? WHERE id = ?", (title, track_id))


def delete_track(track_id: int) -> bool:
    """Delete a track and remove it from any queue referencing it."""
    cur = _execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    for row in _execute("SELECT chat_id, queue FROM call_state").fetchall():
        try:
            queue = json.loads(row["queue"])
        except (json.JSONDecodeError, TypeError):
            continue
        if track_id in queue:
            queue = [t for t in queue if t != track_id]
            _execute(
                "UPDATE call_state SET queue = ? WHERE chat_id = ?",
                (json.dumps(queue), row["chat_id"]),
            )
    return cur.rowcount > 0


def count_tracks() -> int:
    return _execute("SELECT COUNT(*) AS c FROM tracks").fetchone()["c"]


# ------------------------------------------------------ chat settings ----

def upsert_chat(chat_id: int, title: str = "") -> None:
    _execute(
        "INSERT INTO chat_settings (chat_id, title) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title",
        (chat_id, title),
    )


def get_chat_setting(chat_id: int) -> sqlite3.Row | None:
    return _execute("SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)).fetchone()


def set_loop(chat_id: int, mode: str) -> None:
    upsert_chat(chat_id)
    _execute("UPDATE chat_settings SET loop_mode = ? WHERE chat_id = ?", (mode, chat_id))


def set_speed(chat_id: int, speed: float) -> None:
    upsert_chat(chat_id)
    _execute("UPDATE chat_settings SET speed = ? WHERE chat_id = ?", (speed, chat_id))


def set_volume(chat_id: int, volume: int) -> None:
    upsert_chat(chat_id)
    _execute("UPDATE chat_settings SET volume = ? WHERE chat_id = ?", (volume, chat_id))


def set_equalizer(chat_id: int, preset: str) -> None:
    upsert_chat(chat_id)
    _execute("UPDATE chat_settings SET equalizer = ? WHERE chat_id = ?", (preset, chat_id))


def get_equalizer(chat_id: int) -> str:
    row = _execute("SELECT equalizer FROM chat_settings WHERE chat_id = ?", (chat_id,)).fetchone()
    return row["equalizer"] if row else "flat"


def set_joined(chat_id: int, joined: bool) -> None:
    upsert_chat(chat_id)
    _execute(
        "UPDATE chat_settings SET joined = ? WHERE chat_id = ?",
        (1 if joined else 0, chat_id),
    )


def list_chats(only_joined: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM chat_settings"
    if only_joined:
        sql += " WHERE joined = 1"
    return _execute(sql + " ORDER BY chat_id").fetchall()


# ------------------------------------------------------------- banned ----

def ban_user(user_id: int, name: str = "") -> bool:
    cur = _execute(
        "INSERT OR IGNORE INTO banned (user_id, name, banned_at) VALUES (?, ?, ?)",
        (user_id, name, int(time.time())),
    )
    return cur.rowcount > 0


def unban_user(user_id: int) -> bool:
    cur = _execute("DELETE FROM banned WHERE user_id = ?", (user_id,))
    return cur.rowcount > 0


def is_banned(user_id: int) -> bool:
    cur = _execute("SELECT 1 FROM banned WHERE user_id = ?", (user_id,))
    return cur.fetchone() is not None


def list_banned() -> list[sqlite3.Row]:
    return _execute("SELECT * FROM banned ORDER BY banned_at").fetchall()


def count_banned() -> int:
    return _execute("SELECT COUNT(*) AS c FROM banned").fetchone()["c"]


# ------------------------------------------------------------- trials ----

def had_sub(user_id: int) -> bool:
    """True if the user ever had a subscription (active or expired)."""
    row = _execute(
        "SELECT 1 FROM subs WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None


def grant_trial(user_id: int, days: int = 1, name: str = "") -> int | None:
    """Give a one-time free trial. Returns the expiry, or None if the user
    already had a subscription/trial before (trials are once per user)."""
    if had_sub(user_id):
        return None
    now = int(time.time())
    expires = now + days * 86400
    _execute(
        "INSERT INTO subs (user_id, name, expires_at, granted_at, trial) "
        "VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT(user_id) DO UPDATE SET expires_at = excluded.expires_at, "
        "name = excluded.name, trial = 1",
        (user_id, name, expires, now),
    )
    return expires


# --------------------------------------------------------- call state ----

def clear_all_queues() -> int:
    """Empty the waiting queue of every chat (current playback continues).
    Returns how many chats had a queue to clear."""
    rows = _execute("SELECT chat_id, queue FROM call_state").fetchall()
    changed = 0
    for row in rows:
        try:
            queue = json.loads(row["queue"])
        except (json.JSONDecodeError, TypeError):
            continue
        if queue:
            _execute(
                "UPDATE call_state SET queue = '[]' WHERE chat_id = ?",
                (row["chat_id"],),
            )
            changed += 1
    return changed


def save_call_state(chat_id: int, queue: list[int], current_id: int | None,
                    position: float = 0.0, paused: bool = False) -> None:
    _execute(
        "INSERT INTO call_state (chat_id, queue, current_id, position, paused, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET queue = excluded.queue, "
        "current_id = excluded.current_id, position = excluded.position, "
        "paused = excluded.paused, updated_at = excluded.updated_at",
        (chat_id, json.dumps(queue), current_id, position, 1 if paused else 0, int(time.time())),
    )


def get_call_state(chat_id: int) -> dict[str, Any]:
    row = _execute("SELECT * FROM call_state WHERE chat_id = ?", (chat_id,)).fetchone()
    if not row:
        return {"queue": [], "current_id": None, "position": 0.0, "paused": False}
    try:
        queue = json.loads(row["queue"])
    except (json.JSONDecodeError, TypeError):
        queue = []
    return {
        "queue": queue,
        "current_id": row["current_id"],
        "position": row["position"],
        "paused": bool(row["paused"]),
    }


def clear_call_state(chat_id: int) -> None:
    _execute("DELETE FROM call_state WHERE chat_id = ?", (chat_id,))
