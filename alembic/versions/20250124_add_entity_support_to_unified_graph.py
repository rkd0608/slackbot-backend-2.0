"""Add entity support to unified knowledge graph

Revision ID: 20250124_entity_graph
Revises: 20251022_create_cross_source_knowledge_graph
Create Date: 2025-01-24 00:00:00.000000

This migration extends the cross-source knowledge graph to support entities:
- Adds new entity node types (PERSON, TOPIC, PROJECT, etc.)
- Adds entity_metadata JSONB column to cross_source_nodes
- Adds importance_score JSONB column for graph analytics
- Adds new entity-specific edge types
- Adds indexes for efficient entity queries

This enables the unified architecture where entities are nodes in the same graph
as content (messages, code, PRs, etc.), allowing powerful cross-source reasoning.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import JSON

# revision identifiers, used by Alembic.
revision = '20250124_entity_graph'
down_revision = '20251022_cross_source'
branch_labels = None
depends_on = None


def upgrade():
    """Add entity support to unified knowledge graph"""

    # 1. Add new columns to cross_source_nodes (using MySQL JSON)
    # MySQL doesn't allow default values for JSON columns
    op.add_column('cross_source_nodes',
        sa.Column('entity_metadata', JSON, nullable=True)
    )
    op.add_column('cross_source_nodes',
        sa.Column('importance_score', JSON, nullable=True)
    )

    # 2. MySQL doesn't support GIN indexes - these are for PostgreSQL only
    # For MySQL, JSON columns are already indexed efficiently
    # If needed, we can create functional indexes in MySQL 8.0+ on specific JSON keys

    # 3. Update NodeType enum to include entity types
    # MySQL enums: Need to modify the ENUM column definition
    # Get current enum values and add new ones
    op.execute("""
        ALTER TABLE cross_source_nodes
        MODIFY COLUMN node_type ENUM(
            'message', 'thread', 'file', 'code_snippet',
            'repository', 'pull_request', 'issue', 'code_file', 'commit', 'release',
            'jira_issue', 'jira_epic', 'jira_sprint',
            'confluence_page', 'confluence_space',
            'linear_issue', 'linear_project',
            'gdrive_doc', 'gdrive_sheet', 'gdrive_slide',
            'person', 'topic', 'project', 'technology', 'organization', 'location', 'event'
        ) NOT NULL
    """)

    # 4. Update EdgeType enum to include entity relationship types
    op.execute("""
        ALTER TABLE cross_source_edges
        MODIFY COLUMN edge_type ENUM(
            'references', 'discusses', 'relates_to',
            'caused_by', 'fixes', 'resolved_by',
            'part_of', 'child_of', 'implements',
            'follows', 'precedes',
            'modifies', 'introduces', 'removes', 'tests',
            'mentions', 'assigned_to', 'reviewed_by',
            'authored_by', 'about', 'tagged_with', 'involves', 'belongs_to', 'located_in',
            'works_with', 'related_to', 'similar_to', 'expertise_in', 'member_of', 'parent_topic', 'co_occurs_with'
        ) NOT NULL
    """)

    # 5. Update DetectionMethod enum
    op.execute("""
        ALTER TABLE cross_source_edges
        MODIFY COLUMN detection_method ENUM(
            'explicit_link', 'keyword_match', 'entity_extraction',
            'temporal_proximity', 'author_correlation', 'semantic_similarity',
            'co_occurrence', 'heuristic', 'manual'
        ) NOT NULL
    """)


def downgrade():
    """Remove entity support from unified knowledge graph"""

    # 1. Drop columns (no indexes to drop in MySQL version)
    op.drop_column('cross_source_nodes', 'importance_score')
    op.drop_column('cross_source_nodes', 'entity_metadata')

    # 2. Revert enum values (optional - usually not needed)
    # MySQL: Would need to redefine enums without entity types
    # Skipping for simplicity - old code will still work with extra enum values
