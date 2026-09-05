"""企业微信 Channel Adapter。

实现回调验签、AES 解密、消息去重、用户身份映射、
session_id 生成、Agent 执行与加密被动回复。
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional

from trpc_service.channels.base import WebhookRequest, WebhookResponse
from trpc_service.channels.dedupe import Deduper
from trpc_service.channels import wecom_crypto
from trpc_service.config.tenant_config import ChannelConfig, TenantConfig
from trpc_service.filter.context import META_TRACE
from trpc_service.filter.user_authz import user_authz
from trpc_service.gateway.router import SessionRouter
from trpc_service.telemetry.context import build_agent_context, new_trace_id

REPLY_MAX_CHARS = 1800  # 企微被动回复文本长度上限（保守值）


def _xml_get(xml_text: str, tag: str) -> str:
    try:
        root = ET.fromstring(xml_text)
        node = root.find(tag)
        return (node.text or "") if node is not None else ""
    except ET.ParseError:
        return ""


def _build_reply_xml(encrypt: str, signature: str, timestamp: str, nonce: str) -> str:
    return (
        "<xml>"
        f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
        f"<TimeStamp>{timestamp}</TimeStamp>"
        f"<Nonce><![CDATA[{nonce}]]></Nonce>"
        "</xml>"
    )


def _chunk_text(text: str, limit: int = REPLY_MAX_CHARS) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


class WeComAdapter:
    """企业微信通道适配器（被动回复模式）。"""

    channel_type = "wecom"

    def __init__(
        self,
        tenant_config: TenantConfig,
        channel_config: ChannelConfig,
        runner_getter: Callable[[str], object],
        deduper: Optional[Deduper] = None,
    ):
        self.tenant_config = tenant_config
        self.channel_config = channel_config
        self.get_runner = runner_getter
        self.deduper = deduper or Deduper()

    # ---- 对外入口 ----

    async def handle_webhook(self, tenant_id: str, request: WebhookRequest) -> WebhookResponse:
        cfg = self.channel_config
        query = request.query
        msg_signature = query.get("msg_signature", "")
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")

        if request.method.upper() == "GET":
            # URL 可靠性验证：解密 echostr 原样返回
            echostr = query.get("echostr", "")
            if not wecom_crypto.verify_signature(cfg.token, timestamp, nonce, echostr, msg_signature):
                return WebhookResponse(status_code=403, body="bad signature")
            plain, _ = wecom_crypto.decrypt_message(cfg.encoding_aes_key, echostr)
            return WebhookResponse(body=plain)

        encrypt = _xml_get(request.body, "Encrypt")
        if not encrypt:
            return WebhookResponse(status_code=400, body="missing Encrypt")
        if not wecom_crypto.verify_signature(cfg.token, timestamp, nonce, encrypt, msg_signature):
            return WebhookResponse(status_code=403, body="bad signature")

        try:
            plain_xml, _receiveid = wecom_crypto.decrypt_message(cfg.encoding_aes_key, encrypt)
        except wecom_crypto.WeComCryptoError:
            return WebhookResponse(status_code=400, body="decrypt failed")

        return await self._process_message(tenant_id, plain_xml, timestamp, nonce)

    # ---- 内部流程 ----

    async def _process_message(
        self, tenant_id: str, plain_xml: str, timestamp: str, nonce: str
    ) -> WebhookResponse:
        from_user = _xml_get(plain_xml, "FromUserName")
        msg_type = _xml_get(plain_xml, "MsgType")
        msg_id = _xml_get(plain_xml, "MsgId") or ""
        chat_id = _xml_get(plain_xml, "ChatId") or ""  # 群聊才有
        content = _xml_get(plain_xml, "Content")

        # 非文本消息与空内容直接 ACK（图片/文件处理见设计文档）
        if msg_type != "text" or not content or not from_user:
            return WebhookResponse(body="success")

        # 幂等去重：同一 MsgId 只处理一次
        if msg_id and self.deduper.seen(f"wecom:{msg_id}"):
            return WebhookResponse(body="success")

        # 身份映射 + 会话路由
        session_id = SessionRouter.session_id(tenant_id, self.channel_type, from_user, chat_id)
        user_authz.bind(tenant_id, self.channel_type, from_user, session_id)

        runner = self.get_runner(tenant_id)
        if runner is None:
            return WebhookResponse(status_code=503, body="agent not ready")

        # 预算前置校验（工具层 budget_limit 过滤器还有二次拦截）
        from trpc_service.gateway.budget import BudgetExceeded
        from trpc_service.filter.budget_limit import budget_manager

        try:
            budget_manager.check(tenant_id)
        except BudgetExceeded:
            from trpc_service.audit.model import AuditEvent
            from trpc_service.audit.service import audit_service

            audit_service.emit(
                AuditEvent(
                    tenant_id=tenant_id,
                    channel=self.channel_type,
                    user_id=from_user,
                    session_id=session_id,
                    agent_name=runner.app_name,
                    decision="block",
                    error_type="budget_exceeded",
                )
            )
            return self._encrypted_reply("今日使用额度已用完，请明日再试。", timestamp, nonce)

        agent_context = build_agent_context(
            tenant_id=tenant_id,
            user_id=from_user,
            session_id=session_id,
            channel=self.channel_type,
            trace_id=new_trace_id(),
        )
        outcome = await runner.run(
            user_id=session_id,  # 企微侧以会话绑定作为 user 标识
            session_id=session_id,
            message=content,
            agent_context=agent_context,
        )
        reply = outcome.text or "服务暂时不可用，请稍后重试。"

        # 审计由 web 层 AuditService 复用（channels 直接复用 audit_service）
        from trpc_service.audit.model import AuditEvent
        from trpc_service.audit.service import audit_service
        from trpc_service.filter.budget_limit import budget_manager

        budget_manager.record(tenant_id, api_calls=1, tokens=len(content) + len(reply))
        audit_service.emit(
            AuditEvent(
                tenant_id=tenant_id,
                channel=self.channel_type,
                user_id=from_user,
                session_id=session_id,
                agent_name=runner.app_name,
                tool_name=",".join(c.get("name", "") for c in outcome.tool_calls),
                decision="error" if outcome.error_type else "allow",
                error_type=outcome.error_type,
                trace_id=agent_context.get_metadata(META_TRACE),
            )
        )

        # 加密被动回复（超长文本分片，本条返回首片）
        first_chunk = _chunk_text(reply)[0]
        return self._encrypted_reply(first_chunk, timestamp, nonce)

    def _encrypted_reply(self, reply_text: str, timestamp: str, nonce: str) -> WebhookResponse:
        cfg = self.channel_config
        ts = timestamp or str(int(time.time()))
        inner_xml = (
            "<xml>"
            f"<ToUserName><![CDATA[{self.tenant_config.tenant_id}]]></ToUserName>"
            f"<FromUserName><![CDATA[{cfg.bot_id}]]></FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[{reply_text}]]></Content>"
            "</xml>"
        )
        encrypt = wecom_crypto.encrypt_message(cfg.encoding_aes_key, inner_xml, cfg.corp_id or "RECEIVEID")
        signature = wecom_crypto.sha1_signature(cfg.token, ts, nonce, encrypt)
        body = _build_reply_xml(encrypt, signature, ts, nonce)
        return WebhookResponse(content_type="application/xml", body=body, delivered_reply=reply_text)
