# SmartDoc

[![tests](https://github.com/babycat4475/smartdoc/actions/workflows/tests.yml/badge.svg)](https://github.com/babycat4475/smartdoc/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**LLM-powered document classification & structured extraction service.**
FastAPI + any OpenAI-compatible LLM + SQLite, with a deterministic rule-engine
fallback so the service works even without an API key.

🔗 **Live Demo**: https://thehun-would-ended-textbook.trycloudflare.com （临时隧道，在线时可玩；离线时按下方 Quickstart 本地运行）

粘贴一段发票 / 合同 / 简历 / 论文文本，SmartDoc 返回类别 + 按类别契约抽取的结构化字段（JSON）。

## Features

- **8 类文档分类**：发票 / 合同 / 简历 / 论文 / 收据 / 报告 / 通知 / 其他
- **按类别的字段契约**（JSON Schema）：发票抽号码/日期/购销方/价税合计，合同抽甲乙方/金额/期限……
- **双引擎**：LLM（OpenAI-compatible，默认 DeepSeek）+ 零依赖规则引擎兜底；LLM 失败自动降级，服务永不因上游宕机而不可用
- **可靠性**：超时 + 指数退避重试、JSON-mode + 围栏容错解析、字段类型强制转换、缺失字段显式报告（不编造）
- **内容哈希缓存**：相同文本二次请求 0ms 命中
- **SQLite 持久化**：文档记录、事件日志、统计接口
- **可评测**：84 篇标注语料（46 开发 + 26 噪声留出 + 12 对抗），一条命令出准确率/字段填充率/延迟报告
- **34 个自动化测试**：单元 + API 集成 + LLM mock（重试/降级/缓存全覆盖）+ 准确率回归门禁
- 自带 Web 演示页（`/`）与 OpenAPI 文档（`/docs`），Dockerfile 一键部署

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 无 key 也能跑（规则引擎）；配置 LLM：
cp .env.example .env   # 填入 SMARTDOC_LLM_API_KEY

uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000       (演示页)
#      http://127.0.0.1:8000/docs  (Swagger)
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/extract \
  -H 'Content-Type: application/json' \
  -d '{"text":"广东增值税专用发票\n发票号码: 24310137\n价税合计: ¥12,800.00"}'
```

```json
{
  "doc_id": "bc2fc25b667c",
  "category": "invoice",
  "fields": {"invoice_number": "24310137", "total_amount": 12800.0, "...": "..."},
  "missing_fields": ["buyer"],
  "engine": "rules",
  "latency_ms": 1
}
```

## API

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/v1/classify` | 文本 → 类别 + 置信度 + 理由 |
| POST | `/api/v1/extract` | 文本 → 类别 + 契约字段（可指定类别跳过分类） |
| POST | `/api/v1/upload` | 上传 .txt（UTF-8 / GB18030 自动识别） |
| GET | `/api/v1/documents` | 分页列出历史文档 |
| GET | `/api/v1/documents/{id}` | 单条详情 |
| GET | `/api/v1/stats` | 分类分布 / 引擎分布 / 缓存命中 / 平均延迟 |
| GET | `/health` | 健康检查（无 key 时 degraded） |

## Architecture

```
client ──► FastAPI (pydantic validation)
              │
              ▼
        SmartDocService
        ├─ cache (SHA-256 content hash → SQLite)
        ├─ LLMEngine (httpx, JSON-mode, retry×2, schema coercion)
        │     └─ on failure ──► RuleEngine (keywords + regex, 0 deps)
        └─ Store (documents / cache / events)
```

分层：`schemas.py`（契约）/ `contracts.py`（字段 Schema）/ `llm.py` / `rules.py` /
`service.py`（编排）/ `store.py`（持久化）/ `main.py`（HTTP 层）。
核心逻辑不依赖 FastAPI，可独立测试与复用。

## Evaluation (honest numbers)

```bash
python scripts/evaluate.py          # 规则引擎，三个数据集
python scripts/evaluate.py --llm    # 追加 LLM 引擎（需 key）
```

规则引擎（无 LLM，纯本地，~0.06ms/篇）：

| 数据集 | 篇数 | 分类准确率 | 说明 |
|---|---|---|---|
| corpus（开发集） | 46 | 100% | 参与关键词调优 |
| holdout（噪声留出） | 26 | 100% | OCR 噪声/中英混排，用于一轮迭代后验证 |
| adversarial（对抗） | 12 | **75%** | 未参与调优；3 个失败案例保留在报告中 |

LLM 引擎（deepseek-chat，实测，含分类+抽取全链路）：

| 指标 | 数值 |
|---|---|
| corpus 分类准确率 | **100%**（46/46） |
| adversarial 分类准确率 | **100%**（12/12，规则引擎失败的 3 篇全部救回） |
| 字段填充率 | **96.8%**（规则引擎 85.5%） |
| 平均延迟 | ~967ms/篇（分类+抽取两次调用） |
| LLM 失败降级次数 | 0 |

对比结论：规则引擎快 4 个数量级但对抗集只有 75%；LLM 补齐间接表达的理解能力，
两者组合 = 成本/延迟/准确率的工程平衡。完整数据见 `eval/report.json`。

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q        # 34 passed
```

覆盖：规则引擎单测、API 集成（含 GB18030 上传/422/404/OpenAPI）、
LLM mock（429 重试、JSON 容错、类别校验、类型转换）、服务降级、缓存命中、
以及「开发集准确率 ≥85%」回归门禁。

## Docker

```bash
docker build -t smartdoc .
docker run -p 8000:8000 -e SMARTDOC_LLM_API_KEY=sk-... -v smartdoc-data:/data smartdoc
```

## Roadmap

- [x] PostgreSQL + Redis 存储/缓存升级（见 [`postgres-redis` 分支](https://github.com/babycat4475/smartdoc/tree/postgres-redis)，接口 parity + fakeredis 测试）
- [ ] 批量异步任务（Celery/ARQ）与 webhook 回调
- [ ] PDF/图片 OCR 前置（pypdf / RapidOCR）
- [ ] 字段级置信度与人工复核队列
- [ ] Prometheus 指标导出

## License

MIT
