"""PostgreSQL backend for SmartDoc (branch: postgres-redis).

Same public interface as app.store.Store, so SmartDocService is unchanged.
Schema is portable SQL; SQLite-isms (AUTOINCREMENT, INSERT OR REPLACE) removed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from .store import text_hash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    filename TEXT,
    text_hash TEXT NOT NULL,
    text_preview TEXT NOT NULL,
    category TEXT,
    confidence DOUBLE PRECISION,
    engine TEXT,
    fields TEXT,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(text_hash);
CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    engine TEXT,
    latency_ms INTEGER,
    ok INTEGER NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PgStore:
    """Connection-per-operation (simple + process-safe); pool in production."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(_SCHEMA)
            conn.commit()

    def _conn(self):
        return psycopg2.connect(self.dsn)

    def close(self) -> None:  # parity with Store
        pass

    # ---- cache ----
    def cache_get(self, key: str) -> Optional[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM cache WHERE cache_key=%s", (key,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, payload: dict) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cache VALUES (%s,%s,%s) "
                "ON CONFLICT (cache_key) DO UPDATE SET payload=EXCLUDED.payload, created_at=EXCLUDED.created_at",
                (key, json.dumps(payload, ensure_ascii=False), _now()),
            )
            conn.commit()

    # ---- documents ----
    def save_document(
        self,
        text: str,
        filename: Optional[str],
        category: Optional[str] = None,
        confidence: Optional[float] = None,
        engine: Optional[str] = None,
        fields: Optional[dict[str, Any]] = None,
        latency_ms: Optional[int] = None,
    ) -> str:
        doc_id = uuid.uuid4().hex[:12]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    doc_id, _now(), filename, text_hash(text), text[:160],
                    category, confidence, engine,
                    json.dumps(fields, ensure_ascii=False) if fields is not None else None,
                    latency_ms,
                ),
            )
            conn.commit()
        return doc_id

    def update_document(self, doc_id: str, **kw: Any) -> None:
        if not kw:
            return
        if "fields" in kw and kw["fields"] is not None:
            kw["fields"] = json.dumps(kw["fields"], ensure_ascii=False)
        cols = ", ".join(f"{k}=%s" for k in kw)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE documents SET {cols} WHERE doc_id=%s", (*kw.values(), doc_id))
            conn.commit()

    def get_document(self, doc_id: str) -> Optional[dict]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,))
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["fields"] = json.loads(d["fields"]) if d["fields"] else None
        return d

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT doc_id, created_at, filename, category, confidence, engine, text_preview "
                "FROM documents ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return [dict(r) for r in cur.fetchall()]

    # ---- events / stats ----
    def log_event(self, kind: str, engine: str, latency_ms: int, ok: bool) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (ts, kind, engine, latency_ms, ok) VALUES (%s,%s,%s,%s,%s)",
                (_now(), kind, engine, latency_ms, int(ok)),
            )
            conn.commit()

    def stats(self) -> dict:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            total = cur.fetchone()[0]
            cur.execute("SELECT category, COUNT(*) FROM documents WHERE category IS NOT NULL GROUP BY category")
            by_cat = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM events WHERE kind='cache_hit'")
            cache_hits = cur.fetchone()[0]
            cur.execute("SELECT AVG(latency_ms) FROM events WHERE ok=1 AND kind!='cache_hit'")
            avg = cur.fetchone()[0]
            cur.execute("SELECT engine, COUNT(*) FROM events WHERE engine IS NOT NULL GROUP BY engine")
            by_engine = {r[0]: r[1] for r in cur.fetchall()}
        return {
            "total_documents": total,
            "by_category": by_cat,
            "by_engine": by_engine,
            "cache_hits": cache_hits,
            "avg_latency_ms": round(float(avg or 0.0), 1),
        }
