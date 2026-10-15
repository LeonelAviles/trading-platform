"""Approval-gated ML discovery, separate from strategy execution approvals."""
from alembic import op
import sqlalchemy as sa

revision = "f7a9b1c3d5e7"
down_revision = "e6f8a0b2c4d6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ml_experiments",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("thread_id", sa.String(12), sa.ForeignKey("agent_threads.id"), nullable=False, unique=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("decision_json", sa.JSON()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
    )


def downgrade():
    op.drop_table("ml_experiments")
