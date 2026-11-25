"""SQLite persistence + content-hash result cache (stdlib sqlite3, no ORM)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    filename TEXT,
    text_hash TEXT NOT NULL,
    text_preview TEXT NOT NULL,
    category TEXT,
    confidence REAL,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    engine TEXT,
    latency_ms INTEGER,
    ok INTEGER NOT NULL
);
"""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- cache ----
    def cache_get(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM cache WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def cache_put(self, key: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                (key, json.dumps(payload, ensure_ascii=False), _now()),
            )
            self._conn.commit()

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
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id, _now(), filename, text_hash(text), text[:160],
                    category, confidence, engine,
                    json.dumps(fields, ensure_ascii=False) if fields is not None else None,
                    latency_ms,
                ),
            )
            self._conn.commit()
        return doc_id

    def update_document(self, doc_id: str, **kw: Any) -> None:
        if not kw:
            return
        if "fields" in kw and kw["fields"] is not None:
            kw["fields"] = json.dumps(kw["fields"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in kw)
        with self._lock:
            self._conn.execute(f"UPDATE documents SET {cols} WHERE doc_id=?", (*kw.values(), doc_id))
            self._conn.commit()

    def get_document(self, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["fields"] = json.loads(d["fields"]) if d["fields"] else None
        return d

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, created_at, filename, category, confidence, engine, text_preview "
                "FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- events / stats ----
    def log_event(self, kind: str, engine: str, latency_ms: int, ok: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, engine, latency_ms, ok) VALUES (?,?,?,?,?)",
                (_now(), kind, engine, latency_ms, int(ok)),
            )
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
            by_cat = {
                r["category"]: r["c"]
                for r in self._conn.execute(
                    "SELECT category, COUNT(*) c FROM documents WHERE category IS NOT NULL GROUP BY category"
                )
            }
            cache_hits = self._conn.execute(
                "SELECT COUNT(*) c FROM events WHERE kind='cache_hit'"
            ).fetchone()["c"]
            avg = self._conn.execute(
                "SELECT AVG(latency_ms) a FROM events WHERE ok=1 AND kind!='cache_hit'"
            ).fetchone()["a"]
            by_engine = {
                r["engine"]: r["c"]
                for r in self._conn.execute(
                    "SELECT engine, COUNT(*) c FROM events WHERE engine IS NOT NULL GROUP BY engine"
                )
            }
        return {
            "total_documents": total,
            "by_category": by_cat,
            "by_engine": by_engine,
            "cache_hits": cache_hits,
            "avg_latency_ms": round(avg or 0.0, 1),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
