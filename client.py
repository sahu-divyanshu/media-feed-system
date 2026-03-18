"""
client.py — Performance Demonstration (Phase 5)
================================================
Proves that SADD deduplicates across multiple messages from the same sender.

Steps:
  1. Send 10 messages from user_A → user_B
  2. Send  5 messages from user_C → user_B
  3. GET unread count for user_B  → must return 2 (not 15)
  4. POST read-all for user_B     → reset
  5. GET unread count again        → must return 0
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [client]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("unread.client")

BASE_URL = "http://localhost:8000"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
async def send_message(client: httpx.AsyncClient, sender: str, recipient: str) -> dict:
    resp = await client.post(
        f"{BASE_URL}/messages/send",
        json={"sender_id": sender, "recipient_id": recipient},
    )
    resp.raise_for_status()
    data = resp.json()
    log.info(
        "  SADD %s %s → was_new_sender=%s",
        data["key"], sender, data["was_new_sender"],
    )
    return data


async def get_unread(client: httpx.AsyncClient, user_id: str) -> int:
    resp = await client.get(f"{BASE_URL}/messages/unread/{user_id}")
    resp.raise_for_status()
    data = resp.json()
    log.info(
        "  SCARD unread:%s → %d unique sender(s)  [complexity=%s]",
        user_id, data["unread_count"], data["complexity"],
    )
    return data["unread_count"]


async def read_all(client: httpx.AsyncClient, user_id: str) -> bool:
    resp = await client.post(
        f"{BASE_URL}/messages/read-all",
        json={"user_id": user_id},
    )
    resp.raise_for_status()
    data = resp.json()
    log.info("  DEL unread:%s → cleared=%s", user_id, data["cleared"])
    return data["cleared"]


async def debug_set(client: httpx.AsyncClient, user_id: str) -> list[str]:
    resp = await client.get(f"{BASE_URL}/debug/unread/{user_id}")
    resp.raise_for_status()
    data = resp.json()
    log.info("  SMEMBERS unread:%s → %s", user_id, data["members"])
    return data["members"]


def _section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def _assert(condition: bool, message: str) -> None:
    if condition:
        print(f"  ✅  PASS — {message}")
    else:
        print(f"  ❌  FAIL — {message}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Main demo
# ─────────────────────────────────────────────────────────────────────────────
async def main() -> None:
    print("\n" + "═" * 55)
    print("  Unread Message Indicator — Demo Client")
    print("═" * 55)

    async with httpx.AsyncClient(timeout=10.0) as client:

        # ── Ensure clean state ────────────────────────────────────────────────
        _section("0. Reset state before demo")
        await read_all(client, "user_B")
        count = await get_unread(client, "user_B")
        _assert(count == 0, f"user_B starts at 0 unread (got {count})")

        # ── Step 1: 10 messages from user_A → user_B ──────────────────────────
        _section("1. Send 10 messages from user_A → user_B")
        log.info("Sending 10 messages (all from user_A)...")

        tasks = [send_message(client, "user_A", "user_B") for _ in range(10)]
        results = await asyncio.gather(*tasks)

        new_sender_hits = sum(1 for r in results if r["was_new_sender"])
        log.info("was_new_sender=True on %d / 10 calls", new_sender_hits)
        _assert(
            new_sender_hits == 1,
            f"Only 1 of 10 SADD calls added user_A (rest were no-ops) — got {new_sender_hits}",
        )

        # ── Step 2: 5 messages from user_C → user_B ───────────────────────────
        _section("2. Send 5 messages from user_C → user_B")
        log.info("Sending 5 messages (all from user_C)...")

        tasks = [send_message(client, "user_C", "user_B") for _ in range(5)]
        results = await asyncio.gather(*tasks)

        new_sender_hits = sum(1 for r in results if r["was_new_sender"])
        _assert(
            new_sender_hits == 1,
            f"Only 1 of 5 SADD calls added user_C (rest were no-ops) — got {new_sender_hits}",
        )

        # ── Step 3: Verify count = 2 (not 15) ────────────────────────────────
        _section("3. GET unread count — must be 2, not 15")
        members = await debug_set(client, "user_B")
        count = await get_unread(client, "user_B")

        print(f"\n  Total messages sent : 15  (10 from user_A + 5 from user_C)")
        print(f"  Unique senders      : {count}  (user_A, user_C)")
        print(f"  Set members         : {members}")

        _assert(
            count == 2,
            f"SCARD = 2 unique senders despite 15 total messages (got {count})",
        )
        _assert(
            set(members) == {"user_A", "user_C"},
            f"Set contains exactly {{user_A, user_C}} (got {set(members)})",
        )

        # ── Step 4: Reset ─────────────────────────────────────────────────────
        _section("4. POST /messages/read-all → reset user_B")
        cleared = await read_all(client, "user_B")
        _assert(cleared, "DEL returned 1 (key existed and was deleted)")

        # ── Step 5: Verify count = 0 ──────────────────────────────────────────
        _section("5. GET unread count — must be 0 after reset")
        count = await get_unread(client, "user_B")
        _assert(count == 0, f"SCARD = 0 after DEL (got {count})")

        # ── Summary ───────────────────────────────────────────────────────────
        print("\n" + "═" * 55)
        print("  All assertions passed.")
        print("  The Set architecture correctly collapses 15 messages")
        print("  from 2 senders into a count of 2, retrieved in O(1).")
        print("═" * 55 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
