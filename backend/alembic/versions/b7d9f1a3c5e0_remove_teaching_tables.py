"""remove teaching tables

Revision ID: b7d9f1a3c5e0
Revises: a1c3e5f7b9d2
Create Date: 2026-08-31

Teaching mode was removed from the platform: sessions, trades and events go.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d9f1a3c5e0'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table in ('teaching_events', 'teaching_trades', 'teaching_sessions'):
        op.drop_table(table)


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('teaching_sessions',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('root', sa.String(length=8), nullable=False),
    sa.Column('date_from', sa.String(length=32), nullable=True),
    sa.Column('date_to', sa.String(length=32), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('compiled_strategy_id', sa.String(length=12), nullable=True),
    sa.Column('similarity_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['compiled_strategy_id'], ['strategies.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('teaching_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_teaching_sessions_status'), ['status'], unique=False)
    op.create_table('teaching_trades',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('session_id', sa.String(length=12), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('entry_ts', sa.Integer(), nullable=False),
    sa.Column('entry_price', sa.Float(), nullable=False),
    sa.Column('stop_price', sa.Float(), nullable=True),
    sa.Column('target_price', sa.Float(), nullable=True),
    sa.Column('exit_ts', sa.Integer(), nullable=True),
    sa.Column('exit_price', sa.Float(), nullable=True),
    sa.Column('exit_reason', sa.String(length=32), nullable=True),
    sa.Column('pnl_usd', sa.Float(), nullable=True),
    sa.Column('contracts', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Integer(), nullable=True),
    sa.Column('user_note', sa.Text(), nullable=True),
    sa.Column('snapshot_path', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['teaching_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('teaching_trades', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_teaching_trades_session_id'), ['session_id'], unique=False)
    op.create_table('teaching_events',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('session_id', sa.String(length=12), nullable=False),
    sa.Column('ts', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['teaching_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('teaching_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_teaching_events_session_id'), ['session_id'], unique=False)
