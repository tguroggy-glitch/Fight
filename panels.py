"""Inline keyboards for the owner panel and playback controls.

IMPORTANT: button text must stay PLAIN Unicode emoji. Telegram does NOT parse
HTML / <tg-emoji> tags inside InlineKeyboardButton text, so wrapping buttons
in te.emo() shows raw "<tg-emoji ...>" garbage instead of emoji. Premium
animated emoji (<tg-emoji>) is only for MESSAGE text (handlers.py), which is
sent with HTML parse mode.
"""

from __future__ import annotations

import time

from pyrogram.types import InlineKeyboardButton
from pyrogram.types import InlineKeyboardMarkup

import database as db

PAGE_SIZE = 6


def _button(label: str, callback_data: str) -> InlineKeyboardButton:
    """Button factory with a built-in safety net.

    Telegram silently swallows callback_data that has no ':', and raw
    <tg-emoji> tags must never leak into button labels (button text is not
    parsed as HTML — it would show the tag itself). This guard makes both
    mistakes impossible: malformed callback_data becomes 'noop' (harmless)
    and labels are stripped of any markup.
    """
    from tgemoji import plain as _strip
    clean = _strip(label)
    if ":" not in callback_data or len(callback_data) > 64:
        callback_data = "noop"
    return InlineKeyboardButton(clean, callback_data=callback_data)


def main_menu() -> InlineKeyboardMarkup:
    import userbot as ub  # late import: panels load before userbot

    n_tracks = db.count_tracks()
    n_admins = len(db.list_admins())
    n_subs = len(db.list_subs())
    n_chats = len(db.list_chats())
    ub_label = "💎 Userbot: Active" if ub.is_connected() else "⚪ Userbot: Offline"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"📤 Upload ({n_tracks})", callback_data="up:info"),
            InlineKeyboardButton(f"📚 Library ({n_tracks})", callback_data="lib:0"),
        ],
        [
            InlineKeyboardButton(f"👥 Admins ({n_admins})", callback_data="adm:list"),
            InlineKeyboardButton(f"⏳ Subs ({n_subs})", callback_data="sub:list"),
        ],
        [
            InlineKeyboardButton(f"🌐 Chats ({n_chats})", callback_data="chats:list"),
            InlineKeyboardButton("📊 Stats", callback_data="stats:show"),
        ],
        [
            InlineKeyboardButton("📣 Broadcast", callback_data="cast:ask"),
            InlineKeyboardButton("🧹 Cleanup", callback_data="clean:ask"),
        ],
        [InlineKeyboardButton(ub_label, callback_data="ub:status")],
        [
            InlineKeyboardButton("❓ Help", callback_data="help:0"),
            InlineKeyboardButton("🔄 Refresh", callback_data="panel:main"),
        ],
    ])


def library_page(page: int) -> InlineKeyboardMarkup:
    tracks = db.list_tracks()
    if not tracks:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📭 Library Empty", callback_data="noop")],
            [InlineKeyboardButton("📤 Upload First Track", callback_data="up:info")],
            [InlineKeyboardButton("⬅️ Back to Panel", callback_data="panel:main")],
        ])
    pages = max(1, (len(tracks) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = []
    for track in tracks[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        minutes, seconds = divmod(track["duration"] or 0, 60)
        label = f"🎵 {track['title']}"[:60] + f" ({minutes}:{seconds:02d})"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"trk:view:{track['id']}")
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"lib:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"lib:{page + 1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton("📤 Upload", callback_data="up:info"),
        InlineKeyboardButton("⬅️ Back", callback_data="panel:main"),
    ])
    return InlineKeyboardMarkup(rows)


def track_view(track_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Play in Chat", callback_data=f"trk:playpick:{track_id}")],
        [
            InlineKeyboardButton("➕ Add to Queue", callback_data=f"trk:qpick:{track_id}"),
            InlineKeyboardButton("✏️ Rename", callback_data=f"trk:rename:{track_id}"),
        ],
        [InlineKeyboardButton("🗑 Delete Track", callback_data=f"trk:del:{track_id}")],
        [InlineKeyboardButton("📚 Back to Library", callback_data="lib:0")],
    ])


def chat_pick(chat_id: int, action: str, page: int = 0) -> InlineKeyboardMarkup:
    """Pick a chat (for play / queue actions). action: 'play' or 'queue'."""
    chats = db.list_chats()
    if not chats:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📭 No Connected Chats", callback_data="noop")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"trk:view:{chat_id}")],
        ])
    pages = max(1, (len(chats) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = []
    for chat in chats[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        title = (chat["title"] or str(chat["chat_id"]))[:50]
        rows.append([
            InlineKeyboardButton(
                f"🌐 {title}",
                callback_data=f"trk:{action}:{chat_id}:{chat['chat_id']}",
            )
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"trk:cpick:{chat_id}:{action}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"trk:cpick:{chat_id}:{action}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"trk:view:{chat_id}")])
    return InlineKeyboardMarkup(rows)


def track_controls(chat_id: int) -> InlineKeyboardMarkup:
    setting = db.get_chat_setting(chat_id)
    loop = setting["loop_mode"] if setting else "off"
    speed = setting["speed"] if setting else 1.0
    volume = setting["volume"] if setting else 100

    loop_labels = {
        "off": "🔁 Loop: Off",
        "one": "🔂 Loop: One",
        "queue": "🔁 Loop: Queue",
    }
    loop_label = loop_labels.get(loop, "🔁 Loop: Off")

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏯ Play/Pause", callback_data=f"ctl:toggle:{chat_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"ctl:skip:{chat_id}"),
        ],        [_button("⏹ Stop & Leave", f"ctl:stop:{chat_id}"),
            _button("📊 Status", f"ctl:status:{chat_id}"),
        ],
        [
            _button(loop_label, f"ctl:cycleloop:{chat_id}"),
            _button(f"🚀 Speed: {speed:g}x", f"ctl:speed:{chat_id}"),
        ],
        [
            _button("🎚 Equalizer", f"ctl:eqmenu:{chat_id}"),
            _button(f"🔊 Vol: {volume}%", f"ctl:volmenu:{chat_id}"),
        ],
        [
            _button("⏪ -30s", f"ctl:seek:{chat_id}:-30"),
            _button("🔉 -10%", f"ctl:voldown:{chat_id}"),
            _button("🔊 +10%", f"ctl:volup:{chat_id}"),
            _button("+30s ⏩", f"ctl:seek:{chat_id}:30"),
        ],
        [
            _button("🎲 Random", f"ctl:random:{chat_id}"),
            _button("🧹 Clear Queue", f"ctl:clrqueue:{chat_id}"),
            _button("♻️ Reset", f"ctl:reset:{chat_id}"),
        ],
        [_button("📚 Library", "lib:0")],
        [_button("❌ Close", "ctl:close")],
    ])


def admins_menu() -> InlineKeyboardMarkup:
    rows = []
    for admin in db.list_admins():
        name = (admin["name"] or str(admin["user_id"]))[:40]
        rows.append([
            InlineKeyboardButton(f"➖ {name}", callback_data=f"adm:confirmrm:{admin['user_id']}"),
        ])
    rows.append([InlineKeyboardButton("➕ Add Admin", callback_data="adm:add")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)


def subs_menu() -> InlineKeyboardMarkup:
    rows = []
    now = time.time()
    for sub in db.list_subs(include_expired=True):
        left = sub["expires_at"] - now
        state = "✅" if left > 0 else "⛔"
        days = max(0, int(left // 86400))
        name = str(sub["name"] or sub["user_id"])[:30]
        rows.append([
            InlineKeyboardButton(
                f"{state} {name} ({days}d left)",
                callback_data=f"sub:view:{sub['user_id']}",
            )
        ])
    rows.append([InlineKeyboardButton("➕ Grant Subscription", callback_data="sub:add")])
    rows.append([InlineKeyboardButton("🧹 Remove Expired", callback_data="clean:subs")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)


def sub_view(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏳ +7 Days", callback_data=f"sub:extend:{user_id}:7"),
            InlineKeyboardButton("⏳ +30 Days", callback_data=f"sub:extend:{user_id}:30"),
        ],
        [
            InlineKeyboardButton("⏳ +90 Days", callback_data=f"sub:extend:{user_id}:90"),
            InlineKeyboardButton("🗑 Revoke", callback_data=f"sub:revoke:{user_id}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="sub:list")],
    ])


def admin_confirm_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Remove", callback_data=f"adm:rm:{user_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="adm:list"),
        ],
    ])


def chats_menu() -> InlineKeyboardMarkup:
    rows = []
    for chat in db.list_chats():
        title = (chat["title"] or str(chat["chat_id"]))[:40]
        if chat["joined"]:
            label = f"🔴 {title} — Leave"
            data = f"chat:leave:{chat['chat_id']}"
        else:
            label = f"⚪ {title} — Join"
            data = f"chat:join:{chat['chat_id']}"
        rows.append([InlineKeyboardButton(label, callback_data=data)])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)


def speed_menu(chat_id: int) -> InlineKeyboardMarkup:
    speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
    rows = []
    for i in range(0, len(speeds), 5):
        chunk = speeds[i:i+5]
        rows.append([
            InlineKeyboardButton(f"🚀 {s:g}x", callback_data=f"ctl:setspeed:{chat_id}:{s}")
            for s in chunk
        ])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"ctl:status:{chat_id}")])
    return InlineKeyboardMarkup(rows)


def volume_menu(chat_id: int) -> InlineKeyboardMarkup:
    volumes = [0, 25, 50, 75, 100, 125, 150, 175, 200]
    rows = []
    for i in range(0, len(volumes), 5):
        chunk = volumes[i:i+5]
        rows.append([
            InlineKeyboardButton(f"🔊 {v}%", callback_data=f"ctl:setvol:{chat_id}:{v}")
            for v in chunk
        ])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"ctl:status:{chat_id}")])
    return InlineKeyboardMarkup(rows)


def equalizer_menu(chat_id: int) -> InlineKeyboardMarkup:
    presets = [
        ("Flat", "flat"),
        ("Bass Boost", "bass"),
        ("Treble Boost", "treble"),
        ("Vocal", "vocal"),
        ("Rock", "rock"),
        ("Electronic", "electronic"),
        ("Classical", "classical"),
        ("Pop", "pop"),
    ]
    rows = []
    for i in range(0, len(presets), 2):
        chunk = presets[i:i+2]
        rows.append([
            InlineKeyboardButton(f"🎚 {name}", callback_data=f"ctl:eq:{chat_id}:{preset}")
            for name, preset in chunk
        ])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"ctl:status:{chat_id}")])
    return InlineKeyboardMarkup(rows)


def stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📣 Broadcast", callback_data="cast:ask"),
            InlineKeyboardButton("🧹 Cleanup", callback_data="clean:ask"),
        ],
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="panel:main")],
    ])


def cleanup_confirm_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Clean All", callback_data="clean:do"),
            InlineKeyboardButton("❌ Cancel", callback_data="panel:main"),
        ],
    ])


def back_to_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Panel", callback_data="panel:main")],
    ])


def pm_me_button() -> InlineKeyboardMarkup:
    """Group-friendly button pointing at the bot's DM."""
    import config  # late import: username is filled at runtime

    target = config.BOT_USERNAME or config.CONTACT_USERNAME.lstrip("@")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Open the Bot in PM", url=f"https://t.me/{target}")],
    ])


def cancel_login() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Cancel Login", callback_data="ub:cancel")],
    ])


def userbot_menu(connected: bool, login_active: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if connected:
        rows.append([InlineKeyboardButton("🚪 Disconnect Account", callback_data="ub:logout")])
    elif login_active:
        rows.append([InlineKeyboardButton("🚫 Cancel Login", callback_data="ub:cancel")])
    else:
        rows.append([InlineKeyboardButton("💎 Connect My Account", callback_data="ub:begin")])
    rows.append([InlineKeyboardButton("⬅️ Back to Panel", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)
