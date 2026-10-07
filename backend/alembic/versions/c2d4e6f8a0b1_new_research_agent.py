"""new research agent

Revision ID: c2d4e6f8a0b1
Revises: b7d9f1a3c5e0
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d4e6f8a0b1"
down_revision: Union[str, Sequence[str], None] = "b7d9f1a3c5e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("license", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_knowledge_sources_name", "knowledge_sources", ["name"])
    op.create_index("ix_knowledge_sources_status", "knowledge_sources", ["status"])

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("source_id", sa.String(12), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("properties_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_knowledge_nodes_source_id", "knowledge_nodes", ["source_id"])
    op.create_index("ix_knowledge_nodes_kind", "knowledge_nodes", ["kind"])
    op.create_index("uq_knowledge_node_source_key", "knowledge_nodes", ["source_id", "key"], unique=True)

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("from_node_id", sa.String(12), sa.ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("predicate", sa.String(40), nullable=False),
        sa.Column("to_node_id", sa.String(12), sa.ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("properties_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_knowledge_edges_from_node_id", "knowledge_edges", ["from_node_id"])
    op.create_index("ix_knowledge_edges_to_node_id", "knowledge_edges", ["to_node_id"])
    op.create_index("ix_knowledge_edges_predicate", "knowledge_edges", ["predicate"])
    op.create_index("uq_knowledge_edge", "knowledge_edges", ["from_node_id", "predicate", "to_node_id"], unique=True)

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("source_id", sa.String(12), sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(12), sa.ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_knowledge_chunks_source_id", "knowledge_chunks", ["source_id"])
    op.create_index("ix_knowledge_chunks_node_id", "knowledge_chunks", ["node_id"])
    op.create_index("ix_knowledge_chunks_content_hash", "knowledge_chunks", ["content_hash"])
    op.execute("CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(chunk_id UNINDEXED, title, path, content, tokenize='porter unicode61')")

    op.create_table(
        "agent_threads",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("previous_response_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("thread_id", sa.String(12), sa.ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_agent_messages_thread_id", "agent_messages", ["thread_id"])


def downgrade() -> None:
    op.drop_table("agent_messages")
    op.drop_table("agent_threads")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks_fts")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_edges")
    op.drop_table("knowledge_nodes")
    op.drop_table("knowledge_sources")
