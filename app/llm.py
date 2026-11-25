"""LLM engine: OpenAI-compatible chat completions with strict JSON output.

Reliability features:
- timeout + bounded retries with exponential backoff
- JSON-mode request, fenced-JSON tolerance, schema validation
- graceful fallback to rule engine (handled by service layer)
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from .config import Settings
from .contracts import build_extraction_schema, required_fields
from .schemas import CATEGORIES, CATEGORY_LABELS_ZH


class LLMError(RuntimeError):
    pass


CLASSIFY_SYSTEM = (
    "你是文档分类器。把用户文档分到且仅分到以下类别之一: "
    + ", ".join(CATEGORIES)
    + "。只输出 JSON 对象: {\"category\": string, \"confidence\": number(0-1), \"reason\": string}。"
    "reason 用中文一句话。不要输出任何其他文字。"
)

EXTRACT_SYSTEM_TMPL = (
    "你是信息抽取器。从文档中抽取字段，输出且仅输出一个 JSON 对象。"
    "找不到的字段填 null，不要编造。金额输出数字。列表输出字符串数组。"
    "字段契约(JSON Schema): {schema}"
)


def _parse_json_loose(content: str) -> dict:
    content = content.strip()
    # tolerate ```json fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    if m:
        content = m.group(1)
    # tolerate leading prose: take first {...} balanced-ish block
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start : end + 1]
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON from model: {e}") from e
    if not isinstance(obj, dict):
        raise LLMError("model output is not a JSON object")
    return obj


class LLMEngine:
    def __init__(self, settings: Settings):
        self.s = settings

    @property
    def configured(self) -> bool:
        return bool(self.s.llm_api_key)

    async def _chat(self, system: str, user: str) -> dict:
        payload = {
            "model": self.s.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}"}
        url = self.s.llm_base_url.rstrip("/") + "/chat/completions"
        last_err: Exception | None = None
        for attempt in range(self.s.llm_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.s.llm_timeout_s) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise LLMError(f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                return _parse_json_loose(data["choices"][0]["message"]["content"])
            except (httpx.HTTPError, LLMError, KeyError) as e:
                last_err = e
                if attempt < self.s.llm_max_retries:
                    await asyncio.sleep(0.8 * (2**attempt))
        raise LLMError(f"LLM failed after retries: {last_err}")

    async def classify(self, text: str, filename: str | None) -> tuple[str, float, str]:
        user = (f"文件名: {filename}\n" if filename else "") + "文档内容:\n" + text[: self.s.max_text_chars]
        obj = await self._chat(CLASSIFY_SYSTEM, user)
        cat = str(obj.get("category", "")).strip().lower()
        if cat not in CATEGORIES:
            raise LLMError(f"unknown category: {cat!r}")
        conf = float(obj.get("confidence", 0.5))
        conf = max(0.0, min(1.0, conf))
        return cat, conf, str(obj.get("reason", ""))[:200]

    async def extract(self, text: str, category: str) -> dict[str, Any]:
        schema = build_extraction_schema(category)
        system = EXTRACT_SYSTEM_TMPL.format(schema=json.dumps(schema, ensure_ascii=False))
        user = f"类别: {category}({CATEGORY_LABELS_ZH.get(category, '')})\n文档内容:\n" + text[: self.s.max_text_chars]
        obj = await self._chat(system, user)
        out: dict[str, Any] = {}
        for field in required_fields(category):
            out[field] = _coerce(field, category, obj.get(field))
        return out


def _coerce(field: str, category: str, value: Any) -> Any:
    from .contracts import FIELD_SPECS

    typ = FIELD_SPECS.get(category, FIELD_SPECS["other"]).get(field, ("str", ""))[0]
    if value is None:
        return None
    if typ == "float":
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(re.sub(r"[^\d.]", "", value) or "nan")
            except ValueError:
                return None
        return None
    if typ == "list[str]":
        if isinstance(value, list):
            return [str(v) for v in value][:30]
        if isinstance(value, str):
            return [s.strip() for s in re.split(r"[,，;；]", value) if s.strip()][:30]
        return None
    return str(value)[:300]
