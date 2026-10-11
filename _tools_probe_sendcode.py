"""Probe: why does the login OTP never arrive?

Connects with the configured api_id/api_hash and tries send_code to a
dummy number. The exception type tells us exactly what's wrong:
- PhoneNumberInvalid  -> credentials are FINE (number format rejected)
- ApiIdInvalid        -> credentials are bad/blocked
- FloodWait           -> number or app is rate-limited (OTP blocked for X sec)
- NetworkError/timeout -> connection problem
"""
from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from pyrogram import Client


async def main() -> None:
    print(f"api_id={config.API_ID} api_hash_len={len(config.API_HASH)}")
    app = Client(
        "probe_login",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True,
    )
    print("connecting ...")
    await app.connect()
    print("connected OK")
    # Dummy Indian number in the exact international format.
    # We do NOT want a real code — PhoneNumberInvalid proves the pipeline works.
    try:
        sent = await app.send_code("+919000000000")
        print(f"send_code OK (unexpected for dummy): type={sent.type!r}")
        print("=> A REAL OTP WOULD ARRIVE. Credentials fine.")
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        msg = str(exc).replace("\n", " ")[:220]
        print(f"send_code -> {name}: {msg}")
        if name == "PhoneNumberInvalid":
            print("=> Credentials are GOOD; a real number would receive the OTP.")
        elif name == "ApiIdInvalid":
            print("=> API_ID/API_HASH is rejected -> OTP can never arrive. Get fresh keys.")
        elif name in ("FloodWait", "FloodWaitError"):
            print("=> Rate-limited: OTP is blocked for a while (see value).")
        else:
            print("=> Network / other issue — see type above.")
    try:
        await app.disconnect()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    asyncio.run(main())
