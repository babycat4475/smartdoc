"""Unit tests for the deterministic rule engine."""
from __future__ import annotations

from app.rules import classify_rules, extract_rules

INVOICE = """广东增值税专用发票
发票号码: 24310137
开票日期: 2026-03-15
购买方: 澳门科技大学广州校友会
销售方: 广州云启科技有限公司
税率: 6%
价税合计: ¥12,800.00"""

RESUME = """个人简历
姓名: 张烨恒
电话: 13544484866
邮箱: zyh@example.com
教育背景
澳门科技大学 计算机科学与技术 本科
技能: Python, SQL, Linux, Git"""

CONTRACT = """技术服务合同
甲方: 广州中科易德科技有限公司
乙方: 张烨恒
签订日期: 2026-03-10
合同金额: ¥8,000
期限自签订之日起12个月。违约应赔偿。"""


def test_classify_invoice():
    cat, conf, _ = classify_rules(INVOICE)
    assert cat == "invoice"
    assert conf > 0.5


def test_classify_resume():
    assert classify_rules(RESUME)[0] == "resume"


def test_classify_contract():
    assert classify_rules(CONTRACT)[0] == "contract"


def test_classify_other():
    cat, conf, _ = classify_rules("今天天气不错，适合出去散步。")
    assert cat == "other"
    assert conf <= 0.5


def test_classify_filename_hint():
    cat, _, _ = classify_rules("若干内容", filename="发票_2026.pdf")
    assert cat == "invoice"


def test_extract_invoice_fields():
    f = extract_rules(INVOICE, "invoice")
    assert f["invoice_number"] == "24310137"
    assert f["invoice_date"] == "2026-03-15"
    assert f["total_amount"] == 12800.00
    assert f["seller"] == "广州云启科技有限公司"
    assert f["buyer"] == "澳门科技大学广州校友会"
    assert f["currency"] == "CNY"


def test_extract_resume_fields():
    f = extract_rules(RESUME, "resume")
    assert f["name"] == "张烨恒"
    assert f["email"] == "zyh@example.com"
    assert f["phone"] == "13544484866"
    assert f["education"] == "本科"
    assert "Python" in f["skills"]


def test_extract_contract_fields():
    f = extract_rules(CONTRACT, "contract")
    assert f["party_a"] == "广州中科易德科技有限公司"
    assert f["party_b"] == "张烨恒"
    assert f["amount"] == 8000.0
    assert f["sign_date"] == "2026-03-10"


def test_extract_missing_is_none():
    f = extract_rules("没有号码的发票文本 价税合计 ¥5.00", "invoice")
    assert f["invoice_number"] is None
    assert f["total_amount"] == 5.0


def test_amount_with_commas():
    f = extract_rules("收据\n合计: ¥1,234,567.89", "receipt")
    assert f["total_amount"] == 1234567.89
