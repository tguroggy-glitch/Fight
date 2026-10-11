"""Playback engine: streams audio into Telegram group/channel voice chats.

Uses pytgcalls with a hand-built raw `Stream` whose microphone source is an
FFmpeg shell command. Doing it this way gives us exact control over the
filter chain for speed (atempo), EQ (equalizer), seek (-ss), and loop
(handled in Python on StreamEnded).

The Player is client-agnostic: it takes a PyTgCalls instance (which can be
backed by either the bot or the userbot) and a separate pyrogram Client for
downloading track files. In userbot mode the userbot does the actual
voice-chat joining and playback, exactly like the big music bots.
"""

from __future__ import annotations

import asyncio
import platform
import random
import shlex
import shutil
import subprocess
import time

import ntgcalls
from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import ChatUpdate
from pytgcalls.types import StreamEnded
from pytgcalls.types.calls.group_call_config import GroupCallConfig
from pytgcalls.types.raw import AudioParameters
from pytgcalls.types.raw import AudioStream
from pytgcalls.types.raw import Stream

import database as db
from config import AUTO_LEAVE_EMPTY

SILENCE_ARGS = [
    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
    "-f", "s16le", "-acodec", "pcm_s16le",
    "-ac", "2", "-ar", "48000", "pipe:1",
]


def _list_to_cmd(args: list[str]) -> str:
    """Mirror of pytgcalls.list_to_cmd (not exported in __all__)."""
    if platform.system() == "Windows":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def _atempo_chain(speed: float) -> str:
    """Build an atempo filter chain; a single atempo only accepts 0.5-2.0."""
    speed = min(max(speed, 0.1), 4.0)
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def _equalizer_chain(preset: str) -> str:
    """Build an FFmpeg equalizer filter chain for the given preset."""
    presets = {
        "flat": "",
        "bass": "equalizer=f=60:width_type=h:width=50:g=10",
        "treble": "equalizer=f=10000:width_type=h:width=50:g=8",
        "vocal": (
            "equalizer=f=3000:width_type=h:width=50:g=6,"
            "equalizer=f=100:width_type=h:width=50:g=-3"
        ),
        "rock": (
            "equalizer=f=60:width_type=h:width=50:g=6,"
            "equalizer=f=3000:width_type=h:width=50:g=4,"
            "equalizer=f=12000:width_type=h:width=50:g=3"
        ),
        "electronic": (
            "equalizer=f=40:width_type=h:width=50:g=8,"
            "equalizer=f=120:width_type=h:width=50:g=5,"
            "equalizer=f=3000:width_type=h:width=50:g=-3"
        ),
        "classical": (
            "equalizer=f=200:width_type=h:width=50:g=3,"
            "equalizer=f=4000:width_type=h:width=50:g=2"
        ),
        "pop": (
            "equalizer=f=60:width_type=h:width=50:g=4,"
            "equalizer=f=2000:width_type=h:width=50:g=3,"
            "equalizer=f=8000:width_type=h:width=50:g=2"
        ),
    }
    return presets.get(preset, "")


def ffmpeg_exe() -> str:
    """FFmpeg binary path: bundled ./ffmpeg_bin first, then the system PATH."""
    local = Path(__file__).resolve().parent / "ffmpeg_bin" / (
        "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    )
    if local.exists():
        return str(local)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError(
        "FFmpeg not found. Install it (winget install ffmpeg) or put "
        "ffmpeg.exe into the ffmpeg_bin folder next to the code."
    )


from pathlib import Path  # noqa: E402  (kept close to usage for readability)


def build_ffmpeg_command(
    path: str, speed: float = 1.0, seek: float = 0.0, eq_preset: str = ""
) -> str:
    """FFmpeg command that decodes `path` into raw PCM on stdout.

    Layout matches what ntgcalls expects for MediaSource.SHELL:
    a normal ffmpeg invocation printing raw s16le audio to stdout.
    """
    args: list[str] = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
    if seek > 0:
        args += ["-ss", f"{seek:.3f}"]

    # Build filter chain
    filters = [_atempo_chain(speed)]
    eq = _equalizer_chain(eq_preset)
    if eq:
        filters.append(eq)

    args += ["-i", path, "-vn", "-af", ",".join(filters)]
    args += [
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ac", "2",
        "-ar", "48000",
        "pipe:1",
    ]
    return _list_to_cmd(args)


def _row_get(row, key: str, default=None):
    """sqlite3.Row has no .get(); works for rows and dicts."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


class Player:
    """One instance per process; manages every joined voice chat.

    Parameters
    ----------
    tg_calls : PyTgCalls
        The pytgcalls instance that joins voice chats. Backed by either the
        bot or the userbot client.
    download_client : Client
        A pyrogram Client used only for downloading track files from Telegram.
        Can be the bot or the userbot.
    """

    def __init__(self, tg_calls: PyTgCalls, download_client: Client) -> None:
        self.tg_calls = tg_calls
        self.pyro = download_client
        self._locks: dict[int, asyncio.Lock] = {}
        self._volumes: dict[int, int] = {}
        self._equalizers: dict[int, str] = {}
        self._handlers_registered = False

    def rebind(self, tg_calls: PyTgCalls, download_client: Client) -> None:
        """Hot-swap the engine after an in-chat login (same process)."""
        self.tg_calls = tg_calls
        self.pyro = download_client
        self._volumes.clear()
        self._equalizers.clear()
        self._handlers_registered = False  # register on the new instance
        self.register_handlers()

    # ------------------------------------------------------------ helpers -----

    def _lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    @staticmethod
    def _stream_for(path: str, speed: float, seek: float, eq_preset: str = "") -> Stream:
        cmd = build_ffmpeg_command(path, speed=speed, seek=seek, eq_preset=eq_preset)
        return Stream(
            microphone=AudioStream(
                media_source=ntgcalls.MediaSource.SHELL,
                path=cmd,
                parameters=AudioParameters(48000, 2),
            ),
        )

    @staticmethod
    def _silence_stream() -> Stream:
        silence_cmd = _list_to_cmd(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", *SILENCE_ARGS]
        )
        return Stream(
            microphone=AudioStream(
                media_source=ntgcalls.MediaSource.SHELL,
                path=silence_cmd,
                parameters=AudioParameters(48000, 2),
            ),
        )

    def _call_config(self) -> GroupCallConfig:
        """auto_start=True only for user apps: bots can't start calls
        (Telegram answers BOT_METHOD_INVALID for phone.CreateGroupCall)."""
        from config import USE_USERBOT  # runtime value, updated after login

        return GroupCallConfig(auto_start=USE_USERBOT)

    def _apply_volume(self, chat_id: int) -> None:
        volume = self._volumes.get(chat_id, 100)
        if volume != 100:
            asyncio.ensure_future(self._change_volume_safe(chat_id, volume))

    async def _change_volume_safe(self, chat_id: int, volume: int) -> None:
        try:
            await self.tg_calls.change_volume_call(chat_id, volume)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _track_path(track_id: int) -> str:
        from handlers import MEDIA_DIR  # late import to avoid a cycle

        return str(MEDIA_DIR / f"track_{track_id}")

    async def _resolve_source(self, track_id: int) -> str | None:
        """Make sure the track file exists on disk; re-download if missing."""
        path = Path(self._track_path(track_id))
        if path.exists() and path.stat().st_size > 0:
            return str(path)
        track = db.get_track(track_id)
        if track is None:
            return None
        from handlers import download_track_anywhere  # late import: no cycle

        ok = await download_track_anywhere(
            track["chat_id"], track["message_id"], path
        )
        return str(path) if ok else None

    # ------------------------------------------------------------- events -----

    def register_handlers(self) -> None:
        """Idempotent: safe to call more than once (double registration would
        fire every StreamEnded twice and skip two tracks at once)."""
        if self._handlers_registered:
            return
        self._handlers_registered = True

        @self.tg_calls.on_update()
        async def _on_update(_, update) -> None:  # noqa: ANN001
            if isinstance(update, StreamEnded):
                asyncio.ensure_future(self._on_track_end(update.chat_id))
            elif isinstance(update, ChatUpdate) and (
                update.status & ChatUpdate.Status.LEFT_CALL
            ):
                asyncio.ensure_future(self._on_left_call(update.chat_id))

    async def _on_track_end(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            state = db.get_call_state(chat_id)
            finished = state["current_id"]
            if finished is None:
                return  # we ended the stream ourselves; nothing to advance
            loop = db.get_chat_setting(chat_id)
            mode = _row_get(loop, "loop_mode", "off")
            queue: list[int] = state["queue"]

            if mode == "one":
                nxt, pos = finished, 0.0
            else:
                if finished in queue:
                    queue.remove(finished)
                    if mode == "queue":
                        queue.append(finished)
                if queue:
                    nxt, pos = queue[0], 0.0
                else:
                    nxt, pos = None, 0.0

            db.save_call_state(chat_id, queue, nxt, pos, False)
            if nxt is None:
                if AUTO_LEAVE_EMPTY:
                    await self._leave(chat_id)
                return
            try:
                await self._start_stream(chat_id, nxt, pos)
            except FileNotFoundError:
                db.save_call_state(chat_id, queue, None, 0.0, False)

    async def _on_left_call(self, chat_id: int) -> None:
        db.set_joined(chat_id, False)
        db.clear_call_state(chat_id)
        self._volumes.pop(chat_id, None)
        self._equalizers.pop(chat_id, None)

    # -------------------------------------------------------------- API ------

    async def start(self) -> None:
        self.register_handlers()  # register once; no second start needed

    async def join(self, chat_id: int) -> str:
        """Join the voice chat (streams silence until something plays)."""
        async with self._lock(chat_id):
            state = db.get_call_state(chat_id)
            if state["current_id"] is not None:
                # Already streaming a track: re-joining with the silence stream
                # would cut the audio off mid-playback.
                return "Already in the voice chat with playback running."
            await self._join_call(chat_id)
            db.set_joined(chat_id, True)
            return "Joined the voice chat."

    async def _join_call(self, chat_id: int) -> None:
        await self.tg_calls.play(chat_id, self._silence_stream(), self._call_config())

    async def leave(self, chat_id: int) -> str:
        async with self._lock(chat_id):
            await self._leave(chat_id)
            return "Left the voice chat."

    async def _leave(self, chat_id: int) -> None:
        try:
            await self.tg_calls.leave_call(chat_id)
        except Exception:  # noqa: BLE001
            pass
        db.set_joined(chat_id, False)
        db.clear_call_state(chat_id)
        self._volumes.pop(chat_id, None)

    async def play(self, chat_id: int, track_id: int, position: float = 0.0) -> str:
        """Play a track now: replaces whatever is playing and resets the queue."""
        async with self._lock(chat_id):
            await self._join_call(chat_id)
            db.set_joined(chat_id, True)
            db.save_call_state(chat_id, [track_id], track_id, position, False)
            await self._start_stream(chat_id, track_id, position)
            return "Playing."

    async def queue_add(self, chat_id: int, track_id: int) -> str:
        async with self._lock(chat_id):
            state = db.get_call_state(chat_id)
            queue: list[int] = state["queue"]
            queue.append(track_id)
            if state["current_id"] is None:
                await self._join_call(chat_id)
                db.set_joined(chat_id, True)
                db.save_call_state(chat_id, [], track_id, 0.0, False)
                await self._start_stream(chat_id, track_id, 0.0)
                return "Playing now."
            db.save_call_state(chat_id, queue, state["current_id"],
                               state["position"], state["paused"])
            return f"Added to queue (position {len(queue)})."

    async def _start_stream(self, chat_id: int, track_id: int, position: float) -> None:
        path = await self._resolve_source(track_id)
        if path is None:
            db.save_call_state(chat_id, [], None, 0.0, False)
            raise FileNotFoundError(f"track {track_id} unavailable")
        setting = db.get_chat_setting(chat_id)
        speed = _row_get(setting, "speed", 1.0) or 1.0
        eq_preset = self._equalizers.get(chat_id) or _row_get(setting, "equalizer", "flat") or "flat"
        self._equalizers[chat_id] = eq_preset
        stream = self._stream_for(path, speed, position, eq_preset)
        await self.tg_calls.play(chat_id, stream)
        self._apply_volume(chat_id)

    async def pause(self, chat_id: int) -> str:
        async with self._lock(chat_id):
            await self.tg_calls.pause(chat_id)
            state = db.get_call_state(chat_id)
            db.save_call_state(chat_id, state["queue"], state["current_id"],
                               state["position"], True)
            return "Paused."

    async def resume(self, chat_id: int) -> str:
        async with self._lock(chat_id):
            await self.tg_calls.resume(chat_id)
            state = db.get_call_state(chat_id)
            db.save_call_state(chat_id, state["queue"], state["current_id"],
                               state["position"], False)
            return "Resumed."

    async def _skip_locked(self, chat_id: int) -> str:
        state = db.get_call_state(chat_id)
        current = state["current_id"]
        if current is None:
            return "Nothing is playing."
        loop = db.get_chat_setting(chat_id)
        mode = _row_get(loop, "loop_mode", "off")
        queue: list[int] = state["queue"]
        if current in queue:
            queue.remove(current)
            if mode == "queue":
                queue.append(current)
        if not queue:
            db.save_call_state(chat_id, [], None, 0.0, False)
            await self._leave(chat_id)
            return "Queue finished — left the voice chat."
        nxt = queue[0]
        db.save_call_state(chat_id, queue, nxt, 0.0, False)
        await self._start_stream(chat_id, nxt, 0.0)
        return "Skipped."

    async def skip_now(self, chat_id: int) -> str:
        async with self._lock(chat_id):
            return await self._skip_locked(chat_id)

    async def play_random(self, chat_id: int, first_track_id: int | None = None) -> str:
        """Shuffle the whole library into the queue and start playing.

        ``first_track_id`` (optional) is played right now and the rest of the
        library is queued after it in random order.
        """
        tracks = [t["id"] for t in db.list_tracks()]
        if not tracks:
            return "Library is empty — upload some tracks first."
        async with self._lock(chat_id):
            if first_track_id is not None and first_track_id in tracks:
                tracks.remove(first_track_id)
                random.shuffle(tracks)
                queue = [first_track_id, *tracks]
                start = first_track_id
            else:
                random.shuffle(tracks)
                queue = list(tracks)
                start = queue.pop(0)
            await self._join_call(chat_id)
            db.set_joined(chat_id, True)
            db.save_call_state(chat_id, queue, start, 0.0, False)
            await self._start_stream(chat_id, start, 0.0)
            return f"Shuffled {len(queue) + 1} track(s) — playing now."

    async def stop(self, chat_id: int) -> str:
        async with self._lock(chat_id):
            db.clear_call_state(chat_id)
            await self._leave(chat_id)
            return "Stopped and left the voice chat."

    async def set_speed(self, chat_id: int, speed: float) -> str:
        async with self._lock(chat_id):
            state = db.get_call_state(chat_id)
            if state["current_id"] is not None:
                setting = db.get_chat_setting(chat_id)
                old_speed = _row_get(setting, "speed", 1.0) or 1.0
                raw = await self._raw_time(chat_id)
                elapsed = raw * old_speed if raw is not None else state["position"]
                db.set_speed(chat_id, speed)
                await self._start_stream(chat_id, state["current_id"], elapsed)
                return f"Speed set to {speed:g}x."
            db.set_speed(chat_id, speed)
            return f"Speed set to {speed:g}x (applies to the next track)."

    async def set_volume(self, chat_id: int, volume: int) -> str:
        self._volumes[chat_id] = volume
        db.set_volume(chat_id, volume)
        await self._change_volume_safe(chat_id, volume)
        return f"Volume set to {volume}%."

    async def set_equalizer(self, chat_id: int, preset: str) -> str:
        valid = {"flat", "bass", "treble", "vocal", "rock",
                 "electronic", "classical", "pop"}
        if preset not in valid:
            return f"Invalid preset. Use: {', '.join(sorted(valid))}"

        async with self._lock(chat_id):
            state = db.get_call_state(chat_id)
            self._equalizers[chat_id] = preset
            db.set_equalizer(chat_id, preset)
            if state["current_id"] is not None:
                setting = db.get_chat_setting(chat_id)
                speed = _row_get(setting, "speed", 1.0) or 1.0
                raw = await self._raw_time(chat_id)
                elapsed = raw * speed if raw is not None else state["position"]
                await self._start_stream(chat_id, state["current_id"], elapsed)
                return f"Equalizer: {preset} applied."
            return f"Equalizer: {preset} (applies to next track)."

    async def set_loop(self, chat_id: int, mode: str) -> str:
        db.set_loop(chat_id, mode)
        label = {
            "off": "off — queue plays once",
            "one": "one — repeating current track",
            "queue": "queue — cycling the whole queue",
        }[mode]
        return f"Loop mode: {label}."

    async def seek(self, chat_id: int, seconds: float) -> str:
        async with self._lock(chat_id):
            state = db.get_call_state(chat_id)
            if state["current_id"] is None:
                return "Nothing is playing."
            track = db.get_track(state["current_id"])
            duration = track["duration"] if track else 0
            if duration and seconds > duration:
                seconds = max(duration - 1, 0)
            db.save_call_state(chat_id, state["queue"], state["current_id"],
                               seconds, False)
            await self._start_stream(chat_id, state["current_id"], seconds)
            return f"Seeked to {int(seconds)}s."

    async def _raw_time(self, chat_id: int) -> float | None:
        """Raw call clock: real seconds elapsed in the current stream."""
        try:
            raw = await self.tg_calls.time(chat_id)
        except Exception:  # noqa: BLE001
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def _elapsed(self, chat_id: int) -> float | None:
        """Position inside the ORIGINAL media, corrected for playback speed.

        The FFmpeg atempo filter consumes the media faster/slower, so the call
        clock counts stream seconds while the media position is stream * speed.
        """
        raw = await self._raw_time(chat_id)
        if raw is None:
            return None
        setting = db.get_chat_setting(chat_id)
        speed = _row_get(setting, "speed", 1.0) or 1.0
        return raw * speed

    # -------------------------------------------------------- persistence -----

    async def restore_sessions(self) -> None:
        """Re-join chats that had an active call before the last restart."""
        for chat in db.list_chats(only_joined=True):
            chat_id = chat["chat_id"]
            state = db.get_call_state(chat_id)
            if state["current_id"] is None and not state["queue"]:
                continue
            title = _row_get(chat, "title", "") or str(chat_id)
            try:
                async with self._lock(chat_id):
                    await self._join_call(chat_id)
                    db.set_joined(chat_id, True)
                    queue: list[int] = state["queue"]
                    if queue and state["current_id"] is not None:
                        await self._start_stream(
                            chat_id, state["current_id"], state["position"]
                        )
                        if state["paused"]:
                            try:
                                await self.tg_calls.pause(chat_id)
                            except Exception:  # noqa: BLE001
                                pass
                    setting = db.get_chat_setting(chat_id)
                    volume = _row_get(setting, "volume", 100)
                    if volume != 100:
                        self._volumes[chat_id] = volume
                        await self._change_volume_safe(chat_id, volume)
                    eq = _row_get(setting, "equalizer", "flat")
                    if eq and eq != "flat":
                        self._equalizers[chat_id] = eq
                print(f"[restore] resumed call in {title}")
            except Exception as exc:  # noqa: BLE001
                print(f"[restore] could not resume {title} ({chat_id}): {exc}")

    # ----------------------------------------------------------- status -------

    async def status_text(self, chat_id: int) -> str:
        setting = db.get_chat_setting(chat_id)
        state = db.get_call_state(chat_id)
        if state["current_id"] is None:
            return "Nothing playing."
        track = db.get_track(state["current_id"])
        title = track["title"] if track else f"track {state['current_id']}"
        try:
            pos = await self._elapsed(chat_id) or 0.0
        except Exception:  # noqa: BLE001
            pos = 0.0
        duration = track["duration"] if track else 0
        paused = " ⏸" if state["paused"] else ""
        loop = _row_get(setting, "loop_mode", "off")
        speed = _row_get(setting, "speed", 1.0) or 1.0
        volume = _row_get(setting, "volume", 100)
        eq = _row_get(setting, "equalizer", "flat")
        queue = state["queue"]
        eq_label = f" | 🎚 EQ: {eq}" if eq != "flat" else ""
        return (
            f"🎧 Now playing{paused}: {title}\n"
            f"⏱ {int(pos)}s / {duration or '?'}s\n"
            f"🔁 loop: {loop} | 🚀 speed: {speed:g}x | 🔊 volume: {volume}%{eq_label}\n"
            f"📜 Queue: {len(queue)} track(s)"
        )


start_time = time.time()
