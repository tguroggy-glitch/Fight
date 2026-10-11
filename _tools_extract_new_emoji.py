"""Extract premium emoji IDs from the new @NewEmojis HTML export.

Row layout in the HTML:
  <td class="emoji-cell"><img src="data:image/webp;base64,..."></td>
  <td><code ...>5084979757905347540</code></td>
  <td><code ...>&lt;tg-emoji emoji-id=\"5084979757905347540\"&gt;✅&lt;/tg-emoji&gt;</code></td>

The Unicode emoji is taken from the HTML-code column (the image is just a
preview webp). Quotes inside the code cell are backslash-escaped, so the
regex must accept optional backslashes.
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
matches = glob.glob(str(Path(r"C:\Users\anike\Downloads\Telegram Desktop") / "*NewEmojis*.html"))
if not matches:
    matches = glob.glob(str(Path(r"C:\Users\anike\Downloads\Telegram Desktop") / "*Boseh*.html"))
if not matches:
    raise SystemExit("HTML file not found")

html_path = Path(matches[0])
raw = html_path.read_text(encoding="utf-8", errors="replace")

# emoji-id=\"123\"&gt;EMOJI&lt;/tg-emoji&gt;   (backslashes optional)
row_re = re.compile(
    r'emoji-id=\\?"(\d{10,25})\\?"&gt;(.+?)&lt;/tg-emoji&gt;', re.DOTALL
)

pairs: dict[str, str] = {}
dupes = 0
bad = 0
for emoji_id, emoji in row_re.findall(raw):
    emoji = emoji.strip()
    if not emoji or any(ch in emoji for ch in "<>&"):
        bad += 1
        continue
    if emoji in pairs:
        dupes += 1
        continue  # keep first occurrence
    pairs[emoji] = emoji_id

print(f"file     : {html_path.name}")
print(f"rows     : {len(row_re.findall(raw))}")
print(f"unique   : {len(pairs)}  (dupes skipped: {dupes}, malformed: {bad})")
print("emojis   : " + " ".join(pairs.keys()))

# Regenerate premium_emoji.py — new pack REPLACES the old one entirely.
lines = [
    '"""Premium (custom) emoji IDs from the @NewEmojis pack (200 items).',
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
for emoji, emoji_id in pairs.items():
    # repr() handles ZWJ/VS16 sequences safely and always produces valid Python
    lines.append(f"    {emoji!r}: \"{emoji_id}\",")
lines.append("}")
lines.append("")

(ROOT / "premium_emoji.py").write_text("\n".join(lines), encoding="utf-8")
print(f"wrote premium_emoji.py with {len(pairs)} entries")
