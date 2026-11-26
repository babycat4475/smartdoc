# postgres-redis 分支：存储与缓存纵深

主分支用 SQLite + 进程内缓存表（零依赖、单机够用）。本分支演示生产化路径：

## 变更

| 组件 | 主分支 | 本分支 |
|---|---|---|
| 文档/事件存储 | `app/store.py` (sqlite3) | `app/pg_store.py` (psycopg2, PostgreSQL) |
| 结果缓存 | SQLite cache 表 | `app/redis_cache.py` (Redis, TTL 24h) |
| 服务层 | SmartDocService | **不变**——Store 是唯一触库层，接口一致即插即换 |

## 关键设计

- **接口 parity**：PgStore 与 Store 方法签名完全一致，`SmartDocService` 零改动
- **SQL 可移植**：去掉 SQLite 方言（`AUTOINCREMENT`→`SERIAL`，`INSERT OR REPLACE`→`ON CONFLICT DO UPDATE`），有静态检查测试守住
- **CachedStore 组合器**：文档走 PG、缓存走 Redis，一个 `__getattr__` 委托搞定
- **Redis TTL**：多副本部署下缓存一致失效，SQLite 表做不到

## 测试

```bash
pip install psycopg2-binary redis fakeredis
pytest -q          # 38 passed, 1 skipped
```

- Redis 路径用 **fakeredis** 全语义测试（缓存往返 / TTL / 服务层命中）
- PG parity 测试默认 skip，设 `SMARTDOC_TEST_PG_DSN=postgresql://...` 后对真库跑双实现一致性

## 切换方式

```python
from app.pg_store import PgStore
from app.redis_cache import RedisCache, CachedStore

store = CachedStore(PgStore("postgresql://user:pass@host/smartdoc"), RedisCache())
service = SmartDocService(settings, store)   # 其余不变
```

## docker-compose 一键起依赖

```yaml
services:
  db:
    image: postgres:16
    environment: { POSTGRES_PASSWORD: dev, POSTGRES_DB: smartdoc }
    ports: ["5432:5432"]
  cache:
    image: redis:7
    ports: ["6379:6379"]
```
