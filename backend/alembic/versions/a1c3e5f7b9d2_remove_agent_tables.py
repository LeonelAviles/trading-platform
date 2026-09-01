"""remove agent, research, knowledge and LLM tables

Revision ID: a1c3e5f7b9d2
Revises: fd664d14dca5
Create Date: 2026-08-31

The platform no longer runs an LLM agent: agent runs, findings, research
sources / docs / queue, primitive requests, LLM usage, the knowledge store and
the teaching questions go, together with `backtests.agent_run_id`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3e5f7b9d2'
down_revision: Union[str, Sequence[str], None] = 'fd664d14dca5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Order respects foreign keys: children before parents.
    with op.batch_alter_table('backtests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_backtests_agent_run_id'))
        batch_op.drop_column('agent_run_id')
    for table in ('knowledge_facts', 'teaching_questions', 'findings', 'llm_usage', 'research_docs',
                  'research_sources', 'research_queue', 'primitive_requests', 'agent_runs'):
        op.drop_table(table)


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('agent_runs',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('input_json', sa.JSON(), nullable=True),
    sa.Column('state_json', sa.JSON(), nullable=True),
    sa.Column('question_json', sa.JSON(), nullable=True),
    sa.Column('answer_json', sa.JSON(), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('primitive_requests',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('params_json', sa.JSON(), nullable=True),
    sa.Column('pseudocode', sa.Text(), nullable=True),
    sa.Column('sources_json', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('research_queue',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('topic', sa.Text(), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('requested_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('research_sources',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('domain', sa.String(length=255), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('tier', sa.Integer(), nullable=True),
    sa.Column('credibility', sa.Float(), nullable=True),
    sa.Column('scored_json', sa.JSON(), nullable=True),
    sa.Column('fetched_at', sa.String(length=32), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('url')
    )
    op.create_table('research_docs',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('source_id', sa.String(length=12), nullable=False),
    sa.Column('topic', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('ingested_to_graph', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['research_sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('llm_usage',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('ts', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=64), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cache_read', sa.Integer(), nullable=False),
    sa.Column('cache_write', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('agent_run_id', sa.String(length=12), nullable=True),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('findings',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('backtest_id', sa.String(length=12), nullable=True),
    sa.Column('strategy_id', sa.String(length=12), nullable=True),
    sa.Column('category', sa.String(length=64), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('evidence_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['backtest_id'], ['backtests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('teaching_questions',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('session_id', sa.String(length=12), nullable=False),
    sa.Column('trade_id', sa.String(length=12), nullable=True),
    sa.Column('replay_ts', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('asked_at', sa.String(length=32), nullable=False),
    sa.Column('answered_at', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['teaching_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trade_id'], ['teaching_trades.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('knowledge_facts',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('tags_json', sa.JSON(), nullable=True),
    sa.Column('source_id', sa.String(length=12), nullable=True),
    sa.Column('source_title', sa.Text(), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('credibility', sa.Float(), nullable=False),
    sa.Column('evidence_type', sa.String(length=24), nullable=True),
    sa.Column('embedding_json', sa.JSON(), nullable=True),
    sa.Column('ref_id', sa.String(length=12), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('invalid_at', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['research_sources.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('backtests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_run_id', sa.String(length=12), nullable=True))
        batch_op.create_foreign_key('fk_backtests_agent_run_id', 'agent_runs', ['agent_run_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_backtests_agent_run_id'), ['agent_run_id'], unique=False)
