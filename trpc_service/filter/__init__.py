"""平台治理过滤链：白名单、脱敏、预算、二次确认、用户权限。

复用 trpc_agent_sdk 的 BaseFilter + register_*_filter 机制；
所有过滤器实例为进程级单例，租户差异通过 AgentContext 元数据解析。
导入本包即完成全部过滤器注册。
"""
from trpc_service.filter import budget_limit  # noqa: F401
from trpc_service.filter import dangerous_confirm  # noqa: F401
from trpc_service.filter import pii_mask  # noqa: F401
from trpc_service.filter import tool_whitelist  # noqa: F401
from trpc_service.filter.context import META_CHANNEL  # noqa: F401
from trpc_service.filter.context import META_SESSION  # noqa: F401
from trpc_service.filter.context import META_TENANT  # noqa: F401
from trpc_service.filter.context import META_TRACE  # noqa: F401
from trpc_service.filter.context import META_USER  # noqa: F401

__all__ = ["META_TENANT", "META_USER", "META_CHANNEL", "META_TRACE", "META_SESSION"]
