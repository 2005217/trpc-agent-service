"""StorageAdapter：按租户选择 InMemory / Redis / SQL 后端。

直接复用 trpc_agent_sdk 的 SessionService / MemoryService 三实现，
本模块只负责根据 TenantConfig.storage 装配并持有引用，
保证同一租户在进程内共享同一服务实例。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SqlSessionService

from trpc_service.config.tenant_config import StorageBackend, TenantConfig


@dataclass
class StorageAdapter:
    """一个租户的会话/记忆服务组合。"""

    session_service: BaseSessionService
    memory_service: Optional[BaseMemoryService] = None
    backend: str = StorageBackend.IN_MEMORY

    async def close(self) -> None:
        if self.memory_service is not None:
            try:
                await self.memory_service.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            await self.session_service.close()
        except Exception:  # noqa: BLE001
            pass


def create_storage(tenant_config: TenantConfig) -> StorageAdapter:
    """按租户存储配置装配 Session/Memory 服务。"""
    backend = tenant_config.storage.session_backend
    if backend == StorageBackend.REDIS:
        url = tenant_config.storage.redis_url
        if not url:
            raise ValueError(f"租户 {tenant_config.tenant_id} 选择 Redis 后端但未配置 redis_url")
        return StorageAdapter(
            session_service=RedisSessionService(db_url=url, is_async=True),
            memory_service=RedisMemoryService(db_url=url, enabled=True, is_async=True),
            backend=backend,
        )
    if backend == StorageBackend.SQL:
        url = tenant_config.storage.sql_url
        if not url:
            raise ValueError(
                f"租户 {tenant_config.tenant_id} 选择 SQL 后端但未配置 sql_url"
                "（在 tenants.yaml 或环境变量中填写，如 mysql+pymysql://user:pass@host/db）"
            )
        return StorageAdapter(
            session_service=SqlSessionService(db_url=url, is_async=True),
            memory_service=SqlMemoryService(db_url=url, enabled=True, is_async=True),
            backend=backend,
        )
    # 默认内存后端
    return StorageAdapter(
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(enabled=True),
        backend=StorageBackend.IN_MEMORY,
    )
