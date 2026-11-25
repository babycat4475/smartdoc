"""Pydantic schemas: request/response models and the extraction contract."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["invoice", "contract", "resume", "paper", "receipt", "report", "notice", "other"]

CATEGORIES: tuple[str, ...] = (
    "invoice", "contract", "resume", "paper", "receipt", "report", "notice", "other",
)

CATEGORY_LABELS_ZH = {
    "invoice": "发票",
    "contract": "合同",
    "resume": "简历",
    "paper": "学术论文",
    "receipt": "收据/小票",
    "report": "报告",
    "notice": "通知公告",
    "other": "其他",
}


class ClassifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000, description="Raw document text")
    filename: Optional[str] = Field(default=None, description="Optional original filename hint")


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    category: Optional[Category] = Field(
        default=None, description="Skip classification when the category is known"
    )


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class ClassifyResponse(BaseModel):
    doc_id: str
    category: Category
    category_zh: str
    confidence: float
    reason: str
    engine: Literal["llm", "rules", "cache"]
    latency_ms: int


class ExtractResponse(BaseModel):
    doc_id: str
    category: Category
    fields: dict[str, Any]
    missing_fields: list[str]
    engine: Literal["llm", "rules", "cache"]
    latency_ms: int


class DocumentRecord(BaseModel):
    doc_id: str
    created_at: str
    filename: Optional[str]
    category: Optional[str]
    confidence: Optional[float]
    engine: Optional[str]
    fields: Optional[dict[str, Any]]
    text_preview: str


class StatsResponse(BaseModel):
    total_documents: int
    by_category: dict[str, int]
    by_engine: dict[str, int]
    cache_hits: int
    avg_latency_ms: float


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    llm_configured: bool
    version: str
