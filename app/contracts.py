"""Per-category extraction contracts (JSON-Schema style, enforced downstream)."""
from __future__ import annotations

# field name -> (type, description). "str" | "float" | "list[str]"
FIELD_SPECS: dict[str, dict[str, tuple[str, str]]] = {
    "invoice": {
        "invoice_number": ("str", "发票号码"),
        "invoice_date": ("str", "开票日期, ISO YYYY-MM-DD if possible"),
        "seller": ("str", "销售方名称"),
        "buyer": ("str", "购买方名称"),
        "total_amount": ("float", "价税合计金额"),
        "currency": ("str", "币种, e.g. CNY"),
    },
    "contract": {
        "title": ("str", "合同标题"),
        "party_a": ("str", "甲方名称"),
        "party_b": ("str", "乙方名称"),
        "sign_date": ("str", "签订日期"),
        "amount": ("float", "合同金额, if stated"),
        "duration": ("str", "合同期限描述"),
    },
    "resume": {
        "name": ("str", "姓名"),
        "email": ("str", "邮箱"),
        "phone": ("str", "电话"),
        "education": ("str", "最高学历"),
        "school": ("str", "毕业院校"),
        "skills": ("list[str]", "技能列表"),
    },
    "paper": {
        "title": ("str", "论文标题"),
        "authors": ("list[str]", "作者列表"),
        "venue": ("str", "发表会议/期刊"),
        "year": ("str", "年份"),
        "keywords": ("list[str]", "关键词"),
    },
    "receipt": {
        "merchant": ("str", "商户名称"),
        "date": ("str", "交易日期"),
        "total_amount": ("float", "总金额"),
        "items": ("list[str]", "商品条目"),
    },
    "report": {
        "title": ("str", "报告标题"),
        "author": ("str", "作者/机构"),
        "date": ("str", "日期"),
        "summary": ("str", "一句话摘要"),
    },
    "notice": {
        "title": ("str", "通知标题"),
        "issuer": ("str", "发布单位"),
        "date": ("str", "发布日期"),
        "deadline": ("str", "截止日期, if stated"),
    },
    "other": {
        "title": ("str", "文档标题或主题"),
        "summary": ("str", "一句话摘要"),
    },
}


def required_fields(category: str) -> list[str]:
    return list(FIELD_SPECS.get(category, FIELD_SPECS["other"]).keys())


def build_extraction_schema(category: str) -> dict:
    """A JSON Schema used both for prompting and for post-validation."""
    spec = FIELD_SPECS.get(category, FIELD_SPECS["other"])
    props: dict = {}
    for name, (typ, desc) in spec.items():
        if typ == "float":
            props[name] = {"type": ["number", "null"], "description": desc}
        elif typ == "list[str]":
            props[name] = {"type": "array", "items": {"type": "string"}, "description": desc}
        else:
            props[name] = {"type": ["string", "null"], "description": desc}
    return {"type": "object", "properties": props, "additionalProperties": False}
