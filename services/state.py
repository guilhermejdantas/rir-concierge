"""Ephemeral state store with a Redis backend and an in-process fallback.

The concierge keeps three kinds of short-lived state: the show an alert is
currently open for, per-event alert dedupe locks, and per-event skip markers.

* **Redis** (``REDIS_URL`` set and reachable) — required if more than one
  instance of the app runs, so the ``SET NX`` alert lock is shared.
* **In-process** (:class:`InMemoryStore`) — used when ``REDIS_URL`` is empty or
  the server is unreachable. Fine for a single-instance deployment such as
  Cloud Run with ``--min-instances=1 --max-instances=1``. State is lost on a
  cold start, which at worst means one duplicate alert after a restart.

Both backends expose the tiny async subset the app uses: ``get``, ``set``
(with ``nx`` / ``ex``), ``delete``, ``ping``, ``aclose``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class StateStore(Protocol):
    """Structural type for the state backends (Redis client satisfies this)."""

    async def get(self, key: str) -> Optional[str]: ...

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: Optional[int] = None,
        nx: bool = False,
    ) -> Optional[bool]: ...

    async def delete(self, *keys: str) -> int: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


class InMemoryStore:
    """Process-local dict with TTL and ``SET NX`` semantics."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, Optional[float]]] = {}
        self._lock = asyncio.Lock()

    def _live(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            return self._live(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: Optional[int] = None,
        nx: bool = False,
    ) -> Optional[bool]:
        async with self._lock:
            if nx and self._live(key) is not None:
                return None
            expires_at = time.monotonic() + ex if ex else None
            self._data[key] = (value, expires_at)
            return True

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            removed = 0
            for key in keys:
                if self._data.pop(key, None) is not None:
                    removed += 1
            return removed

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self._data.clear()


async def open_state_store(redis_url: str) -> StateStore:
    """Return a connected Redis client, or an :class:`InMemoryStore` fallback."""
    if not redis_url:
        logger.warning("REDIS_URL empty; using in-process state store.")
        return InMemoryStore()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            redis_url, encoding="utf-8", decode_responses=True
        )
        if not await client.ping():
            raise ConnectionError("PING returned falsy")
        logger.info("Connected to Redis state store.")
        return client
    except Exception as exc:  # noqa: BLE001 - degrade rather than crash
        logger.warning(
            "Redis unavailable (%s); falling back to in-process state store.", exc
        )
        return InMemoryStore()
