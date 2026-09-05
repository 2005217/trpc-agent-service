"""工具白名单过滤器（TOOL 层）。

按租户 tools.allowed_tools / blocked_tools 拦截越权工具调用：
- blocked_tools 命中 → 拦截
- allowed_tools 非空且不在名单 → 拦截
- allowed_tools 为空 → 除 blocked 外全放行
"""
from __future__ import annotations

from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter

from trpc_service.filter.context import current_tool_name
from trpc_service.filter.context import resolve_tenant


@register_tool_filter("tool_whitelist")
class ToolWhitelistFilter(BaseFilter):
    """租户级工具白名单。"""

    async def _before(self, ctx, req, rsp):
        tenant = resolve_tenant(ctx)
        if tenant is None:
            return
        name = current_tool_name()
        if not name:
            return
        if name in tenant.tools.blocked_tools:
            rsp.rsp = {
                "error": "tool_blocked",
                "message": f"工具 {name} 已被租户禁用",
                "status": "blocked",
            }
            rsp.is_continue = False
            return
        if tenant.tools.allowed_tools and name not in tenant.tools.allowed_tools:
            rsp.rsp = {
                "error": "tool_not_allowed",
                "message": f"工具 {name} 不在租户可用工具名单中",
                "status": "blocked",
            }
            rsp.is_continue = False
