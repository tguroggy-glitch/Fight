"""Animated message elements: progress bars, spinners, login step tracker.

All frames use premium-emoji markup (tg-emoji) when available and plain
Unicode fallbacks otherwise. Each animation edits ONE message in place
(rate-limited to stay inside Telegram's flood limits and feel smooth).
"""

from __future__ import annotations

import asyncio

import tgemoji as te


def bar(fraction: float, width: int = 10) -> str:
    """A block progress bar, premium-styled (equalizer vibe)."""
    fraction = min(1.0, max(0.0, fraction))
    filled = round(fraction * width)
    return "▰" * filled + "▱" * (width - filled)


class StepTracker:
    """Editable login progress: 3 steps, checked off as they complete.

    Call :meth:`render` after every stage change; it edits the same message
    with the new state (a small animated shimmer on the active step).
    """

    STEPS = ("Phone", "Code", "Connect")

    # 4 distinct packed animated emojis — a real spin for premium viewers
    SPIN = ("✨", "🌟", "💫", "⭐️")

    def __init__(self) -> None:
        self.message = None          # the Message we keep editing
        self.done: set[int] = set()  # indexes of completed steps
        self.current: int = 0        # index of the active step
        self._tick = 0

    def _frame(self, title: str) -> str:
        self._tick += 1
        spinner = self.SPIN[self._tick % 4]
        rows = [te.emo(f"💎 <b>{te.esc(title)}</b>"), ""]
        for i, name in enumerate(self.STEPS):
            if i in self.done:
                icon, style = "✅", "<s>"
                label = name
            elif i == self.current:
                icon, style = spinner, "<b>"
                label = f"{name}…"
            else:
                icon, style = "⚪️", "<i>"
                label = name
            pct = bar((i + 1) / len(self.STEPS) if i in self.done else i / len(self.STEPS), 6)
            rows.append(te.emo(f"{icon} {style}{label}{_close(style)}  {pct}"))
        rows.append("")
        rows.append(te.emo("📲 Send the next step right here in this chat."))
        return "\n".join(rows)

    async def attach(self, message, title: str) -> None:
        self.message = message
        await self.render(title)

    async def render(self, title: str) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit_text(self._frame(title))
        except Exception:  # noqa: BLE001 - flood waits / not modified: skip a frame
            await asyncio.sleep(0.6)

    async def complete_step(self, title: str) -> None:
        self.done.add(self.current)
        self.current = min(self.current + 1, len(self.STEPS) - 1)
        await self.render(title)

    async def fail(self, title: str, error: str) -> None:
        if self.message is None:
            return
        icon = "❌"
        rows = [te.emo(f"💎 <b>{te.esc(title)}</b>"), ""]
        for i, name in enumerate(self.STEPS):
            mark = "✅" if i in self.done else ("❌" if i == self.current else "⚪️")
            rows.append(te.emo(f"{mark} {name}"))
        rows += ["", te.emo(f"{icon} <b>{te.esc(error)}</b>"), ""]
        rows.append(te.emo("🔄 Fix it and send the step again — progress is kept."))
        try:
            await self.message.edit_text("\n".join(rows))
        except Exception:  # noqa: BLE001
            pass

    async def finish(self, title: str, name: str, premium: bool) -> None:
        self.done = {0, 1, 2}
        star = " ✨" if premium else ""
        rows = [
            te.emo(f"💎 <b>{te.esc(title)}</b>"),
            "",
            te.emo("✅ Phone  " + bar(1.0, 6)),
            te.emo("✅ Code   " + bar(1.0, 6)),
            te.emo("✅ Connect " + bar(1.0, 6)),
            "",
            te.emo(f"👑 <b>{te.esc(name)}{star}</b> is now the playback engine!"),
            "",
            te.emo("🎵 Add me to a group → /join → play something!"),
        ]
        try:
            await self.message.edit_text("\n".join(rows))
        except Exception:  # noqa: BLE001
            pass

    def reset(self) -> None:
        self.message = None
        self.done = set()
        self.current = 0


def _close(style: str) -> str:
    return {"<b>": "</b>", "<i>": "</i>", "<s>": "</s>"}.get(style, "")


class Spinner:
    """Edits one message through a loop of frames while a task runs.

    Usage:
        async with Spinner(chat, "Loading library") as s:
            ... work ...
        # final text set by the caller afterwards
    """

    FRAMES = ("🕒", "🕓", "🕔", "🕕")
    # spinner cycle uses clock faces as a smooth loader; the pack has ⏰/⏱

    def __init__(self, reply_func, title: str, interval: float = 1.2) -> None:
        self._reply = reply_func        # async callable(text) -> Message
        self.title = title
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.message = None

    def _frame(self, n: int) -> str:
        spin = self.FRAMES[n % len(self.FRAMES)]
        dots = "." * (1 + n % 3)
        return te.emo(f"{spin} <b>{te.esc(self.title)}{dots}</b>")

    async def _run(self) -> None:
        n = 0
        while not self._stop.is_set():
            frame = self._frame(n)
            try:
                if self.message is None:
                    self.message = await self._reply(frame)
                else:
                    await self.message.edit_text(frame)
            except Exception:  # noqa: BLE001
                await asyncio.sleep(self.interval)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
            n += 1

    async def __aenter__(self) -> "Spinner":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3)
            except Exception:  # noqa: BLE001
                self._task.cancel()
        self._task = None
