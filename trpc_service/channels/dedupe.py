"""消息去重器。

内存 TTL 实现（进程内有效）；多节点部署时替换为 Redis SETNX +
TTL（键 channel:msg_id），SQL 侧以 idempotency 表唯一索引兜底。
"""
from __future__ import annotations

import time
from typing import Dict


class Deduper:
    """TTL 去重缓存。"""

    def __init__(self, ttl_seconds: int = 300):
        self._ttl = ttl_seconds
        self._seen: Dict[str, float] = {}

    def seen(self, key: str) -> bool:
        """返回 True 表示重复消息；首次登记返回 False。"""
        now = time.monotonic()
        self._evict(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict(self, now: float) -> None:
        expired = [k for k, ts in self._seen.items() if now - ts > self._ttl]
        for k in expired:
            self._seen.pop(k, None)

    def forget(self, key: str) -> None:
        self._seen.pop(key, None)

    def clear(self) -> None:
        self._seen.clear()
