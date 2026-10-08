"""Append-only research proposals, decisions and exact evidence membership."""
from alembic import op
import sqlalchemy as sa

revision = "e6f8a0b2c4d6"
down_revision = "d4e6f8a0b2c3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_proposals",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("thread_id", sa.String(12), sa.ForeignKey("agent_threads.id"), nullable=False),
        sa.Column("parent_id", sa.String(12), sa.ForeignKey("research_proposals.id")),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("blockers_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("strategy_id", sa.String(12), sa.ForeignKey("strategies.id")),
        sa.Column("run_id", sa.String(12), sa.ForeignKey("agent_runs.id")),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_research_proposals_thread_id", "research_proposals", ["thread_id"])
    op.create_table(
        "research_decisions",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("proposal_id", sa.String(12), sa.ForeignKey("research_proposals.id"), nullable=False, unique=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_table(
        "research_evidence",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("proposal_id", sa.String(12), sa.ForeignKey("research_proposals.id"), nullable=False),
        sa.Column("job_id", sa.String(12), sa.ForeignKey("backtests.id"), nullable=False, unique=True),
    )
    op.create_index("ix_research_evidence_proposal_id", "research_evidence", ["proposal_id"])


def downgrade():
    op.drop_table("research_evidence")
    op.drop_table("research_decisions")
    op.drop_table("research_proposals")
