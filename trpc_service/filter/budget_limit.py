"""预算限流过滤器（TOOL 层）。

每次工具执行扣减租户预算；预算由 gateway.budget.BudgetManager 维护，
入口层（web/channels）在 Agent 运行前也会做前置校验。
"""
from __future__ import annotations

from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter

from trpc_service.filter.context import resolve_tenant
from trpc_service.gateway.budget import BudgetExceeded
from trpc_service.gateway.budget import BudgetManager

budget_manager = BudgetManager()


@register_tool_filter("budget_limit")
class BudgetLimitFilter(BaseFilter):
    """工具执行前的租户预算校验。"""

    async def _before(self, ctx, req, rsp):
        tenant = resolve_tenant(ctx)
        if tenant is None:
            return
        try:
            budget_manager.check(tenant.tenant_id)
        except BudgetExceeded as ex:
            rsp.rsp = {
                "error": "budget_exceeded",
                "message": str(ex),
                "status": "blocked",
            }
            rsp.is_continue = False
