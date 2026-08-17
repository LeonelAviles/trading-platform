"""expand ticks into full mbo_events

Renames `ticks` -> `mbo_events` and widens it from a plain trade-print
table to the full Databento MBO record shape (action, order_id, sequence,
flags, ts_in_delta, instrument/publisher/rtype, channel_id), needed for
order-book reconstruction (DOM), not just trade prints. `timestamp` is
renamed to `ts_event` to match the source field name.

Written as a rename + alters (not autogenerate's default drop/create) so
existing rows survive the upgrade — the new NOT NULL columns have no
sensible default for pre-existing data, so backfill them (or truncate the
table) before running this against a database that already has rows.

Revision ID: fada482fd648
Revises: ee6a4a034450
Create Date: 2026-08-16 20:56:47.621856

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fada482fd648'
down_revision: Union[str, Sequence[str], None] = 'ee6a4a034450'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('ticks', 'mbo_events')
    op.alter_column('mbo_events', 'timestamp', new_column_name='ts_event')
    op.alter_column(
        'mbo_events', 'side',
        existing_type=sa.String(length=8), type_=sa.String(length=1),
    )
    op.alter_column(
        'mbo_events', 'price',
        existing_type=sa.Numeric(18, 6), type_=sa.Numeric(18, 9), nullable=True,
    )

    op.add_column('mbo_events', sa.Column('action', sa.String(length=1), nullable=False, server_default='T'))
    op.alter_column('mbo_events', 'action', server_default=None)
    op.add_column('mbo_events', sa.Column('order_id', sa.BigInteger(), nullable=False, server_default='0'))
    op.alter_column('mbo_events', 'order_id', server_default=None)
    op.add_column('mbo_events', sa.Column('sequence', sa.BigInteger(), nullable=False, server_default='0'))
    op.alter_column('mbo_events', 'sequence', server_default=None)
    op.add_column('mbo_events', sa.Column('flags', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('mbo_events', 'flags', server_default=None)
    op.add_column('mbo_events', sa.Column('ts_in_delta', sa.BigInteger(), nullable=False, server_default='0'))
    op.alter_column('mbo_events', 'ts_in_delta', server_default=None)
    op.add_column('mbo_events', sa.Column('channel_id', sa.Integer(), nullable=True))
    op.add_column('mbo_events', sa.Column('instrument_id', sa.BigInteger(), nullable=True))
    op.add_column('mbo_events', sa.Column('publisher_id', sa.Integer(), nullable=True))
    op.add_column('mbo_events', sa.Column('rtype', sa.Integer(), nullable=True))

    op.drop_index(op.f('ix_ticks_timestamp'), table_name='mbo_events')
    op.create_index(op.f('ix_mbo_events_ts_event'), 'mbo_events', ['ts_event'], unique=False)
    op.execute("ALTER INDEX ix_ticks_market_data_id RENAME TO ix_mbo_events_market_data_id")
    op.create_index(op.f('ix_mbo_events_order_id'), 'mbo_events', ['order_id'], unique=False)
    op.create_index(op.f('ix_mbo_events_instrument_id'), 'mbo_events', ['instrument_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mbo_events_instrument_id'), table_name='mbo_events')
    op.drop_index(op.f('ix_mbo_events_order_id'), table_name='mbo_events')
    op.execute("ALTER INDEX ix_mbo_events_market_data_id RENAME TO ix_ticks_market_data_id")
    op.drop_index(op.f('ix_mbo_events_ts_event'), table_name='mbo_events')

    op.drop_column('mbo_events', 'rtype')
    op.drop_column('mbo_events', 'publisher_id')
    op.drop_column('mbo_events', 'instrument_id')
    op.drop_column('mbo_events', 'channel_id')
    op.drop_column('mbo_events', 'ts_in_delta')
    op.drop_column('mbo_events', 'flags')
    op.drop_column('mbo_events', 'sequence')
    op.drop_column('mbo_events', 'order_id')
    op.drop_column('mbo_events', 'action')

    op.alter_column(
        'mbo_events', 'price',
        existing_type=sa.Numeric(18, 9), type_=sa.Numeric(18, 6), nullable=False,
    )
    op.alter_column(
        'mbo_events', 'side',
        existing_type=sa.String(length=1), type_=sa.String(length=8),
    )
    op.alter_column('mbo_events', 'ts_event', new_column_name='timestamp')
    op.create_index(op.f('ix_ticks_timestamp'), 'mbo_events', ['timestamp'], unique=False)
    op.rename_table('mbo_events', 'ticks')
