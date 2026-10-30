"""token budget, cancelled status

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD_RUN = "('queued', 'running', 'awaiting_approval', 'completed', 'failed', 'rejected')"
NEW_RUN = "('queued', 'running', 'awaiting_approval', 'completed', 'failed', 'rejected', 'cancelled')"
OLD_STEP = "('pending', 'awaiting_approval', 'completed', 'failed', 'rejected')"
NEW_STEP = "('pending', 'awaiting_approval', 'completed', 'failed', 'rejected', 'cancelled')"


def set_checks(run_values: str, step_values: str) -> None:
    op.drop_constraint("runs_status_check", "runs", type_="check")
    op.create_check_constraint("runs_status_check", "runs", f"status IN {run_values}")
    op.drop_constraint("run_steps_status_check", "run_steps", type_="check")
    op.create_check_constraint("run_steps_status_check", "run_steps", f"status IN {step_values}")


def upgrade() -> None:
    op.add_column("runs", sa.Column("max_tokens", sa.Integer))
    op.add_column("runs", sa.Column("tokens_used", sa.Integer, nullable=False, server_default="0"))
    op.add_column("run_steps", sa.Column("tokens", sa.Integer))
    set_checks(NEW_RUN, NEW_STEP)


def downgrade() -> None:
    op.execute("UPDATE runs SET status = 'failed', error = 'cancelled' WHERE status = 'cancelled'")
    op.execute("UPDATE run_steps SET status = 'rejected' WHERE status = 'cancelled'")
    set_checks(OLD_RUN, OLD_STEP)
    op.drop_column("run_steps", "tokens")
    op.drop_column("runs", "tokens_used")
    op.drop_column("runs", "max_tokens")
