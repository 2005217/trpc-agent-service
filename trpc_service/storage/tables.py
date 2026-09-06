"""平台侧数据表的 SQLAlchemy 模型（唯一事实源）。

表结构设计见 docs/data-model.md；schema 变更走 Alembic 迁移
（migrations/），不要手动 ALTER。测试可用 Database.create_all()。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MYSQL_TABLE_ARGS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Base(DeclarativeBase):
    """平台表共同基类（与框架自身的 ORM metadata 互不相干）。"""


class AuditLogRow(Base):
    """审计日志（audit_log）。字段对齐 docs/data-model.md 的 DDL。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        {"mysql_engine": MYSQL_TABLE_ARGS["mysql_engine"], "mysql_charset": MYSQL_TABLE_ARGS["mysql_charset"]},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),  # SQLite 仅对 INTEGER PK 自增
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    user_id: Mapped[str] = mapped_column(String(64), default="")
    session_id: Mapped[str] = mapped_column(String(64), default="")
    agent_name: Mapped[str] = mapped_column(String(64), default="")
    tool_name: Mapped[str] = mapped_column(String(128), default="")
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="allow")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str] = mapped_column(String(64), default="")
    cost: Mapped[float] = mapped_column(Numeric(10, 4), default=0.0)
    trace_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChannelBindingRow(Base):
    """IM 用户与租户的绑定（channel_binding）。写入方在⑤幂等批次接线。"""

    __tablename__ = "channel_binding"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel_type", "external_user_id", "chat_id",
            name="uk_binding",
        ),
        {"mysql_engine": MYSQL_TABLE_ARGS["mysql_engine"], "mysql_charset": MYSQL_TABLE_ARGS["mysql_charset"]},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),  # SQLite 仅对 INTEGER PK 自增
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class IdempotencyRow(Base):
    """消息幂等键（idempotency）。第三层兜底（SQL 唯一索引）。"""

    __tablename__ = "idempotency"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_idem"),
        {"mysql_engine": MYSQL_TABLE_ARGS["mysql_engine"], "mysql_charset": MYSQL_TABLE_ARGS["mysql_charset"]},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),  # SQLite 仅对 INTEGER PK 自增
        primary_key=True,
        autoincrement=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dedupe_status: Mapped[str] = mapped_column(String(16), nullable=False, default="processed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
