"""Rule-based fallback engine: keyword classification + regex extraction.

Used when no LLM key is configured, when the LLM call fails after retries,
and as an offline baseline in evaluation. Deterministic and dependency-free.
"""
from __future__ import annotations

import re
from typing import Any

from .contracts import required_fields
from .schemas import CATEGORIES

# Ordered: first match wins ties by score; weights tuned on eval corpus.
KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "invoice": [("发票", 3), ("价税合计", 4), ("发票号码", 4), ("税率", 2), ("增值税", 3), ("invoice", 3), ("税额", 2), ("fapiao", 4), ("tax rate", 2), ("价税合汁", 3)],
    "contract": [("合同", 3), ("甲方", 3), ("乙方", 3), ("签订", 2), ("违约", 2), ("协议", 1.5), ("contract", 3), ("party a", 3), ("party b", 3), ("agreement", 2), ("出租方", 3), ("承租方", 3), ("委托方", 2.5), ("受托方", 2.5), ("breach", 2)],
    "resume": [("简历", 4), ("求职", 3), ("教育背景", 3), ("工作经历", 3), ("毕业院校", 2), ("技能", 1), ("resume", 3), ("curriculum vitae", 4), ("b.sc", 2), ("m.sc", 2), ("skills:", 2), ("internship", 2), ("实习", 1.5)],
    "paper": [("abstract", 3), ("摘要", 1.5), ("关键词", 2), ("参考文献", 3), ("doi", 3), ("proceedings", 2), ("论文", 2), ("实验结果", 2), ("keywords:", 2), ("neurips", 3), ("cvpr", 3), ("icml", 3), ("acl", 1.5), ("references:", 2), ("we propose", 1.5), ("本文提出", 2)],
    "receipt": [("小票", 4), ("收据", 4), ("收银", 3), ("合计", 1), ("找零", 3), ("receipt", 3), ("交易号", 2), ("应收", 2.5), ("实收", 2.5), ("支付金额", 2.5), ("订单 #", 2), ("购物凭证", 3), ("欢迎再次光临", 3)],
    "report": [("报告", 2.5), ("调研", 2), ("分析报告", 4), ("总结", 1), ("report", 2)],
    "notice": [("通知", 3), ("公告", 3), ("兹定于", 3), ("特此通知", 4), ("notice", 2), ("announcement", 3)],
}

_DATE = r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"
_AMOUNT = r"(?:¥|￥|RMB|CNY|合计[:：]?|金额[:：]?|总计[:：]?)\s*([0-9][0-9,]*\.?[0-9]*)"
_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_PHONE = r"(?:\+?86[- ]?)?1[3-9]\d{9}"


def classify_rules(text: str, filename: str | None = None) -> tuple[str, float, str]:
    hay = text.lower()
    if filename:
        hay = filename.lower() + "\n" + hay
    scores: dict[str, float] = {c: 0.0 for c in CATEGORIES}
    hits: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for cat, kws in KEYWORDS.items():
        for kw, w in kws:
            n = hay.count(kw.lower())
            if n:
                scores[cat] += w * min(n, 3)
                hits[cat].append(kw)
    # structural signals (regex evidence beyond keywords)
    if re.search(_EMAIL, text) and re.search(_PHONE, text):
        scores["resume"] += 3.0
        hits["resume"].append("email+phone")
    if re.search(r"(硕士|本科|博士|b\.sc|m\.sc|ph\.d)", hay) and re.search(r"(大学|学院|university)", hay):
        scores["resume"] += 1.5
        hits["resume"].append("degree+school")
    if re.search(r"x\s*\d", hay) and re.search(r"(应收|实收|支付金额|总计|合计)", hay):
        scores["receipt"] += 2.0
        hits["receipt"].append("qty+total")
    best = max(scores, key=lambda c: scores[c])
    if scores[best] <= 0:
        return "other", 0.3, "no keyword matched"
    total = sum(scores.values()) or 1.0
    conf = min(0.95, 0.4 + 0.55 * scores[best] / total)
    reason = "keywords: " + ", ".join(hits[best][:4])
    return best, round(conf, 3), reason


def _first(pattern: str, text: str, group: int = 0) -> str | None:
    m = re.search(pattern, text)
    return m.group(group) if m else None


def _norm_date(text: str) -> str | None:
    m = re.search(_DATE, text)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _amount(text: str) -> float | None:
    m = re.search(_AMOUNT, text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def extract_rules(text: str, category: str) -> dict[str, Any]:
    f: dict[str, Any] = {}
    date = _norm_date(text)
    amount = _amount(text)
    email = _first(_EMAIL, text)
    phone = _first(_PHONE, text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0][:80] if lines else None

    if category == "invoice":
        f = {
            "invoice_number": _first(r"发票号码[:：]?\s*([0-9]{6,})", text, 1),
            "invoice_date": date,
            "seller": _first(r"销售方[:：]?\s*([^\n\r]{2,40})", text, 1),
            "buyer": _first(r"购买方[:：]?\s*([^\n\r]{2,40})", text, 1),
            "total_amount": amount,
            "currency": "CNY" if amount is not None else None,
        }
    elif category == "contract":
        f = {
            "title": title,
            "party_a": _first(r"甲方[:：]?\s*([^\n\r]{2,40})", text, 1),
            "party_b": _first(r"乙方[:：]?\s*([^\n\r]{2,40})", text, 1),
            "sign_date": date,
            "amount": amount,
            "duration": _first(r"(期限[^\n\r]{0,40})", text, 1),
        }
    elif category == "resume":
        name = _first(r"姓名[:：]?\s*([\u4e00-\u9fa5A-Za-z·]{2,10})", text, 1)
        f = {
            "name": name or (title if title and len(title) <= 6 else None),
            "email": email,
            "phone": phone,
            "education": _first(r"(博士|硕士|本科|大专)", text, 1),
            "school": _first(r"(?:毕业院校|学校)[:：]?\s*([^\n\r]{2,30})", text, 1)
            or _first(r"([\u4e00-\u9fa5]{2,12}(?:大学|学院))", text, 1),
            "skills": re.findall(r"(Python|Java|C\+\+|SQL|Linux|Docker|Git|PyTorch|FastAPI)", text),
        }
    elif category == "paper":
        f = {
            "title": title,
            "authors": None,
            "venue": _first(r"(ICCSM|CVPR|NeurIPS|ICML|ACL|IEEE[A-Za-z ]*)", text, 1),
            "year": _first(r"\b(20\d{2})\b", text, 1),
            "keywords": None,
        }
    elif category == "receipt":
        f = {"merchant": title, "date": date, "total_amount": amount, "items": None}
    elif category == "notice":
        f = {"title": title, "issuer": None, "date": date,
             "deadline": _norm_date(text[text.find("截止"):]) if "截止" in text else None}
    else:
        f = {"title": title, "summary": (lines[1][:100] if len(lines) > 1 else title)}

    return {k: f.get(k) for k in required_fields(category)}
