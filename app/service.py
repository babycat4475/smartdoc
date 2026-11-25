"""Service layer: orchestrates cache -> LLM -> rules fallback, persistence, metrics."""
from __future__ import annotations

import time
from typing import Any, Optional

from .config import Settings
from .contracts import required_fields
from .llm import LLMEngine, LLMError
from .rules import classify_rules, extract_rules
from .schemas import CATEGORY_LABELS_ZH
from .store import Store, text_hash


class SmartDocService:
    def __init__(self, settings: Settings, store: Store, llm: Optional[LLMEngine] = None):
        self.s = settings
        self.store = store
        self.llm = llm if llm is not None else LLMEngine(settings)

    # ---- classification ----
    async def classify(self, text: str, filename: Optional[str] = None) -> dict[str, Any]:
        t0 = time.perf_counter()
        ckey = f"cls:{self.s.llm_model}:{text_hash(text + (filename or ''))}"
        if self.s.cache_enabled:
            cached = self.store.cache_get(ckey)
            if cached:
                doc_id = self.store.save_document(text, filename, latency_ms=0, **cached)
                self.store.log_event("cache_hit", "cache", 0, True)
                return self._cls_response(doc_id, cached, "cache", 0)

        engine = "rules"
        try:
            if self.llm.configured:
                cat, conf, reason = await self.llm.classify(text, filename)
                engine = "llm"
            else:
                raise LLMError("no API key configured")
        except LLMError:
            if not self.s.rule_fallback_enabled:
                raise
            cat, conf, reason = classify_rules(text, filename)

        ms = int((time.perf_counter() - t0) * 1000)
        payload = {"category": cat, "confidence": conf, "engine": engine}
        doc_id = self.store.save_document(text, filename, cat, conf, engine, latency_ms=ms)
        if self.s.cache_enabled and engine == "llm":
            self.store.cache_put(ckey, payload)
        self.store.log_event("classify", engine, ms, True)
        return self._cls_response(doc_id, payload, engine, ms, reason)

    @staticmethod
    def _cls_response(doc_id: str, payload: dict, engine: str, ms: int, reason: str = "") -> dict:
        return {
            "doc_id": doc_id,
            "category": payload["category"],
            "category_zh": CATEGORY_LABELS_ZH.get(payload["category"], "其他"),
            "confidence": payload["confidence"],
            "reason": reason or payload.get("reason", ""),
            "engine": engine,
            "latency_ms": ms,
        }

    # ---- extraction ----
    async def extract(self, text: str, category: Optional[str] = None) -> dict[str, Any]:
        t0 = time.perf_counter()
        if category is None:
            cls = await self.classify(text)
            category = cls["category"]
            doc_id = cls["doc_id"]
        else:
            doc_id = self.store.save_document(text, None, category)

        ekey = f"ext:{self.s.llm_model}:{category}:{text_hash(text)}"
        if self.s.cache_enabled:
            cached = self.store.cache_get(ekey)
            if cached:
                fields = cached["fields"]
                self.store.update_document(doc_id, fields=fields, engine="cache", latency_ms=0)
                self.store.log_event("cache_hit", "cache", 0, True)
                return self._ext_response(doc_id, category, fields, "cache", 0)

        engine = "rules"
        try:
            if self.llm.configured:
                fields = await self.llm.extract(text, category)
                engine = "llm"
            else:
                raise LLMError("no API key configured")
        except LLMError:
            if not self.s.rule_fallback_enabled:
                raise
            fields = extract_rules(text, category)

        ms = int((time.perf_counter() - t0) * 1000)
        self.store.update_document(doc_id, fields=fields, engine=engine, latency_ms=ms)
        if self.s.cache_enabled and engine == "llm":
            self.store.cache_put(ekey, {"fields": fields})
        self.store.log_event("extract", engine, ms, True)
        return self._ext_response(doc_id, category, fields, engine, ms)

    @staticmethod
    def _ext_response(doc_id: str, category: str, fields: dict, engine: str, ms: int) -> dict:
        missing = [k for k in required_fields(category) if fields.get(k) in (None, "", [])]
        return {
            "doc_id": doc_id,
            "category": category,
            "fields": fields,
            "missing_fields": missing,
            "engine": engine,
            "latency_ms": ms,
        }
