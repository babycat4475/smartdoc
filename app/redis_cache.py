"""Redis cache backend (branch: postgres-redis).

Drop-in for the cache portion of Store: same cache_get/cache_put interface.
TTL support is the reason to prefer Redis over the SQLite cache table in
multi-replica deployments.
"""
from __future__ import annotations

import json
from typing import Optional

import redis


class RedisCache:
    def __init__(self, url: str = "redis://localhost:6379/0", ttl_s: int = 86_400, client=None):
        self.r = client or redis.Redis.from_url(url, decode_responses=True)
        self.ttl_s = ttl_s

    def cache_get(self, key: str) -> Optional[dict]:
        raw = self.r.get(key)
        return json.loads(raw) if raw else None

    def cache_put(self, key: str, payload: dict) -> None:
        self.r.set(key, json.dumps(payload, ensure_ascii=False), ex=self.ttl_s)


class CachedStore:
    """Compose: documents/events -> underlying store; cache -> Redis."""

    def __init__(self, base, cache: RedisCache):
        self._base = base
        self._cache = cache

    def cache_get(self, key):
        return self._cache.cache_get(key)

    def cache_put(self, key, payload):
        self._cache.cache_put(key, payload)

    def __getattr__(self, name):
        return getattr(self._base, name)
