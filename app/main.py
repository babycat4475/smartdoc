"""SmartDoc API — FastAPI application."""
from __future__ import annotations

import io
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .config import Settings, get_settings
from .schemas import (
    ClassifyRequest, ClassifyResponse, DocumentRecord, ExtractRequest,
    ExtractResponse, HealthResponse, StatsResponse,
)
from .service import SmartDocService
from .store import Store

VERSION = "1.0.0"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.db_path)
    service = SmartDocService(settings, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        store.close()

    app = FastAPI(
        title="SmartDoc",
        description="LLM-powered document classification & structured extraction service",
        version=VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.state.service = service
    app.state.settings = settings

    def svc() -> SmartDocService:
        return app.state.service

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health():
        return {
            "status": "ok" if service.llm.configured else "degraded",
            "llm_configured": service.llm.configured,
            "version": VERSION,
        }

    @app.post("/api/v1/classify", response_model=ClassifyResponse, tags=["inference"])
    async def classify(req: ClassifyRequest, s: SmartDocService = Depends(svc)):
        return await s.classify(req.text, req.filename)

    @app.post("/api/v1/extract", response_model=ExtractResponse, tags=["inference"])
    async def extract(req: ExtractRequest, s: SmartDocService = Depends(svc)):
        return await s.extract(req.text, req.category)

    @app.post("/api/v1/upload", response_model=ExtractResponse, tags=["inference"])
    async def upload(file: UploadFile = File(...), s: SmartDocService = Depends(svc)):
        raw = await file.read()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "file too large (max 2MB)")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="replace")
        if not text.strip():
            raise HTTPException(422, "empty document")
        return await s.extract(text, None)

    @app.get("/api/v1/documents", tags=["documents"])
    async def list_documents(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        s: SmartDocService = Depends(svc),
    ):
        return s.store.list_documents(limit, offset)

    @app.get("/api/v1/documents/{doc_id}", response_model=DocumentRecord, tags=["documents"])
    async def get_document(doc_id: str, s: SmartDocService = Depends(svc)):
        doc = s.store.get_document(doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        return doc

    @app.get("/api/v1/stats", response_model=StatsResponse, tags=["meta"])
    async def stats(s: SmartDocService = Depends(svc)):
        return s.store.stats()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index():
        return _DASHBOARD_HTML

    return app


app = create_app()

_DASHBOARD_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>SmartDoc</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1a2332}
h1{color:#1f4e79}textarea{width:100%;height:160px;font-size:14px;padding:8px;box-sizing:border-box}
button{background:#1f4e79;color:#fff;border:0;padding:9px 22px;border-radius:6px;font-size:14px;cursor:pointer;margin:8px 8px 0 0}
pre{background:#f4f6f9;padding:12px;border-radius:6px;overflow:auto;font-size:13px}
.badge{display:inline-block;background:#e8f0fa;color:#1f4e79;padding:2px 10px;border-radius:10px;font-size:12px;margin-left:8px}
</style></head><body>
<h1>SmartDoc <span class="badge">FastAPI + LLM</span></h1>
<p>粘贴文档文本，体验分类与结构化抽取。API 文档见 <a href="/docs">/docs</a>。</p>
<textarea id="t" placeholder="粘贴发票 / 合同 / 简历 / 论文等文本…"></textarea>
<button onclick="run('classify')">分类</button>
<button onclick="run('extract')">分类 + 抽取</button>
<pre id="out">结果将显示在这里</pre>
<script>
async function run(op){
  const out=document.getElementById('out');
  out.textContent='处理中…';
  const r=await fetch('/api/v1/'+op,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:document.getElementById('t').value})});
  out.textContent=JSON.stringify(await r.json(),null,2);
}
</script></body></html>"""
