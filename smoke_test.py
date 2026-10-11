"""Offline smoke test: config, database, ffmpeg command building, panels.

Run without any Telegram credentials:
    dummy env vars -> python smoke_test.py

Exits non-zero if anything fails.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Windows consoles default to cp1252; make emoji printing safe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Provide dummy credentials BEFORE importing config/database so the modules load.
DUMMY = {
    "API_ID": "123456",
    "API_HASH": "dummy_hash_for_tests",
    "BOT_TOKEN": "123456:dummy-token-for-tests",
    "OWNER_ID": "42",
    "CONTACT_USERNAME": "@test_owner",
    "DB_PATH": "",  # filled below per-run
    "DOWNLOAD_DIR": "",  # filled below per-run
}


def _prepare_env(tmp: Path) -> None:
    for key, value in DUMMY.items():
        if value:
            os.environ[key] = value
    os.environ["DB_PATH"] = str(tmp / "test.db")
    os.environ["DOWNLOAD_DIR"] = str(tmp / "downloads")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vcbot_smoke_"))
    _prepare_env(tmp)

    failures: list[str] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            print(f"  ok  {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
            print(f"FAIL  {name}: {exc}")

    # ---------------------------------------------------------------- config
    import config

    def test_config():
        assert config.API_ID == 123456
        assert config.OWNER_ID == 42
        assert config.CONTACT_USERNAME == "@test_owner"
        assert config.PAYWALL_TEXT.count("@test_owner") == 1

    check("config loads & validates", test_config)

    # ------------------------------------------------------------------- db
    import database as db

    db.init(tmp / "test.db")

    def test_admins():
        assert db.add_admin(1001, "Alice") is True
        assert db.add_admin(1001, "Alice") is False  # duplicate
        assert db.is_admin(1001) is True
        assert db.is_admin(9999) is False
        assert db.remove_admin(1001) is True
        assert db.is_admin(1001) is False

    def test_subs():
        expires = db.grant_sub(2002, 7, "Bob")
        assert expires > __import__("time").time()
        assert db.is_subscribed(2002) is True
        # extension, not replacement
        expires2 = db.grant_sub(2002, 7)
        assert expires2 - expires == 7 * 86400
        # revoke
        assert db.revoke_sub(2002) is True
        assert db.is_subscribed(2002) is False
        # expired sub is invisible
        db.grant_sub(3003, -1)  # negative days -> already expired
        assert db.is_subscribed(3003) is False

    def test_tracks():
        tid = db.add_track("Test Song", -100123, 10, 180)
        assert db.get_track(tid)["title"] == "Test Song"
        db.rename_track(tid, "Renamed")
        assert db.get_track(tid)["title"] == "Renamed"
        assert db.count_tracks() == 1
        # queue cleanup on delete
        db.save_call_state(-100123, [tid, 999], tid)
        assert db.delete_track(tid) is True
        state = db.get_call_state(-100123)
        assert state["queue"] == [999]

    def test_chat_settings():
        db.set_loop(-100123, "queue")
        db.set_speed(-100123, 1.5)
        db.set_volume(-100123, 150)
        row = db.get_chat_setting(-100123)
        assert row["loop_mode"] == "queue"
        assert row["speed"] == 1.5
        assert row["volume"] == 150
        assert len(db.list_chats()) == 1

    def test_call_state_roundtrip():
        db.save_call_state(-100123, [5, 6, 7], 5, 12.5, True)
        state = db.get_call_state(-100123)
        assert state == {"queue": [5, 6, 7], "current_id": 5,
                         "position": 12.5, "paused": True}
        db.clear_call_state(-100123)
        assert db.get_call_state(-100123)["current_id"] is None

    check("database admins", test_admins)
    check("database subscriptions", test_subs)
    check("database tracks + queue cleanup", test_tracks)
    check("database chat settings", test_chat_settings)
    check("database call state", test_call_state_roundtrip)

    # --------------------------------------------------------------- player
    from player import _atempo_chain
    from player import build_ffmpeg_command

    def test_atempo():
        assert _atempo_chain(1.0) == "atempo=1.000000"
        assert _atempo_chain(0.5) == "atempo=0.500000"
        assert _atempo_chain(2.0) == "atempo=2.000000"
        # 0.25 needs two chained filters (0.5 * 0.5)
        assert _atempo_chain(0.25).count("atempo=") == 2
        # 4.0 needs two chained filters (2.0 * 2.0)
        assert _atempo_chain(4.0).count("atempo=") == 2

    def test_ffmpeg_command():
        cmd = build_ffmpeg_command("song.mp3", speed=1.5, seek=30)
        assert "song.mp3" in cmd
        assert "-ss 30.000" in cmd
        assert "atempo=1.500000" in cmd
        assert "s16le" in cmd
        assert "-ac 2" in cmd
        assert "-ar 48000" in cmd
        assert cmd.rstrip().endswith("pipe:1")

    check("atempo chain", test_atempo)
    check("ffmpeg command builder", test_ffmpeg_command)

    def test_ffmpeg_exe():
        from player import ffmpeg_exe

        resolved = ffmpeg_exe()
        assert "ffmpeg" in resolved, resolved

    check("ffmpeg binary resolves (bundled or PATH)", test_ffmpeg_exe)

    # ------------------------------------------------------------- tgemoji
    import premium_emoji
    import tgemoji as te

    def test_tgemoji():
        assert len(premium_emoji.PREMIUM_EMOJI) > 50
        out = te.emo("✅ done")
        assert '<tg-emoji emoji-id="' in out
        assert "✅" in out
        assert te.esc("<b> & x") == "&lt;b&gt; &amp; x"
        assert te.emo("plain text only") == "plain text only"

    check("premium emoji formatter", test_tgemoji)

    # -------------------------------------------------------------- panels
    import panels

    def test_panels():
        tid = db.add_track("Panel Song", -100123, 11, 95)
        db.upsert_chat(-100123, "Test Chat")
        db.set_joined(-100123, True)
        assert panels.main_menu().inline_keyboard
        assert panels.library_page(0).inline_keyboard
        assert panels.track_view(tid).inline_keyboard
        assert panels.chat_pick(tid, "play", 0).inline_keyboard
        assert panels.track_controls(-100123).inline_keyboard
        assert panels.admins_menu().inline_keyboard
        assert panels.subs_menu().inline_keyboard
        assert panels.chats_menu().inline_keyboard
        assert panels.speed_menu(-100123).inline_keyboard

    check("inline panels build", test_panels)

    # -------------------------------------------------------- handler units
    from handlers import _extract_audio  # noqa: PLC0415

    class _FakeAudio:
        title = "Song"
        file_name = "song.mp3"
        file_size = 1000
        duration = 10

    class _FakeDoc:
        mime_type = "text/plain"
        file_name = "notes.txt"
        file_size = 10
        duration = 0

    class _FakeMsg:
        audio = _FakeAudio()
        voice = None
        document = None
        caption = None

    class _FakeMsgDoc:
        audio = None
        voice = None
        document = _FakeDoc()
        caption = None

    def test_extract():
        assert _extract_audio(_FakeMsg()) is not None
        assert _extract_audio(_FakeMsgDoc()) is None  # txt file rejected

    check("audio extraction filter", test_extract)

    # ---------------------------------------------------------------- done
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        return 1
    print("\nAll checks passed ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
