"""runs, run_steps, outbox

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("lease_owner", sa.Text),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'completed', 'failed', 'rejected')",
            name="runs_status_check",
        ),
    )
    op.create_index("ix_runs_status_heartbeat", "runs", ["status", "heartbeat_at"])

    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("output", JSONB),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "step_no", name="uq_run_steps_run_step"),
        sa.CheckConstraint(
            "status IN ('pending', 'awaiting_approval', 'completed', 'failed', 'rejected')",
            name="run_steps_status_check",
        ),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("recipient", sa.Text, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "step_no", name="uq_outbox_run_step"),
    )


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("run_steps")
    op.drop_table("runs")
