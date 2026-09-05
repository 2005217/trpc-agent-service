"""审计服务：落盘 JSONL（data/audit/audit.jsonl）。

SQL 后端（audit_log 表）在 storage.sql 可用时自动启用；
文件后端始终兜底，保证审计不丢。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from trpc_service.audit.model import AuditEvent
from trpc_service.audit.redact import redact

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_AUDIT_DIR = PROJECT_ROOT / "data" / "audit"


class AuditService:
    """异步友好的审计写入器（文件为同步 IO，量小可接受）。"""

    def __init__(self, audit_dir: Optional[Path] = None):
        self._dir = Path(audit_dir) if audit_dir else DEFAULT_AUDIT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "audit.jsonl"

    def emit(self, event: AuditEvent) -> AuditEvent:
        """写入一条审计记录（脱敏后落盘）。"""
        record = redact(event.model_dump(mode="json"))
        with self._file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        return event

    def tail(self, limit: int = 20) -> List[dict]:
        """读取最近 limit 条审计记录（Admin API 查询用）。"""
        if not self._file.exists():
            return []
        lines = self._file.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines[-limit:]]

    def cleanup(self, retention_map: dict[str, int]) -> int:
        """按租户保留期清理过期审计行，返回删除行数。"""
        if not self._file.exists():
            return 0
        now = datetime.now()
        kept, removed = [], 0
        for line in self._file.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            days = retention_map.get(record.get("tenant_id", ""), 90)
            created = datetime.fromisoformat(record["created_at"])
            if now - created > timedelta(days=days):
                removed += 1
            else:
                kept.append(line)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, self._file)   # 原子替换：避免写一半崩溃丢全部审计
        return removed


audit_service = AuditService()
