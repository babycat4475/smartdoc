"""Offline evaluation: classification accuracy + extraction field recall.

Runs the deterministic rule engine over eval/corpus.jsonl (no API key needed)
and, with --llm, the LLM engine too. Writes eval/report.json + prints summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm import LLMEngine, LLMError  # noqa: E402
from app.rules import classify_rules, extract_rules  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "eval" / "corpus.jsonl"
SETS = {
    "corpus": Path(__file__).resolve().parents[1] / "eval" / "corpus.jsonl",
    "holdout": Path(__file__).resolve().parents[1] / "eval" / "holdout.jsonl",
    "adversarial": Path(__file__).resolve().parents[1] / "eval" / "adversarial.jsonl",
}


def load_corpus(path: Path = CORPUS) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def eval_rules(docs: list[dict]) -> dict:
    t0 = time.perf_counter()
    correct = 0
    per_cat: dict[str, dict[str, int]] = {}
    field_total = field_hit = 0
    confusion: dict[str, dict[str, int]] = {}
    for d in docs:
        pred, conf, reason = classify_rules(d["text"])
        gold = d["category"]
        per_cat.setdefault(gold, {"n": 0, "ok": 0})
        per_cat[gold]["n"] += 1
        ok = pred == gold
        per_cat[gold]["ok"] += int(ok)
        correct += int(ok)
        confusion.setdefault(gold, {})
        confusion[gold][pred] = confusion[gold].get(pred, 0) + 1
        fields = extract_rules(d["text"], gold)
        for v in fields.values():
            field_total += 1
            field_hit += int(v not in (None, "", []))
    ms = (time.perf_counter() - t0) * 1000
    return {
        "engine": "rules",
        "n_docs": len(docs),
        "classification_accuracy": round(correct / len(docs), 4),
        "per_category": {k: round(v["ok"] / v["n"], 4) for k, v in per_cat.items()},
        "field_fill_rate": round(field_hit / field_total, 4),
        "confusion": confusion,
        "total_latency_ms": round(ms, 1),
        "avg_latency_ms_per_doc": round(ms / len(docs), 2),
    }


async def eval_llm(docs: list[dict]) -> dict:
    settings = get_settings()
    llm = LLMEngine(settings)
    if not llm.configured:
        return {"engine": "llm", "error": "SMARTDOC_LLM_API_KEY not set"}
    correct = 0
    field_total = field_hit = 0
    latencies = []
    fallback = 0
    for d in docs:
        t0 = time.perf_counter()
        try:
            pred, conf, reason = await llm.classify(d["text"], None)
        except LLMError:
            fallback += 1
            pred, conf, reason = classify_rules(d["text"])
        latencies.append((time.perf_counter() - t0) * 1000)
        correct += int(pred == d["category"])
        try:
            fields = await llm.extract(d["text"], d["category"])
        except LLMError:
            fields = extract_rules(d["text"], d["category"])
        for v in fields.values():
            field_total += 1
            field_hit += int(v not in (None, "", []))
        await asyncio.sleep(0.15)  # rate-limit courtesy
    return {
        "engine": "llm",
        "model": settings.llm_model,
        "n_docs": len(docs),
        "classification_accuracy": round(correct / len(docs), 4),
        "field_fill_rate": round(field_hit / field_total, 4),
        "llm_failures_fell_back": fallback,
        "avg_latency_ms_per_doc": round(sum(latencies) / len(latencies), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also evaluate the LLM engine (needs API key)")
    args = ap.parse_args()
    docs = load_corpus()
    report = {name: eval_rules(load_corpus(path)) for name, path in SETS.items() if path.exists()}
    if args.llm:
        report["llm"] = asyncio.run(eval_llm(docs))
    out = CORPUS.parent / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nreport written to {out}")


if __name__ == "__main__":
    main()
