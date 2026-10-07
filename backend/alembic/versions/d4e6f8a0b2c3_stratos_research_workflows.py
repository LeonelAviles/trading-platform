"""durable Stratos research workflows

Revision ID: d4e6f8a0b2c3
Revises: c2d4e6f8a0b1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8a0b2c3"
down_revision: Union[str, Sequence[str], None] = "c2d4e6f8a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("thread_id", sa.String(12), sa.ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=True),
        sa.Column("answer_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    with op.batch_alter_table("backtests") as batch_op:
        batch_op.add_column(sa.Column("agent_run_id", sa.String(12), nullable=True))
        batch_op.create_foreign_key("fk_backtests_agent_run_id", "agent_runs", ["agent_run_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index("ix_backtests_agent_run_id", ["agent_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("backtests") as batch_op:
        batch_op.drop_index("ix_backtests_agent_run_id")
        batch_op.drop_constraint("fk_backtests_agent_run_id", type_="foreignkey")
        batch_op.drop_column("agent_run_id")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_thread_id", table_name="agent_runs")
    op.drop_table("agent_runs")
