"""Agent 工厂：根据租户配置构建 LlmAgent。

复用 trpc_agent_sdk 的 OpenAIModel + LlmAgent，按租户组装模型、
提示词、工具集与治理过滤链（白名单/脱敏/预算/二次确认）。
"""
from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import LoadMemoryTool
import trpc_service.filter  # noqa: F401  # 导入即完成过滤器注册
from trpc_service.config.tenant_config import TenantConfig
from trpc_service.tool.functions import build_example_tools

# 挂载到每个工具上的治理过滤链（顺序即执行顺序）
TOOL_FILTER_CHAIN = [
    "tool_whitelist",
    "pii_mask",
    "budget_limit",
    "dangerous_confirm",
]


class AgentFactory:
    """根据租户配置构建 Agent。"""

    @staticmethod
    def create_agent(tenant_config: TenantConfig) -> LlmAgent:
        model = OpenAIModel(
            model_name=tenant_config.model.model_name,
            api_key=tenant_config.model.api_key,
            base_url=tenant_config.model.base_url,
        )
        tools = build_example_tools(tenant_config.app.app_name, filters_name=TOOL_FILTER_CHAIN)
        tools.append(LoadMemoryTool())  # Memory 检索入口：post-turn 写入的记忆靠它读回上下文
        return LlmAgent(
            name=tenant_config.app.app_name,
            description=tenant_config.app.description,
            model=model,
            instruction=tenant_config.app.instruction,
            tools=tools,
        )
