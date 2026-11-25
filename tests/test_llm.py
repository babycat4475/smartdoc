"""LLM engine tests with a fake transport — no network, no API key.

Covers: JSON parsing tolerance, retry on 429/5xx, category validation,
type coercion, and service-level fallback to rules.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.llm import LLMEngine, LLMError, _parse_json_loose
from app.service import SmartDocService
from app.store import Store


def test_parse_plain_json():
    assert _parse_json_loose('{"category":"invoice"}')["category"] == "invoice"


def test_parse_fenced_json():
    assert _parse_json_loose('```json\n{"a": 1}\n```')["a"] == 1


def test_parse_json_with_prose():
    assert _parse_json_loose('好的，结果如下: {"a": 2} 希望有帮助')["a"] == 2


def test_parse_invalid_raises():
    with pytest.raises(LLMError):
        _parse_json_loose("完全不是JSON")


def _settings(**kw) -> Settings:
    base = dict(llm_api_key="test-key", llm_max_retries=2, _env_file=None)
    base.update(kw)
    return Settings(**base)


def _engine_with(handler) -> LLMEngine:
    eng = LLMEngine(_settings())
    transport = httpx.MockTransport(handler)

    async def chat(system, user):
        payload = {
            "model": "x", "messages": [], "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(transport=transport, timeout=5) as client:
            resp = await client.post("https://fake.local/v1/chat/completions", json=payload)
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}")
        return _parse_json_loose(resp.json()["choices"][0]["message"]["content"])

    eng._chat = chat  # type: ignore[method-assign]
    return eng


def _ok(content: str):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_llm_classify_ok():
    eng = _engine_with(lambda req: _ok('{"category":"contract","confidence":0.9,"reason":"甲方乙方"}'))
    cat, conf, reason = asyncio.run(eng.classify("合同文本", None))
    assert (cat, conf) == ("contract", 0.9)


def test_llm_classify_bad_category_rejected():
    eng = _engine_with(lambda req: _ok('{"category":"banana","confidence":0.9}'))
    with pytest.raises(LLMError):
        asyncio.run(eng.classify("text", None))


def test_llm_extract_coercion():
    content = '{"invoice_number": 123456, "total_amount": "¥1,280.50", "buyer": null, "seller": "X", "invoice_date": null, "currency": null}'
    eng = _engine_with(lambda req: _ok(content))
    f = asyncio.run(eng.extract("t", "invoice"))
    assert f["invoice_number"] == "123456"
    assert f["total_amount"] == 1280.50
    assert f["buyer"] is None


def test_retry_then_success(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return _ok('{"category":"resume","confidence":0.8,"reason":"ok"}')

    settings = _settings()
    eng = LLMEngine(settings)

    real_client = httpx.AsyncClient

    def patched_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    cat, _, _ = asyncio.run(eng.classify("简历", None))
    assert cat == "resume"
    assert calls["n"] == 3


def test_service_falls_back_to_rules(tmp_path):
    def handler(request):
        return httpx.Response(500)

    settings = _settings(db_path=str(tmp_path / "s.db"), llm_max_retries=0)
    store = Store(settings.db_path)

    class BrokenLLM(LLMEngine):
        @property
        def configured(self):
            return True

        async def classify(self, text, filename):
            raise LLMError("boom")

    svc = SmartDocService(settings, store, llm=BrokenLLM(settings))
    out = asyncio.run(svc.classify("广东增值税专用发票 价税合计 ¥10.00 发票号码: 12345678"))
    assert out["engine"] == "rules"
    assert out["category"] == "invoice"
    store.close()


def test_cache_hit_second_call(tmp_path):
    settings = _settings(db_path=str(tmp_path / "c.db"), llm_api_key="")
    store = Store(settings.db_path)

    class FakeLLM(LLMEngine):
        @property
        def configured(self):
            return True

        async def classify(self, text, filename):
            return "invoice", 0.99, "fake"

    svc = SmartDocService(settings, store, llm=FakeLLM(settings))
    text = "广东增值税专用发票 价税合计 ¥10.00"
    first = asyncio.run(svc.classify(text))
    second = asyncio.run(svc.classify(text))
    assert first["engine"] == "llm"
    assert second["engine"] == "cache"
    assert second["latency_ms"] == 0
    store.close()
