"""Find emojis used in bot UI strings that have NO premium ID in the new pack.

Those are the ones that render as plain Unicode instead of animated premium
emoji — the "somewhere the emoji is not working" problem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import premium_emoji
import tgemoji

EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # symbols, pictographs
    "\U00002600-\U000027BF"   # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"              # VS16
    "\U0000200D"              # ZWJ (inside sequences)
    "\U00002190-\U000021FF"   # arrows
    "\U00002300-\U000023FF"
    "\U000025A0-\U000025FF"   # geometric shapes (play triangle etc.)
    "]+"
)

files = ["handlers.py", "panels.py", "main.py", "player.py", "userbot.py", "animations.py"]
root = Path(__file__).resolve().parent

# Build set of pack keys (also with VS16-stripped variants)
pack = {**tgemoji.ALIASES, **premium_emoji.PREMIUM_EMOJI}
pack_keys = set(pack)
pack_stripped = {k.replace("\ufe0f", ""): k for k in pack}

used: dict[str, list[str]] = {}
for fname in files:
    p = root / fname
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if '"""' in line or line.strip().startswith("#"):
            # still scan: docstrings/comments excluded roughly; keep it simple
            pass
        for m in EMOJI_RE.finditer(line):
            token = m.group(0)
            used.setdefault(token, []).append(f"{fname}:{lineno}")

missing = {}
covered = 0
for token, locs in sorted(used.items()):
    if token in pack_keys or token.replace("\ufe0f", "") in pack_stripped:
        covered += 1
        continue
    missing[token] = locs[:3]

print(f"pack size        : {len(pack)}")
print(f"distinct used    : {len(used)}")
print(f"covered by pack  : {covered}")
print(f"MISSING from pack: {len(missing)}")
print()
for token, locs in sorted(missing.items()):
    print(f"  {token!r:12} U+{' U+'.join(f'{ord(c):04X}' for c in token)}  e.g. {', '.join(locs)}")
