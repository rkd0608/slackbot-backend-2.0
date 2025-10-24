"""Create cross-source knowledge graph tables

Revision ID: 20251022_cross_source
Revises: 20251020_add_github_integration_tables
Create Date: 2025-10-22

This migration creates the unified knowledge graph infrastructure:
- cross_source_nodes: Stores all content from any source (Slack, GitHub, Jira, etc.)
- cross_source_edges: Stores relationships between nodes

Design:
- Supports multi-source integration (GitHub, Jira, Confluence, Linear, etc.)
- Canonical ID format: "{source}:{source_id}"
- Team-based multi-tenancy
- JSONB for flexible source-specific metadata
- Optimized indexes for common query patterns
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20251022_cross_source'
down_revision = 'add_github_integration'
branch_labels = None
depends_on = None


def upgrade():
    # MySQL doesn't need explicit ENUM creation - handled in column definitions

    # Create cross_source_nodes table
    op.create_table(
        'cross_source_nodes',
        # Primary identity
        sa.Column('canonical_id', sa.String(length=500), nullable=False, primary_key=True),
        sa.Column('team_id', sa.String(length=100), nullable=False, index=True),

        # Source information
        sa.Column('source', sa.String(length=50), nullable=False, index=True),
        sa.Column('source_id', sa.String(length=500), nullable=False),
        sa.Column('node_type', sa.String(length=50), nullable=False, index=True),

        # Core content
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),

        # Access
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('visibility', sa.String(length=50), nullable=True, server_default='team'),

        # Attribution
        sa.Column('author', sa.String(length=500), nullable=True),
        sa.Column('author_id', sa.String(length=500), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('indexed_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),

        # Context
        sa.Column('project_context', mysql.JSON(), nullable=True),
        sa.Column('channel_context', mysql.JSON(), nullable=True),
        sa.Column('tags', mysql.JSON(), nullable=True),

        # Embeddings
        sa.Column('vector_id_semantic', sa.String(length=500), nullable=True),
        sa.Column('vector_id_code', sa.String(length=500), nullable=True),

        # Source-specific metadata
        sa.Column('source_metadata', mysql.JSON(), nullable=True),

        # Soft delete
        sa.Column('is_deleted', sa.String(length=10), nullable=False, server_default='false'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )

    # Create indexes for cross_source_nodes
    # Note: Using mysql_length for large VARCHAR columns to stay within MySQL index key length limit
    op.create_index('idx_node_team_source', 'cross_source_nodes', ['team_id', 'source'])
    op.create_index('idx_node_team_type', 'cross_source_nodes', ['team_id', 'node_type'])
    op.create_index('idx_node_team_created', 'cross_source_nodes', ['team_id', 'created_at'])
    op.create_index('idx_node_source_id', 'cross_source_nodes', ['source', 'source_id'], mysql_length={'source_id': 191})
    op.create_index('idx_node_author', 'cross_source_nodes', ['team_id', 'author'], mysql_length={'author': 191})
    op.create_index('idx_node_visibility', 'cross_source_nodes', ['team_id', 'visibility'])

    # Create cross_source_edges table
    op.create_table(
        'cross_source_edges',
        # Primary key
        sa.Column('id', sa.String(length=100), nullable=False, primary_key=True),

        # Relationship
        sa.Column('source_node_id', sa.String(length=500), nullable=False),
        sa.Column('target_node_id', sa.String(length=500), nullable=False),
        sa.Column('edge_type', sa.String(length=50), nullable=False, index=True),

        # Context
        sa.Column('team_id', sa.String(length=100), nullable=False, index=True),

        # Confidence and evidence
        sa.Column('confidence', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('detection_method', sa.String(length=50), nullable=False),
        sa.Column('evidence', sa.String(length=2000), nullable=True),
        sa.Column('link_text', sa.String(length=1000), nullable=True),

        # Edge metadata (avoiding 'metadata' as it's reserved)
        sa.Column('edge_metadata', mysql.JSON(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),

        # Validation
        sa.Column('is_validated', sa.String(length=10), nullable=False, server_default='false'),
        sa.Column('validated_by', sa.String(length=500), nullable=True),
        sa.Column('validated_at', sa.DateTime(), nullable=True),

        # Soft delete
        sa.Column('is_deleted', sa.String(length=10), nullable=False, server_default='false'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),

        # Foreign keys
        sa.ForeignKeyConstraint(
            ['source_node_id'],
            ['cross_source_nodes.canonical_id'],
            name='fk_edge_source_node',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['target_node_id'],
            ['cross_source_nodes.canonical_id'],
            name='fk_edge_target_node',
            ondelete='CASCADE'
        ),
    )

    # Create indexes for cross_source_edges
    # Note: Using mysql_length for large VARCHAR columns to stay within MySQL index key length limit (3072 bytes)
    op.create_index('idx_edge_source', 'cross_source_edges', ['source_node_id', 'edge_type'], mysql_length={'source_node_id': 191})
    op.create_index('idx_edge_target', 'cross_source_edges', ['target_node_id', 'edge_type'], mysql_length={'target_node_id': 191})
    op.create_index('idx_edge_team_type', 'cross_source_edges', ['team_id', 'edge_type'])
    op.create_index('idx_edge_confidence', 'cross_source_edges', ['team_id', 'confidence'])
    op.create_index('idx_edge_detection', 'cross_source_edges', ['detection_method'])
    op.create_index('idx_edge_bidirectional', 'cross_source_edges', ['source_node_id', 'target_node_id'], mysql_length={'source_node_id': 191, 'target_node_id': 191})
    op.create_index('idx_edge_created', 'cross_source_edges', ['team_id', 'created_at'])


def downgrade():
    # Drop tables (MySQL automatically drops indexes when table is dropped)
    op.drop_table('cross_source_edges')
    op.drop_table('cross_source_nodes')
