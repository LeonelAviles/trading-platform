"""Feature-only discovery, recorded AI review, and held-out exposure ledger."""
from alembic import op
import sqlalchemy as sa

revision = "a8c0d2e4f6b8"
down_revision = "f7a9b1c3d5e7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ml_discoveries",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("thread_id", sa.String(12), sa.ForeignKey("agent_threads.id"), nullable=False, unique=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("decision_json", sa.JSON()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("review_json", sa.JSON()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False))
    with op.batch_alter_table("ml_experiments") as batch:
        batch.add_column(sa.Column("discovery_id", sa.String(12)))
        batch.create_foreign_key("fk_ml_experiment_discovery", "ml_discoveries", ["discovery_id"], ["id"])
        batch.create_unique_constraint("uq_ml_experiment_discovery", ["discovery_id"])
    op.create_table("ml_heldout_uses",
        sa.Column("session_key", sa.String(32), primary_key=True),
        sa.Column("experiment_id", sa.String(12), sa.ForeignKey("ml_experiments.id"), nullable=False),
        sa.Column("partition", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False))
    op.create_index("ix_ml_heldout_uses_experiment_id", "ml_heldout_uses", ["experiment_id"])


def downgrade():
    op.drop_table("ml_heldout_uses")
    with op.batch_alter_table("ml_experiments") as batch:
        batch.drop_constraint("uq_ml_experiment_discovery", type_="unique")
        batch.drop_constraint("fk_ml_experiment_discovery", type_="foreignkey")
        batch.drop_column("discovery_id")
    op.drop_table("ml_discoveries")
