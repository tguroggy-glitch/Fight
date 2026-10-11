"""Message formatting helpers: premium emoji substitution + HTML escaping.

`emo()` wraps known Unicode emoji in <tg-emoji emoji-id="..."> spans using the
merged pack (premium_emoji.py). Icons that have no direct pack entry are
ALIASED to the closest packed animated emoji (the original icon stays as the
fallback text, so clients that can't render custom emoji still show it).

Only use emo() on strings sent with HTML parse mode - never on user input
(user text must go through esc() first, or the markup becomes injectable).
"""

from __future__ import annotations

import html
import re
from functools import lru_cache

from config import PREMIUM_EMOJI


def esc(text: str) -> str:
    """Escape a plain string for HTML parse mode."""
    return html.escape(str(text), quote=False)


# Icons used by the UI that the packs don't contain directly -> nearest
# animated premium emoji they may borrow. The visible fallback text is the
# original icon, so nothing looks wrong if custom emoji can't render.
ALIASES: dict[str, str] = {
    "→": "6154421933095000846",   # ➡️
    "⏪": "6129802875611651951",  # ⏩
    "⏭": "6129802875611651951",
    "⏯": "5116444615301399317",  # 🔘
    "⏱": "5123230779593196220",  # ⏰
    "⏳": "5123230779593196220",
    "⏸": "5134537521518085000",  # ⏹
    "✏️": "5104960787579929462", # ✨
    "❌": "5116151848855667552", # 🚫
    "❎": "5084979757905347540", # ✅
    "➕": "4918438965029110683", # 🆕
    "➖": "5116151848855667552",
    "⬅️": "6154421933095000846",
    "⬆️": "5116395218882528029", # ⏫
    "🆔": "5116582462276764538", # 👤
    "🎛": "6129544215501216365", # 🎚
    "🎧": "5172447776205702031", # 🎵
    "👉": "6129523887421006760", # 👆
    "👥": "5116582462276764538",
    "💾": "6129760505759276442", # 💠
    "📋": "5123359615727174427", # 💡
    "📖": "6129639980387015660", # 🚀
    "📚": "5172447776205702031",
    "📜": "5172447776205702031",
    "📤": "5116395218882528029",
    "📥": "5116204921766544244",
    "📭": "5116204921766544244",
    "📲": "5172893417717367746", # 📞
    "🔁": "5172447776205702031",
    "🔂": "5172447776205702031",
    "🔄": "4906943755644306322", # 🌐
    "🔉": "6129492160497589882", # 🔈
    "🔊": "6129492160497589882",
    "🔑": "6129731845442510016", # 🔐
    "🔢": "5141109049114232089", # 1️⃣
    "🔧": "4904936030232117798", # ⚙️
    "🔴": "5134537521518085000",
    "🚪": "5116151848855667552",
    "🛠": "4904936030232117798",
    "🛡": "4904500559203009298", # 🔒
    "🤷": "5116268964023894989", # 😐
    # spinner frames (animations.py) — all borrow the alarm-clock animation
    "🕒": "5123230779593196220",
    "🕓": "5123230779593196220",
    "🕔": "5123230779593196220",
    "🕕": "5123230779593196220",
    # play triangle used in status/guide texts — borrow the toggle animation
    "▶️": "5116444615301399317",
    "▶": "5116444615301399317",
    "◀️": "6154421933095000846",
}


@lru_cache(maxsize=1)
def _combined() -> dict[str, str]:
    """Direct pack entries + aliases (aliases never override the pack)."""
    combined = dict(ALIASES)
    import premium_emoji

    combined.update(premium_emoji.PREMIUM_EMOJI)
    return combined


@lru_cache(maxsize=1)
def _pattern() -> re.Pattern[str] | None:
    """Regex matching any emoji we can render premium (longest first)."""
    if not PREMIUM_EMOJI:
        return None
    combined = _combined()
    if not combined:
        return None
    keys = sorted(combined, key=len, reverse=True)
    return re.compile("(" + "|".join(re.escape(k) for k in keys) + ")")


def emo(text: str) -> str:
    """Substitute premium-emoji markup into an HTML-safe string."""
    if not PREMIUM_EMOJI:
        return text
    pattern = _pattern()
    if pattern is None:
        return text
    combined = _combined()
    return pattern.sub(
        lambda m: f'<tg-emoji emoji-id="{combined[m.group(1)]}">'
        f"{m.group(1)}</tg-emoji>",
        text,
    )


def esc_emo(text: str) -> str:
    """Escape user text, then add premium emoji markup (safe combo)."""
    return emo(esc(text))


_TAG_RE = re.compile(r"</?tg-emoji[^>]*>")


def plain(text: str) -> str:
    """Strip premium-emoji markup — for console prints, not messages."""
    return _TAG_RE.sub("", text)


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\U0000FE0F\U00002190-\U000021FF"
    "\U00002300-\U000023FF]"
)


def console(text: str) -> str:
    """Terminal-safe output: no tags, no emoji, pure clean ASCII."""
    return _EMOJI_RE.sub("", plain(text)).strip()
