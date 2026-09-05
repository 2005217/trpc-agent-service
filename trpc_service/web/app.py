"""FastAPI 入口：Web UI 聊天 + Admin API + 审计。

运行方式：
    .venv/Scripts/python.exe -m trpc_service.web.app
"""
from __future__ import annotations
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trpc_service.agent.factory import AgentFactory
from trpc_service.agent.runner import AgentRunner
from trpc_service.audit.model import AuditEvent
from trpc_service.audit.service import audit_service
from trpc_service.channels.base import WebhookRequest
from trpc_service.channels.wecom import WeComAdapter
from trpc_service.config.manager import ConfigManager
from trpc_service.config.settings import ServerConfig
from trpc_service.filter.budget_limit import budget_manager
from trpc_service.filter.pii_mask import mask_text
from trpc_service.gateway.budget import BudgetExceeded
from trpc_service.gateway.router import SessionRouter
from trpc_service.storage.factory import create_storage
from trpc_service.telemetry.context import build_agent_context
from trpc_service.version import __version__

STATIC_DIR = Path(__file__).resolve().parent / "static"


class AppState:
    """进程级共享状态：配置管理器 + 各租户 Runner + 存储适配。"""

    def __init__(self) -> None:
        self.config_manager = ConfigManager()
        self.runners: Dict[str, AgentRunner] = {}

    def build_runner(self, tenant_id: str) -> Optional[AgentRunner]:
        """按租户配置装配 Agent + 存储后端并构建 Runner。"""
        tenant_config = self.config_manager.get(tenant_id)
        if not tenant_config:
            return None
        agent = AgentFactory.create_agent(tenant_config)
        storage = create_storage(tenant_config)
        return AgentRunner(
            app_name=tenant_config.app.app_name,
            agent=agent,
            session_service=storage.session_service,
            memory_service=storage.memory_service,
        )


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from trpc_service.telemetry.setup import setup_telemetry

    setup_telemetry()  # OTEL_ENABLED=1 时生效，否则 no-op

    # 启动时执行一次审计保留期清理（周期任务/多节点守卫：已规划，暂缓）
    retention_map = {
        t.tenant_id: t.audit.retention_days
        for t in state.config_manager.all().values()
    }
    audit_service.cleanup(retention_map)

    for tenant_id in state.config_manager.all():
        runner = state.build_runner(tenant_id)
        if runner:
            state.runners[tenant_id] = runner
    yield
    for runner in state.runners.values():
        await runner.close()


app = FastAPI(title="trpc agent service", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    tenant_id: str
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    trace_id: str


@app.post("/api/v1/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    """Web 聊天接口：预算前置校验 → Agent 执行 → 脱敏 + 审计落盘。"""
    tenant_config = state.config_manager.get(req.tenant_id)
    if not tenant_config:
        raise HTTPException(status_code=404, detail=f"租户不存在: {req.tenant_id}")
    runner = state.runners.get(req.tenant_id)
    if not runner:
        raise HTTPException(status_code=503, detail=f"租户 {req.tenant_id} 的 Agent 未就绪")

    user_id = req.user_id or "web_user"
    session_id = req.session_id or SessionRouter.default_session_id(tenant_config, user_id)
    agent_context = build_agent_context(
        tenant_id=req.tenant_id,
        user_id=user_id,
        session_id=session_id,
        channel="web",
    )
    trace_id = agent_context.get_metadata("trace_id")

    # 预算前置校验（工具层还有二次拦截）
    try:
        budget_manager.check(req.tenant_id)
    except BudgetExceeded as ex:
        audit_service.emit(
            AuditEvent(
                tenant_id=req.tenant_id, channel="web", user_id=user_id, session_id=session_id,
                agent_name=runner.app_name, decision="block", error_type="budget_exceeded",
                trace_id=trace_id,
            )
        )
        raise HTTPException(status_code=429, detail=str(ex))

    start = time.monotonic()
    outcome = await runner.run(
        user_id=user_id, session_id=session_id, message=req.message, agent_context=agent_context
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    # 记录预算用量（token 以字符数粗估，接入 usage 后精确化）
    budget_manager.record(req.tenant_id, api_calls=1, tokens=len(req.message) + len(outcome.text))

    # 审计落盘（密钥/PII 由 redact 处理）
    audit_service.emit(
        AuditEvent(
            tenant_id=req.tenant_id,
            channel="web",
            user_id=user_id,
            session_id=session_id,
            agent_name=runner.app_name,
            tool_name=",".join(c.get("name", "") for c in outcome.tool_calls),
            decision="error" if outcome.error_type else "allow",
            latency_ms=latency_ms,
            error_type=outcome.error_type,
            trace_id=trace_id,
        )
    )

    # 结构化日志（键值对文本，可按 tenant/trace grep）
    from trpc_service.log import get_logger
    get_logger("web.chat").info(
        "chat done tenant=%s trace=%s tool=%s latency=%sms error=%s",
        req.tenant_id,
        trace_id,
        outcome.tool_calls[0]["name"] if outcome.tool_calls else "-",
        latency_ms,
        outcome.error_type or "-",
    )

    # 输出脱敏
    reply = mask_text(outcome.text) if tenant_config.audit.mask_pii else outcome.text
    if outcome.error_type and not reply:
        reply = f"服务暂时不可用（{outcome.error_type}），请稍后重试。"
    return ChatResponse(reply=reply, session_id=session_id, trace_id=trace_id)


class TenantInfo(BaseModel):
    tenant_id: str
    name: str
    status: str
    app_name: str


@app.get("/api/v1/tenants")
async def list_tenants() -> List[TenantInfo]:
    """租户列表。"""
    return [
        TenantInfo(tenant_id=t.tenant_id, name=t.name, status=t.status, app_name=t.app.app_name)
        for t in state.config_manager.all().values()
    ]


class TenantCreateRequest(BaseModel):
    tenant_id: str
    name: str
    instruction: str = "你是一个有帮助的助手。"
    app_name: Optional[str] = None


@app.post("/api/v1/tenants")
async def create_tenant(req: TenantCreateRequest) -> TenantInfo:
    """创建租户并即时装配 Runner（Admin API）。"""
    if state.config_manager.get(req.tenant_id):
        raise HTTPException(status_code=409, detail=f"租户已存在: {req.tenant_id}")
    from trpc_service.config.tenant_config import TenantConfig

    tenant = TenantConfig(
        tenant_id=req.tenant_id,
        name=req.name,
        app={"app_name": req.app_name or req.tenant_id, "instruction": req.instruction},
    )
    state.config_manager.register(tenant)
    runner = state.build_runner(req.tenant_id)
    if runner:
        state.runners[req.tenant_id] = runner
    return TenantInfo(
        tenant_id=tenant.tenant_id, name=tenant.name, status=tenant.status, app_name=tenant.app.app_name
    )


@app.delete("/api/v1/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str) -> dict:
    """删除租户并下线其 Runner 与通道适配器。"""
    if not state.config_manager.get(tenant_id):
        raise HTTPException(status_code=404, detail=f"租户不存在: {tenant_id}")
    state.config_manager.remove(tenant_id)
    runner = state.runners.pop(tenant_id, None)
    if runner:
        await runner.close()
    adapter = _wecom_adapters.pop(tenant_id, None)
    if adapter:
        adapter.deduper.clear()
    return {"deleted": tenant_id}


@app.get("/api/v1/audit")
async def query_audit(limit: int = 20) -> List[dict]:
    """最近审计记录（脱敏后返回）。"""
    return audit_service.tail(limit=min(max(limit, 1), 200))


# ---- 企业微信通道（第二阶段） ----

_wecom_adapters: Dict[str, WeComAdapter] = {}


def _get_wecom_adapter(tenant_id: str) -> WeComAdapter:
    adapter = _wecom_adapters.get(tenant_id)
    if adapter is None:
        tenant_config = state.config_manager.get(tenant_id)
        channel_config = tenant_config.channels.get("wecom") if tenant_config else None
        if not tenant_config or not channel_config or not channel_config.enabled:
            raise HTTPException(status_code=404, detail=f"租户 {tenant_id} 未启用企业微信通道")
        adapter = WeComAdapter(
            tenant_config=tenant_config,
            channel_config=channel_config,
            runner_getter=state.runners.get,
        )
        _wecom_adapters[tenant_id] = adapter
    return adapter


def _to_webhook_request(request: Request, method: str, body: str = "") -> WebhookRequest:
    flat_query = {k: v[0] for k, v in parse_qs(request.url.query, keep_blank_values=True).items()}
    return WebhookRequest(method=method, query=flat_query, body=body)


@app.get("/api/v1/channels/wecom/webhook/{tenant_id}")
async def wecom_verify(tenant_id: str, request: Request) -> PlainTextResponse:
    """企业微信 URL 可靠性验证（GET echostr）。"""
    adapter = _get_wecom_adapter(tenant_id)
    response = await adapter.handle_webhook(tenant_id, _to_webhook_request(request, "GET"))
    return PlainTextResponse(response.body, status_code=response.status_code, media_type=response.content_type)


@app.post("/api/v1/channels/wecom/webhook/{tenant_id}")
async def wecom_callback(tenant_id: str, request: Request) -> PlainTextResponse:
    """企业微信消息回调（POST 加密 XML）。"""
    adapter = _get_wecom_adapter(tenant_id)
    body = (await request.body()).decode("utf-8", errors="replace")
    response = await adapter.handle_webhook(tenant_id, _to_webhook_request(request, "POST", body))
    return PlainTextResponse(response.body, status_code=response.status_code, media_type=response.content_type)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    server = ServerConfig()
    uvicorn.run("trpc_service.web.app:app", host=server.host, port=server.port, workers=server.workers)


if __name__ == "__main__":
    main()
