# 系统架构设计：多租户节点化 Agent 部署平台

> 基于 tRPC-Agent-Python（trpc_agent_sdk 1.1.19）的多租户、可节点化部署、多后端数据同步、可接入企业微信的生产级 Agent 平台。

## 1. 总体架构

```
                 ┌────────────────────────────────────────────────┐
   Web UI ─────► │                Agent Gateway                    │
   企业微信 ────► │  路由 / 预算前置校验 / 用户权限 / 验签解密去重    │
                 └───────────────┬────────────────────────────────┘
                                 │ SessionRouter（无状态路由）
                 ┌───────────────▼────────────────────────────────┐
                 │                Agent Worker                     │
                 │  Runner + LlmAgent + Tool Filter 治理链         │
                 │  （白名单 / 脱敏 / 预算 / 危险确认）             │
                 └──────┬─────────────────┬───────────────────────┘
                        │                 │
              ┌─────────▼───────┐ ┌───────▼────────────┐
              │ Storage Adapter │ │  Telemetry (OTel)  │
              │ InMemory/Redis/ │ │  trace + gen_ai 指标│
              │ SQL Session/    │ └───────┬────────────┘
              │ Memory 服务     │         │
              └─────────┬───────┘         │
                        ▼                 ▼
                 Redis / SQL（MySQL）   OTLP Collector / Jaeger
                        ▲
              ┌─────────┴─────────┐
              │ Audit 审计服务     │（JSONL 兜底 / SQL 表）
              └───────────────────┘
```

### 组件职责

| 组件 | 职责 | 代码位置 |
|------|------|----------|
| Agent Gateway | 统一入口：会话路由、预算前置校验、IM 验签解密去重、用户权限 | `web/` + `gateway/` + `channels/` |
| Agent Worker | 无状态执行体：Runner + Agent + 治理 Filter，可水平扩展 | `agent/` + `filter/` |
| Channel Adapter | IM 协议适配（第一阶段 Web、第二阶段企业微信） | `channels/` |
| Storage Adapter | Session/Memory 三后端装配（InMemory/Redis/SQL） | `storage/factory.py` |
| Admin API | 租户 CRUD、审计查询 | `web/app.py` |
| Telemetry Collector | trace/指标收集上报 | `telemetry/` + `deploy/otel-config.yaml` |
| Audit 审计 | 每请求审计落盘（脱敏后） | `audit/` |

## 2. 多租户与节点部署

### 2.1 租户模型

租户配置覆盖：应用配置（app_name/提示词）、模型配置（provider/model_name/api_key/base_url）、工具权限（allowed/blocked）、IM 通道配置（wecom 的 token/corp_id/encoding_aes_key）、数据后端配置（session_backend/redis_url/sql_url）、审计策略（mask_pii/retention）。密钥**只经环境变量注入**，不写入配置文件与日志。

### 2.2 无状态路由（无 sticky session）

`SessionRouter` 用 `sha1(tenant:channel:user:chat)[:32]` 生成稳定 session_id。任意节点收到同一用户消息都能推导出同一 session_id，配合共享 Session 后端（Redis/SQL）实现**无 sticky 的无状态 Worker**：会话状态全部在存储后端，节点本地只缓存。

### 2.3 租户隔离

- **配置隔离**：每租户独立 TenantConfig，ConfigManager 按租户装配 Runner。
- **数据隔离**：框架 Session/Memory 的 key 为 `{app_name}/{user_id}`，app_name 绑定租户，天然按租户切分。
- **工具权限隔离**：TOOL 层白名单 Filter 按 AgentContext 中的 tenant_id 校验。
- **日志脱敏与密钥管理**：审计/日志入库前经 `audit/redact`（键名匹配整值遮蔽 + PII 打码）；api_key/token 不出现在日志、trace 与错误报告。

## 3. 复用 tRPC-Agent-Python vs 平台新增

| 能力 | 来源 |
|------|------|
| Runner/RunConfig/Event 流、LlmAgent、OpenAIModel | 框架直接复用 |
| Session/Memory 三后端（InMemory/Redis/SQL） | 框架直接复用，平台封装为 StorageAdapter |
| Filter 机制（BaseFilter + register_*_filter + filters_name） | 框架机制复用，白名单/脱敏/预算/二次确认为平台实现 |
| OTel 埋点（invocation/call_llm/execute_tool span + gen_ai 指标） | 框架自动埋点，平台补 OTLP exporter 初始化 |
| FunctionTool / MCPToolset / LoadMemoryTool | 框架直接复用 |
| 租户模型/配置热加载、Session 路由、预算、审计、企微协议、Admin API | 平台新增 |

## 4. 治理、监控和安全

治理过滤链（顺序执行）：`tool_whitelist` → `pii_mask` → `budget_limit` → `dangerous_confirm`，均以 TOOL Filter 实现，租户差异从 AgentContext 元数据解析；IM 用户权限校验在网关层（channel_binding）执行。

监控：框架自动产出 invocation/call_llm/execute_tool span 与 gen_ai 指标；平台补齐 OTLP exporter（`OTEL_ENABLED=1` 开关）与审计指标。trace_id 由入口生成，注入 AgentContext 元数据贯穿全链路（见 `docs/mermaid/sequence.mermaid`），并写回审计记录。

审计字段（验收标准）：`tenant_id / channel / user_id / session_id / agent_name / tool_name / decision / latency / error_type / cost / trace_id`。

## 5. IM 接入（两阶段）

- **第一阶段（已实现）**：Web UI。`POST /api/v1/chat` 同步返回聚合回复，`session_id` 维持多轮上下文。
- **第二阶段（已实现协议逻辑，模拟验证）**：企业微信。`channels/wecom.py` 实现官方协议：URL 验证（echostr 解密）、SHA1 验签、AES-256-CBC 解密、MsgId 去重、`FromUserName`→session 身份映射、群聊拼 ChatId 隔离、加密被动回复与超长分片。单聊 session=`sha1(tenant:wecom:user)`；群聊追加 ChatId。
- **IM 平台限制应对**：长度限制（1800 字分片，后续片走主动推送）、重复投递（Deduper）、失败重试（企微 5 秒重试窗口内快速返回 ACK）、图片/文件（InboundMessage 预留 raw 字段，二期实现）。

## 6. 故障恢复与运维

- **降级**：模型超时/错误 → 统一兜底话术 + 审计 error_type；数据库短暂不可用 → 文件审计兜底；工具失败 → function_response 错误回传由模型自解释。
- **灰度与回滚**：tenants.yaml mtime 热加载；租户配置版本化（tenant_config 表），回滚即切换版本重建 Runner。
- **容量评估**：每节点并发 session 数受事件循环与模型并发限制；Redis QPS ≈ 请求 QPS ×（session 读写 + memory 读写）；IM 回调峰值按企微 5 秒重试反推。
- **部署**：最小方案 = 单进程 `python -m trpc_service.web.app`（InMemory）；生产方案 = `deploy/docker-compose.yml`（gateway + worker 同镜像异命令、Redis、MySQL、OTel Collector），K8s 部署 gateway/worker Deployment + HPA，存储用云 Redis/MySQL。

## 7. 数据同步与多后端

详见 `docs/sync-and-idempotency.md`、`docs/backend-adapter.md`、`docs/data-model.md`。要点：append 语义 + 版本 CAS 解决并发写；event→state→summary 顺序固定；Memory 写后读依赖后端 flush；Redis→SQL 迁移 = 停写→scan 全量→schema 转换导入→切读。
