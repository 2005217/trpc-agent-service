"""SessionRouter：无状态会话路由。

根据 (tenant_id, channel, external_user_id, chat_id) 稳定生成 session_id，
使任意 Worker 节点都能对同一用户路由到同一会话，无需 sticky session。
"""
from __future__ import annotations

import hashlib

from trpc_service.config.tenant_config import TenantConfig


class SessionRouter:
    """把外部用户身份映射为稳定的内部 session_id。"""

    @staticmethod
    def session_id(tenant_id: str, channel: str, external_user_id: str, chat_id: str = "") -> str:
        """生成稳定 session_id。

        单聊：sha1(tenant:channel:user)[:32]
        群聊：额外拼上 chat_id 以隔离不同群的会话。
        """
        raw = f"{tenant_id}:{channel}:{external_user_id}:{chat_id}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def default_session_id(tenant_config: TenantConfig, user_id: str) -> str:
        """Web 通道的默认会话 id。"""
        channel = "web"
        return SessionRouter.session_id(tenant_config.tenant_id, channel, user_id)
