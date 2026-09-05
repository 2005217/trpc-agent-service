"""统一日志入口：级别走环境变量，格式带上下文键值。

用法：
    from trpc_service.log import get_logger
    logger = get_logger("web.chat")
    logger.info("chat done tenant=%s trace=%s", tenant_id, trace_id)
"""
import logging
import os


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,  # 覆盖 uvicorn/框架先装的 handler，统一输出格式
    )
    return logging.getLogger(name)
