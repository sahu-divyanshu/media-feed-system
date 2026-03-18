"""
main.py — Unread Message Indicator API
=======================================

Architecture: Redis Set per recipient
  Key schema : unread:{recipient_id}
  Value      : Set of sender_ids who have unread messages

Why a Set, not a counter?
─────────────────────────
The naive approach — storing an integer count — breaks under this constraint:
"10 messages from the same sender counts as 1 unread conversation, not 10."

A Redis Set solves this natively:
  SADD unread:bob alice   → adds "alice" to bob's unread set (returns 1)
  SADD unread:bob alice   → "alice" already in set, no-op   (returns 0)
  SADD unread:bob carol   → adds "carol"                    (returns 1)
  SCARD unread:bob        → 2 (unique senders)              O(1)

SADD deduplicates by the mathematical definition of a Set: each element
appears at most once. It doesn't matter how many messages alice sends —
she occupies exactly one slot in bob's unread set. This is not application
logic; it is enforced by the data structure itself, atomically, at the
Redis engine level.

SCARD is O(1) because Redis stores the set's cardinality (member count)
as metadata alongside the set. Retrieving it requires no traversal —
it is a direct metadata read, identical in cost whether the set has
1 sender or 1,000,000 senders.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from redis.asyncio import Redis, ConnectionPool

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("unread")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
VALKEY_URL = "redis://localhost:6380/0"


def _unread_key(user_id: str) -> str:
    """Canonical key for a user's unread-sender Set."""
    return f"unread:{user_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Connection pool — module-level singleton, initialised in lifespan
# ─────────────────────────────────────────────────────────────────────────────
_pool: ConnectionPool | None = None


def get_redis() -> Redis:
    """
    FastAPI dependency — yields a Redis client backed by the shared pool.
    The pool manages connection reuse; each request borrows a connection
    and returns it automatically when the dependency scope exits.
    """
    if _pool is None:
        raise RuntimeError("Redis pool not initialised — check lifespan.")
    return Redis(connection_pool=_pool)


RedisDep = Annotated[Redis, Depends(get_redis)]


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────
class SendMessageRequest(BaseModel):
    sender_id:    str = Field(..., min_length=1, examples=["user_A"])
    recipient_id: str = Field(..., min_length=1, examples=["user_B"])


class SendMessageResponse(BaseModel):
    sender_id:    str
    recipient_id: str
    key:          str
    was_new_sender: bool   # True if this sender was not already in the set


class UnreadCountResponse(BaseModel):
    user_id:       str
    unread_count:  int     # SCARD result — number of unique senders
    complexity:    str = "O(1)"


class ReadAllRequest(BaseModel):
    user_id: str = Field(..., min_length=1, examples=["user_B"])


class ReadAllResponse(BaseModel):
    user_id:  str
    key:      str
    cleared:  bool   # True if the key existed and was deleted


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan — pool init + teardown
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool

    log.info("Initialising Valkey connection pool → %s", VALKEY_URL)
    _pool = ConnectionPool.from_url(
        VALKEY_URL,
        max_connections=50,
        decode_responses=True,
    )

    # Verify connectivity before accepting traffic
    probe = Redis(connection_pool=_pool)
    try:
        pong = await probe.ping()
        log.info("Valkey reachable — PING → %s", pong)
    except Exception as exc:
        log.critical("Cannot reach Valkey: %s", exc)
        raise
    finally:
        await probe.aclose()

    log.info("Unread Indicator API ready.")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Closing Valkey connection pool...")
    await _pool.disconnect()
    log.info("Pool closed.")


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Unread Message Indicator",
    description="O(1) unread-sender counts using Redis Sets (SADD / SCARD / DEL)",
    version="1.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — POST /messages/send
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/messages/send",
    response_model=SendMessageResponse,
    status_code=202,
    summary="Record that sender has an unread message for recipient",
)
async def send_message(body: SendMessageRequest, redis: RedisDep):
    """
    SADD unread:{recipient_id}  {sender_id}

    SADD semantics:
      • Returns 1 if sender_id was not already in the set  (new sender)
      • Returns 0 if sender_id was already present         (no-op, deduplicated)

    Result: no matter how many messages sender_A sends to user_B,
    user_B's unread set contains exactly one entry for sender_A.
    """
    key = _unread_key(body.recipient_id)

    log.info(
        "SADD %s %s  [sender=%s recipient=%s]",
        key, body.sender_id, body.sender_id, body.recipient_id,
    )

    added: int = await redis.sadd(key, body.sender_id)  # type: ignore[arg-type]
    was_new = bool(added)

    if was_new:
        log.info("→ New sender added to set  | key=%s sender=%s", key, body.sender_id)
    else:
        log.info("→ Sender already in set (deduplicated) | key=%s sender=%s",
                 key, body.sender_id)

    return SendMessageResponse(
        sender_id=body.sender_id,
        recipient_id=body.recipient_id,
        key=key,
        was_new_sender=was_new,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — GET /messages/unread/{user_id}
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/messages/unread/{user_id}",
    response_model=UnreadCountResponse,
    summary="Get count of unique unread senders (O(1))",
)
async def get_unread_count(user_id: str, redis: RedisDep):
    """
    SCARD unread:{user_id}

    Time complexity: O(1)
    Redis stores the cardinality (member count) of every Set as metadata.
    SCARD reads that metadata field directly — it does not traverse the set.
    The response time is identical whether the set has 1 member or 1,000,000.

    Returns 0 for users with no unread messages (key doesn't exist).
    """
    key = _unread_key(user_id)

    log.info("SCARD %s  [user=%s]", key, user_id)

    count: int = await redis.scard(key)  # type: ignore[assignment]

    log.info("→ SCARD result: %d unique senders | key=%s", count, key)

    return UnreadCountResponse(user_id=user_id, unread_count=count)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — POST /messages/read-all
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/messages/read-all",
    response_model=ReadAllResponse,
    summary="Mark all messages as read (delete the unread set)",
)
async def read_all(body: ReadAllRequest, redis: RedisDep):
    """
    DEL unread:{user_id}

    Deletes the entire set in one atomic operation.
    DEL returns the number of keys deleted: 1 if the key existed, 0 if not.
    Using DEL (hard delete) rather than setting count to zero ensures we do
    not leave an empty key consuming memory.
    """
    key = _unread_key(body.user_id)

    log.info("DEL %s  [user=%s]", key, body.user_id)

    deleted: int = await redis.delete(key)
    cleared = bool(deleted)

    if cleared:
        log.info("→ Key deleted — all unread messages cleared | key=%s", key)
    else:
        log.info("→ Key did not exist (already empty) | key=%s", key)

    return ReadAllResponse(user_id=body.user_id, key=key, cleared=cleared)


# ─────────────────────────────────────────────────────────────────────────────
# GET /debug/{user_id} — inspect raw set members (dev / debug only)
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/debug/unread/{user_id}",
    summary="[Debug] Return raw set members for a user's unread key",
)
async def debug_unread(user_id: str, redis: RedisDep):
    key = _unread_key(user_id)
    members: set[str] = await redis.smembers(key)  # type: ignore[assignment]
    log.info("SMEMBERS %s → %s", key, members)
    return {"key": key, "members": sorted(members), "count": len(members)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /health
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health(redis: RedisDep):
    try:
        await redis.ping()
        valkey_status = "ok"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Valkey unreachable: {exc}")

    info = await redis.info("server")
    return {
        "status":         "ok",
        "valkey_version": info.get("redis_version"),
        "valkey_mode":    info.get("redis_mode"),
    }
