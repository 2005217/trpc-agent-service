"""OpenTelemetry 初始化。

框架 Runner/LlmAgent/工具已自动埋点（invocation / call_llm /
execute_tool span 与 gen_ai 指标），本模块只负责安装 TracerProvider
与 OTLP exporter，使链路可上报到 Jaeger / Tempo 等。
通过环境变量开关：
    OTEL_ENABLED=1
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
"""
from __future__ import annotations

import os


def setup_telemetry(service_name: str = "trpc-agent-service") -> bool:
    """初始化 OTel；未启用时返回 False（no-op，不影响运行）。"""
    if os.getenv("OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover
        return False

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "trpc-agent",
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    # 替换全局 provider，使框架 tracer（trpc.python.agent）随之生效
    trace.set_tracer_provider(provider)
    return True
