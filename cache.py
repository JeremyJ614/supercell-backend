"""
cache.py — Simple in-memory TTL cache.
Keeps radar volumes cached for 5 minutes so repeated requests
don't re-download 10 MB NEXRAD files from S3.
Cloud Run instances are stateless, so this is per-instance only —
good enough for free tier usage patterns.
"""

import time
from typing import Any, Optional

_store: dict[str, tuple[Any, float]] = {}


def cache_get(key: str) -> Optional[Any]:
    """Return cached value if it exists and hasn't expired."""
    entry = _store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        del _store[key]
        return None
    return value


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Store a value with a TTL in seconds."""
    _store[key] = (value, time.time() + ttl)


def cache_delete(key: str) -> None:
    _store.pop(key, None)


def cache_clear() -> None:
    _store.clear()


def cache_stats() -> dict:
    now = time.time()
    total = len(_store)
    expired = sum(1 for _, (_, exp) in _store.items() if now > exp)
    return {"total_keys": total, "expired_keys": expired, "live_keys": total - expired}
