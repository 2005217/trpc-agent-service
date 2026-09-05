"""跨模块共享的进程级单例注册表。

Filter / 路由等组件需要按 tenant_id 查配置与预算状态，
统一从这里获取，避免各模块各自初始化。
"""
from __future__ import annotations

from trpc_service.config.manager import ConfigManager

config_manager = ConfigManager()
