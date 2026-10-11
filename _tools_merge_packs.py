"""Merge the new @NewEmojis pack (145) with the old @fStikBot pack (115).

New pack wins on conflicts (fresher IDs); the old pack backfills icons the
new one lacks (crown/gem/speaker/EQ/musical icons and the like).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent

# ---- Parse the old pack (already regenerated as new-only) ------------------
old_text = (ROOT / "premium_emoji.py").read_text(encoding="utf-8")
# It currently contains ONLY the new pack. Recover the OLD pack from git is
# unreliable (repo may be untracked) — instead re-extract from the fStikBot
# HTML if present, else parse from the generated file (new pack only).
import glob

fst_matches = glob.glob(str(Path(r"C:\Users\anike\Downloads\Telegram Desktop") / "*fStikBot*.html"))
merged: dict[str, str] = {}

if fst_matches:
    raw = Path(fst_matches[0]).read_text(encoding="utf-8", errors="replace")
    row_re = re.compile(r'emoji-id=\\?"(\d{10,25})\\?"&gt;(.+?)&lt;/tg-emoji&gt;', re.DOTALL)
    for emoji_id, emoji in row_re.findall(raw):
        emoji = emoji.strip()
        if emoji and not any(ch in emoji for ch in "<>&"):
            merged.setdefault(emoji, emoji_id)  # old first (fallback base)
    print(f"old pack rows: {len(merged)}")

# ---- Parse the new pack from the regenerated premium_emoji.py --------------
# Import it fresh (it currently holds only the new pack)
sys.path.insert(0, str(ROOT))
for mod in list(sys.modules):
    if mod in ("premium_emoji",):
        del sys.modules[mod]
import premium_emoji  # noqa: E402

new_pack = dict(premium_emoji.PREMIUM_EMOJI)
print(f"new pack rows: {len(new_pack)}")

# ---- Merge: new wins --------------------------------------------------------
merged.update(new_pack)
print(f"merged total : {len(merged)}")

# ---- Regenerate premium_emoji.py -------------------------------------------
def key_repr(emoji: str) -> str:
    """Always-valid dict-key literal (repr handles ZWJ sequences safely)."""
    return repr(emoji)

lines = [
    '"""Premium (custom) emoji IDs — merged @NewEmojis (145) + @fStikBot packs.',
    "",
    'Maps Unicode emoji -> custom emoji ID. Wrapping text with',
    '<tg-emoji emoji-id="...">X</tg-emoji> in HTML parse mode renders the',
    "animated premium emoji; clients without custom-emoji support fall back",
    "to the plain Unicode emoji inside the tag.",
    "",
    "Auto-generated - do not edit by hand.",
    '"""',
    "",
    "PREMIUM_EMOJI: dict[str, str] = {",
]
for emoji, emoji_id in merged.items():
    lines.append(f"    {key_repr(emoji)}: \"{emoji_id}\",")
lines.append("}")
lines.append("")

(ROOT / "premium_emoji.py").write_text("\n".join(lines), encoding="utf-8")
print(f"wrote premium_emoji.py with {len(merged)} entries")
