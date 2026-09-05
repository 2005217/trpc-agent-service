"""IM 通道适配层。

ChannelAdapter 定义统一 webhook 收发接口；WeComAdapter 实现
企业微信真实协议（验签/解密/去重/身份映射/加密回复）。
"""
from trpc_service.channels.base import ChannelAdapter  # noqa: F401
from trpc_service.channels.base import WebhookRequest  # noqa: F401
from trpc_service.channels.base import WebhookResponse  # noqa: F401
from trpc_service.channels.wecom import WeComAdapter  # noqa: F401
