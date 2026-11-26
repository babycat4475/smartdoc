"""Tests for the postgres-redis branch.

Redis path: tested with fakeredis (real protocol semantics, no server).
PgStore path: interface-parity test skipped unless SMARTDOC_TEST_PG_DSN is set;
SQL portability is statically checked (no SQLite-only syntax).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import fakeredis
import pytest

from app.config import Settings
from app.redis_cache import CachedStore, RedisCache
from app.service import SmartDocService
from app.store import Store


def test_redis_cache_roundtrip():
    rc = RedisCache(client=fakeredis.FakeRedis(decode_responses=True))
    assert rc.cache_get("k") is None
    rc.cache_put("k", {"category": "invoice", "confidence": 0.9, "engine": "llm"})
    assert rc.cache_get("k")["category"] == "invoice"


def test_redis_ttl_set():
    fake = fakeredis.FakeRedis(decode_responses=True)
    rc = RedisCache(ttl_s=3600, client=fake)
    rc.cache_put("k", {"a": 1})
    assert 0 < fake.ttl("k") <= 3600


def test_service_uses_redis_cache(tmp_path):
    settings = Settings(db_path=str(tmp_path / "s.db"), llm_api_key="", _env_file=None)
    base = Store(settings.db_path)
    store = CachedStore(base, RedisCache(client=fakeredis.FakeRedis(decode_responses=True)))

    class FakeLLM:
        configured = True

        async def classify(self, text, filename):
            return "invoice", 0.99, "fake"

    svc = SmartDocService(settings, store, llm=FakeLLM())
    text = "广东增值税专用发票 价税合计 ¥10.00"
    first = asyncio.run(svc.classify(text))
    second = asyncio.run(svc.classify(text))
    assert first["engine"] == "llm"
    assert second["engine"] == "cache"
    assert second["latency_ms"] == 0
    base.close()


def test_pg_sql_portability():
    """PgStore schema must not contain SQLite-only syntax."""
    from app import pg_store

    sql = pg_store._SCHEMA
    for banned in ("AUTOINCREMENT", "INSERT OR REPLACE", "PRAGMA"):
        assert banned not in sql
    assert "SERIAL" in sql and "ON CONFLICT" in Path(pg_store.__file__).read_text()


@pytest.mark.skipif(not os.environ.get("SMARTDOC_TEST_PG_DSN"), reason="no test PostgreSQL DSN")
def test_pg_store_parity(tmp_path):
    from app.pg_store import PgStore

    pg = PgStore(os.environ["SMARTDOC_TEST_PG_DSN"])
    lite = Store(str(tmp_path / "l.db"))
    for store in (pg, lite):
        doc = store.save_document("发票文本", "a.txt", "invoice", 0.9, "rules", {"x": 1}, 5)
        assert store.get_document(doc)["category"] == "invoice"
        store.update_document(doc, engine="llm")
        assert store.get_document(doc)["engine"] == "llm"
        store.cache_put("k", {"v": 1})
        assert store.cache_get("k") == {"v": 1}
        store.log_event("classify", "rules", 5, True)
        assert store.stats()["total_documents"] >= 1
        assert len(store.list_documents()) >= 1
    lite.close()
