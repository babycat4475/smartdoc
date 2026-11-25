"""API integration tests via TestClient (rules engine, isolated temp DB)."""
from __future__ import annotations

import json
from pathlib import Path

INVOICE = "广东增值税专用发票\n发票号码: 24310137\n开票日期: 2026-03-15\n价税合计: ¥12,800.00\n销售方: 广州云启科技有限公司"


def test_health_degraded_without_key(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["llm_configured"] is False


def test_classify_ok(client):
    r = client.post("/api/v1/classify", json={"text": INVOICE})
    assert r.status_code == 200
    b = r.json()
    assert b["category"] == "invoice"
    assert b["category_zh"] == "发票"
    assert b["engine"] == "rules"
    assert 0 <= b["confidence"] <= 1
    assert b["doc_id"]


def test_classify_validation_error(client):
    assert client.post("/api/v1/classify", json={"text": ""}).status_code == 422
    assert client.post("/api/v1/classify", json={}).status_code == 422


def test_extract_pipeline(client):
    r = client.post("/api/v1/extract", json={"text": INVOICE})
    assert r.status_code == 200
    b = r.json()
    assert b["category"] == "invoice"
    assert b["fields"]["total_amount"] == 12800.0
    assert "buyer" in b["missing_fields"]


def test_extract_explicit_category(client):
    r = client.post("/api/v1/extract", json={"text": INVOICE, "category": "invoice"})
    assert r.status_code == 200
    assert r.json()["category"] == "invoice"


def test_document_persistence_and_lookup(client):
    doc_id = client.post("/api/v1/classify", json={"text": INVOICE}).json()["doc_id"]
    r = client.get(f"/api/v1/documents/{doc_id}")
    assert r.status_code == 200
    assert r.json()["category"] == "invoice"
    listing = client.get("/api/v1/documents").json()
    assert any(d["doc_id"] == doc_id for d in listing)


def test_document_404(client):
    assert client.get("/api/v1/documents/nonexistent").status_code == 404


def test_stats(client):
    client.post("/api/v1/classify", json={"text": INVOICE})
    s = client.get("/api/v1/stats").json()
    assert s["total_documents"] >= 1
    assert s["by_category"].get("invoice", 0) >= 1


def test_upload_txt(client):
    r = client.post(
        "/api/v1/upload",
        files={"file": ("invoice.txt", INVOICE.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["category"] == "invoice"


def test_upload_empty_422(client):
    r = client.post("/api/v1/upload", files={"file": ("e.txt", b"   ", "text/plain")})
    assert r.status_code == 422


def test_upload_gb18030(client):
    r = client.post(
        "/api/v1/upload",
        files={"file": ("cn.txt", INVOICE.encode("gb18030"), "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["category"] == "invoice"


def test_openapi_schema(client):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/classify" in schema["paths"]
    assert "/api/v1/extract" in schema["paths"]


def test_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "SmartDoc" in r.text


def test_corpus_accuracy_floor(client):
    """Regression guard: rule engine must keep >=85% accuracy on the corpus."""
    corpus = Path(__file__).resolve().parents[1] / "eval" / "corpus.jsonl"
    docs = [json.loads(ln) for ln in corpus.read_text().splitlines() if ln.strip()]
    ok = 0
    for d in docs:
        r = client.post("/api/v1/classify", json={"text": d["text"]})
        ok += int(r.json()["category"] == d["category"])
    assert ok / len(docs) >= 0.85, f"accuracy dropped: {ok}/{len(docs)}"
