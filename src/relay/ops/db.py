"""The ops store: runs (with idempotency keys), the human review queue, the dead-letter queue and gateway calls.

SQLAlchemy 2.0 models; SQLite for a laptop and the tests, PostgreSQL in Docker (the weekly KPI workflow queries these
tables directly with n8n's Postgres node)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class Run(Base):
    """One workflow execution as Relay sees it. (workflow, idempotency_key) is unique: a second delivery of the same
    email, invoice or form is answered from the first run instead of being processed again."""

    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("workflow", "idempotency_key", name="uq_runs_workflow_key"),)

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    workflow: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    execution_id: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="running")
    """running | auto | review | approved | rejected | escalated | error"""
    route: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(24), default="webhook")
    title: Mapped[str] = mapped_column(String(300), default="")
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    queued_ms: Mapped[int | None] = mapped_column(Integer)
    """Time from start until the item was ready for a person (review runs); latency_ms includes the human wait."""


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    workflow: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    """triage | invoice | lead_reply | faq_escalation"""
    title: Mapped[str] = mapped_column(String(300), default="")
    reasons: Mapped[list[Any]] = mapped_column(default=list)
    proposal: Mapped[dict[str, Any]] = mapped_column(default=dict)
    resume_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    """pending | approved | rejected | failed"""
    decision: Mapped[dict[str, Any]] = mapped_column(default=dict)
    decided_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    workflow: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(12), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(40))
    node: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), index=True, default="open")
    """open | replayed | resolved"""
    replays: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class GatewayCall(Base):
    __tablename__ = "gateway_calls"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(String(12), index=True)
    task: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    served_model: Mapped[str | None] = mapped_column(String(120))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cached: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    schema_valid: Mapped[bool] = mapped_column(default=True)
    decision: Mapped[str] = mapped_column(String(32), default="")
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        from pathlib import Path

        target = url.removeprefix("sqlite:///")
        if target and target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            return create_engine(url, connect_args={"check_same_thread": False})
        return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url == "sqlite://":
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return create_engine(url, pool_pre_ping=True)


def add_missing_columns(engine: Engine, metadata: Any) -> None:
    """A minimal forward migration for the demo: nullable columns added in a later version are created on start."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing and column.nullable:
                    kind = column.type.compile(dialect=engine.dialect)
                    connection.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {kind}'))


def make_sessionmaker(url: str) -> sessionmaker[Any]:
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    add_missing_columns(engine, Base.metadata)
    return sessionmaker(engine, expire_on_commit=False)
