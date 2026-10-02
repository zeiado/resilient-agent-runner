import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

RUN_STATUSES = ("queued", "running", "awaiting_approval", "completed", "failed", "rejected")
STEP_STATUSES = ("pending", "awaiting_approval", "completed", "failed", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="runs_status_check"),
        Index("ix_runs_status_heartbeat", "status", "heartbeat_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_no", name="uq_run_steps_run_step"),
        CheckConstraint(_in("status", STEP_STATUSES), name="run_steps_status_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step_no: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(Text)
    input: Mapped[dict] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(Base):
    __tablename__ = "outbox"
    # (run_id, step_no) is the idempotency key for send_email
    __table_args__ = (UniqueConstraint("run_id", "step_no", name="uq_outbox_run_step"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step_no: Mapped[int] = mapped_column(Integer)
    recipient: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
