# 生产风险清单（12 项）

| # | 风险 | 影响 | 缓解措施 |
|---|------|------|---------|
| 1 | **多节点并发写同一 session 冲突** | 会话历史错乱、state 互相覆盖 | append + seq 唯一索引；state 版本 CAS + delta 合并重试；同一 session 前端串行化 |
| 2 | **IM 消息重复投递/乱序** | 重复执行工具、重复扣预算、回复错乱 | 三层幂等（内存 TTL → Redis SETNX → SQL 唯一索引）；按 CreateTime+MsgId 丢弃过期消息 |
| 3 | **模型 API key / IM secret 泄漏** | 资损与安全事件 | 密钥只经环境变量注入；日志/trace/审计入库前 redact（键名整值遮蔽 + PII 打码）；错误报告统一脱敏 |
| 4 | **单租户预算耗尽/刷量** | 全局资源被挤占、超额账单 | 网关前置校验 + TOOL 层二次拦截双层预算；daily_api_calls / daily_token_budget 按租户封顶；超限 429 |
| 5 | **单点 Redis / SQL 故障** | 全部租户会话不可用 | Redis 哨兵/集群；SQL 主从；审计 JSONL 文件兜底保证审计不丢；网关对存储超时快速失败 + 兜底话术 |
| 6 | **模型超时/限流/错误率飙升** | 回复失败率上升 | 统一兜底话术 + error_type 审计归因；RunConfig.max_llm_calls 防止无限循环；按租户降级到备用模型（model 配置可切换） |
| 7 | **危险工具误执行** | 业务数据被误改/误删 | dangerous_confirm 过滤器强制二次确认（confirm=true 或会话内确认）；危险名单独立维护；审计记录 decision |
| 8 | **PII 进入日志/trace/对话** | 合规风险 | mask_pii 租户开关 + 正则打码（手机/身份证/邮箱/密钥）；审计与输出双侧脱敏；trace 属性不含原文 |
| 9 | **配置热加载错误配置全网生效** | 全部租户异常 | YAML 加载失败保持旧配置；租户配置版本化（tenant_config 表）支持回滚；Admin API 变更留痕 |
| 10 | **企微回调验签/解密被绕过** | 伪造消息注入 | 官方 SHA1 验签 + AES-256-CBC 全量实现并做协议回环测试；签名不符一律 403 |
| 11 | **trace 无法串联定位问题** | 故障排查时长不可控 | trace_id 入口生成注入 AgentContext，贯穿 gateway→runner→tool→存储→IM 回复并写回审计；OTLP 上报 Jaeger |
| 12 | **节点故障时在途请求丢失** | 用户感知回复丢失 | IM 场景依赖企微 5 秒重试机制（快速 ACK + 异步执行可扩展为队列）；Web 场景同步请求失败即报错可重试；Worker 无状态，重启无恢复成本 |

## 补充说明

- 风险 1/6 的「降级」与风险 9 的「回滚」联动：配置回滚后 Runner 热重建，秒级生效。
- 风险 4 的多节点计数：MVP 为进程内存计数，多节点部署时替换为 Redis INCRBY（见 `docs/sync-and-idempotency.md`）。
- 风险 12 的异步队列方案：将 webhook 处理改为「立即 ACK + 消息队列 + Worker 消费」，为后续演进而非当前 MVP 范围。
