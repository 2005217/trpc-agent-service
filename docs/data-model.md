# 数据模型设计

## 1. 设计原则

- 运行期 Session/Memory 由框架 `SqlSessionService / SqlMemoryService / RedisSessionService / RedisMemoryService` 自管理（框架内建 schema）；平台侧维护**租户元数据、通道绑定、审计、幂等**四类表。
- 框架检索 key 固定为 `{app_name}/{user_id}`，租户隔离通过 app_name 前缀表达。
- 全部表 MySQL 8 / InnoDB / utf8mb4；Redis 侧以同名 key 结构做热数据。

## 2. 表结构（SQL DDL）

### tenant — 租户
```sql
CREATE TABLE tenant (
  tenant_id      VARCHAR(36) PRIMARY KEY,
  name           VARCHAR(128) NOT NULL,
  status         VARCHAR(16) NOT NULL DEFAULT 'active',
  config_json    JSON NOT NULL,            -- TenantConfig 序列化
  config_version INT NOT NULL DEFAULT 1,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_tenant_version (tenant_id, config_version)
);
```

### tenant_config — 配置版本（灰度/回滚）
```sql
CREATE TABLE tenant_config (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id   VARCHAR(36) NOT NULL,
  version     INT NOT NULL,
  config_json JSON NOT NULL,
  created_by  VARCHAR(64) NOT NULL DEFAULT 'admin',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_tenant_version (tenant_id, version)
);
```

### app — 租户下的 Agent 应用
```sql
CREATE TABLE app (
  tenant_id     VARCHAR(36) NOT NULL,
  app_name      VARCHAR(64) NOT NULL,      -- 框架 Session key 前缀
  instruction   TEXT NOT NULL,
  model_provider VARCHAR(32) NOT NULL DEFAULT 'openai',
  model_name    VARCHAR(64) NOT NULL,
  PRIMARY KEY (tenant_id, app_name)
);
```

### session — 会话（逻辑视图；运行期由框架服务承载）
```sql
CREATE TABLE session (
  session_id    VARCHAR(64) PRIMARY KEY,   -- sha1(tenant:channel:user[:chat])[:32]
  tenant_id     VARCHAR(36) NOT NULL,
  app_name      VARCHAR(64) NOT NULL,
  user_id       VARCHAR(64) NOT NULL,
  state_json    JSON,
  event_count   INT NOT NULL DEFAULT 0,
  last_update_time DATETIME,
  INDEX idx_session_user (tenant_id, app_name, user_id),
  INDEX idx_session_time (last_update_time)
);
```

### message — 消息/事件
```sql
CREATE TABLE message (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id  VARCHAR(64) NOT NULL,
  seq         INT NOT NULL,
  author      VARCHAR(64) NOT NULL,        -- user / agent 名
  content_json JSON NOT NULL,
  trace_id    CHAR(32),
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_session_seq (session_id, seq),
  INDEX idx_msg_trace (trace_id)
);
```

### memory — 长期记忆（key = "{app_name}/{user_id}"）
```sql
CREATE TABLE memory (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id  VARCHAR(36) NOT NULL,
  mem_key    VARCHAR(160) NOT NULL,        -- {app_name}/{user_id}
  content    TEXT NOT NULL,
  embedding  JSON NULL,                    -- 向量库迁移预留
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_memory_key (mem_key)
);
```

### summary — 会话摘要
```sql
CREATE TABLE summary (
  session_id   VARCHAR(64) PRIMARY KEY,
  summary_text TEXT NOT NULL,
  model        VARCHAR(64),
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### channel_binding — IM 账号与租户绑定
```sql
CREATE TABLE channel_binding (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id        VARCHAR(36) NOT NULL,
  channel_type     VARCHAR(16) NOT NULL,   -- web / wecom
  external_user_id VARCHAR(128) NOT NULL,  -- 企微 UserId
  chat_id          VARCHAR(128) NOT NULL DEFAULT '',  -- 群聊 id
  session_id       VARCHAR(64) NOT NULL,
  status           VARCHAR(16) NOT NULL DEFAULT 'active',
  UNIQUE KEY uk_binding (tenant_id, channel_type, external_user_id, chat_id),
  INDEX idx_binding_session (session_id)
);
```

### audit_log — 审计日志
```sql
CREATE TABLE audit_log (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id   VARCHAR(36) NOT NULL,
  channel     VARCHAR(16) NOT NULL,
  user_id     VARCHAR(64),
  session_id  VARCHAR(64),
  agent_name  VARCHAR(64),
  tool_name   VARCHAR(128),
  decision    VARCHAR(16) NOT NULL,        -- allow / block / error
  latency_ms  INT NOT NULL DEFAULT 0,
  error_type  VARCHAR(64) NOT NULL DEFAULT '',
  cost        DECIMAL(10,4) NOT NULL DEFAULT 0,
  trace_id    CHAR(32),
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_audit_tenant_time (tenant_id, created_at),
  INDEX idx_audit_trace (trace_id)
);
```

### idempotency — 幂等去重
```sql
CREATE TABLE idempotency (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  idempotency_key VARCHAR(128) NOT NULL,   -- {channel}:{external_msg_id}
  dedupe_status   VARCHAR(16) NOT NULL DEFAULT 'processed',
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_idem (idempotency_key)
);
```

## 3. Redis 键结构（热数据）

| 键 | 值 | 说明 |
|----|----|------|
| `session:{app}:{user}:{session_id}` | hash | 框架 RedisSessionService 管理 |
| `memory:{app}:{user}` | list/zset | 框架 RedisMemoryService 管理 |
| `idem:{channel}:{msg_id}` | string, SETNX + TTL 300s | 多节点消息去重 |
| `budget:{tenant}:{yyyyMMdd}` | hash(api_calls/tokens), INCRBY | 多节点预算计数 |
| `binding:{channel}:{external_user}` | hash | 用户-租户绑定缓存 |

## 4. JSON Schema 层面对照

TenantConfig（tenants.yaml ↔ config_json）字段：`tenant_id / name / status / app{app_name,description,instruction} / model{provider,model_name,api_key,base_url} / storage{session_backend,redis_url,sql_url} / channels{wecom{enabled,bot_id,secret,token,corp_id,encoding_aes_key}} / tools{allowed_tools,blocked_tools} / audit{enabled,mask_pii,retention_days} / daily_api_calls / daily_token_budget`。

## 5. 实体关系

```
tenant 1─n app 1─n session 1─n message
tenant 1─n channel_binding（external_user_id → session_id）
session 1─1 summary
tenant 1─n memory（mem_key = {app}/{user}）
tenant 1─n audit_log；message/audit_log 均携带 trace_id
```
