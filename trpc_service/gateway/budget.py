"""租户预算管理器。

按租户统计每日调用次数与 token 消耗，超过 daily_api_calls /
daily_token_budget 时拒绝请求。单进程内存实现；多节点部署时
由 Redis 原子计数替换（文档见 docs/sync-and-idempotency.md）。
"""
from __future__ import annotations

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
    """进程内租户预算计数器。"""

    def __init__(self) -> None:
        self._usage: Dict[str, _Usage] = {}

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

    def check(self, tenant_id: str, tokens: int = 0) -> None:
        """检查是否超限，超限抛 BudgetExceeded。"""
        tenant = config_manager.get(tenant_id)
        usage = self._bucket(tenant_id)
        limits_calls = tenant.daily_api_calls if tenant else 10000
        limits_tokens = tenant.daily_token_budget if tenant else 1000000
        if usage.api_calls >= limits_calls:
            raise BudgetExceeded(tenant_id, "daily_api_calls")
        if usage.tokens + tokens > limits_tokens:
            raise BudgetExceeded(tenant_id, "daily_token_budget")

    def record(self, tenant_id: str, api_calls: int = 1, tokens: int = 0) -> None:
        """记录一次用量。"""
        usage = self._bucket(tenant_id)
        usage.api_calls += api_calls
        usage.tokens += tokens

    def usage_of(self, tenant_id: str) -> _Usage:
        return self._bucket(tenant_id)

    def reset(self, tenant_id: Optional[str] = None) -> None:
        if tenant_id:
            self._usage.pop(tenant_id, None)
        else:
            self._usage.clear()
