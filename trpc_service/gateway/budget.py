"""租户预算管理器。

按租户统计每日调用次数与 token 消耗，超过 daily_api_calls /
daily_token_budget 时拒绝请求。

后端双模：
- Redis（主）：配置 BUDGET_REDIS_URL 时启用，INCRBY 原子计数，多节点一致
- 内存（降级）：Redis 未配置/不可用时回退；单节点正确，多节点配额近似

SQL 不做计数后端——热路径临时计数与 SQL 的持久化/查询定位不符；
成本报表由定时快照/审计聚合落表实现（见 docs/backend-adapter.md §3.1）。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from trpc_service.config.registry import config_manager


@dataclass
class _Usage:
    day: str = ""
    api_calls: int = 0
    tokens: int = 0
    extra: Dict[str, float] = field(default_factory=dict)


class BudgetExceeded(Exception):
    """租户预算超限。"""

    def __init__(self, tenant_id: str, reason: str):
        self.tenant_id = tenant_id
        self.reason = reason
        super().__init__(f"租户 {tenant_id} 预算超限: {reason}")


class BudgetManager:
    """租户预算计数器（Redis 主 / 内存降级双后端）。

    接口（check/record/usage_of/reset）与内存版完全一致，调用方零改动。
    """

    def __init__(self) -> None:
        self._usage: Dict[str, _Usage] = {}   # 备用账本：Redis 不可用时的降级路径
        self._redis = None                    # None=内存模式；有客户端=Redis 模式
        url = os.getenv("BUDGET_REDIS_URL")
        if url:
            import redis
            try:
                self._redis = redis.Redis.from_url(
                    url,
                    decode_responses=True,    # 缺省时 GET 返回 bytes，int() 会炸
                    socket_connect_timeout=2,  # 探活最多等 2 秒，别让启动卡死
                )
                self._redis.ping()            # 构造期探活：现在就试一枪
            except Exception:
                # 降级取舍：可用性 > 严格配额。多节点无 Redis 时各节点独立
                # 计数，配额暂时变松（N 倍），但服务不中断。
                self._redis = None

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    def _bucket(self, tenant_id: str) -> _Usage:
        today = self._today()
        usage = self._usage.get(tenant_id)
        if usage is None or usage.day != today:
            usage = _Usage(day=today)
            self._usage[tenant_id] = usage
        return usage

    @staticmethod
    def _limits_of(tenant_id: str) -> tuple[int, int]:
        """限额读取的唯一来源（Redis/内存两条分支共用，避免逻辑存在两份）。"""
        tenant = config_manager.get(tenant_id)
        return (
            tenant.daily_api_calls if tenant else 10000,
            tenant.daily_token_budget if tenant else 1000000,
        )

    @staticmethod
    def _redis_keys(tenant_id: str) -> tuple[str, str, str]:
        """构造预算键：budget:{租户}:{日期}:{字段}。

        日期拼入键名实现按天记账；TTL(48h) 由 record() 在写入时设置——
        本函数只负责键名，不做任何 Redis 操作。
        """
        base = f"budget:{tenant_id}:{time.strftime('%Y-%m-%d', time.localtime())}"
        return base, f"{base}:calls", f"{base}:tokens"

    def check(self, tenant_id: str, tokens: int = 0) -> None:
        """检查是否超限，超限抛 BudgetExceeded。"""
        limits_calls, limits_tokens = self._limits_of(tenant_id)
        if self._redis is not None:
            # 竞态窗口：check(GET) 与 record(INCR) 是两步，多节点并发下
            # 可能瞬时少量超放，最终计数准确；严格零超放需用 Lua 把
            # 读-比-写合成原子操作，MVP 接受此取舍。
            _, calls_key, tokens_key = self._redis_keys(tenant_id)
            used_calls = int(self._redis.get(calls_key) or 0)   # or 0：键不存在时 GET 返回 None
            used_tokens = int(self._redis.get(tokens_key) or 0)
        else:
            usage = self._bucket(tenant_id)
            used_calls, used_tokens = usage.api_calls, usage.tokens
        # 判断逻辑只有一份，Redis/内存仅"读数来源"不同
        if used_calls >= limits_calls:
            raise BudgetExceeded(tenant_id, "daily_api_calls")
        if used_tokens + tokens > limits_tokens:
            raise BudgetExceeded(tenant_id, "daily_token_budget")

    def record(self, tenant_id: str, api_calls: int = 1, tokens: int = 0) -> None:
        """记录一次用量。"""
        if self._redis is not None:
            _, calls_key, tokens_key = self._redis_keys(tenant_id)
            pipe = self._redis.pipeline()      # 三条命令攒一批，一次网络往返
            pipe.incrby(calls_key, api_calls)  # INCRBY：服务端原子累加，多节点不丢计数
            pipe.incrby(tokens_key, tokens)
            # 两个键都必须设 TTL：漏掉任意一个，该键永久残留导致 Redis
            # 内存按天累积泄漏（review 发现的 bug）。
            pipe.expire(calls_key, 172800)     # 48h 生命周期，跨天自然滚动
            pipe.expire(tokens_key, 172800)
            pipe.execute()                     # 不调用 execute 等于什么都没发
            return                             # 别忘：否则落到内存分支 = 双记账
        usage = self._bucket(tenant_id)
        usage.api_calls += api_calls
        usage.tokens += tokens

    def usage_of(self, tenant_id: str) -> _Usage:
        if self._redis is not None:
            _, calls_key, tokens_key = self._redis_keys(tenant_id)
            return _Usage(                     # 返回类型保持 _Usage，调用方不用分叉
                day=self._today(),
                api_calls=int(self._redis.get(calls_key) or 0),
                tokens=int(self._redis.get(tokens_key) or 0),
            )
        return self._bucket(tenant_id)

    def reset(self, tenant_id: Optional[str] = None) -> None:
        if self._redis is not None:
            if tenant_id:
                _, calls_key, tokens_key = self._redis_keys(tenant_id)
                self._redis.delete(calls_key, tokens_key)  # 两个存储都不能留旧账
            else:
                # 诚实设计：全量清空需扫描 budget:*，属于清理阶段的活，
                # 与其假装支持不如显式挡住
                raise NotImplementedError("Redis 模式暂不支持全量 reset，请按租户重置")
            return
        if tenant_id:
            self._usage.pop(tenant_id, None)
        else:
            self._usage.clear()
