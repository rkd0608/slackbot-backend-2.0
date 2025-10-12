"""add team_id to knowledge graph tables

Revision ID: 20251011_2158
Revises: 20251008_1738_98c030b0b8a6
Create Date: 2025-10-11 21:58:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_team_id_kg'
down_revision = '20251010_0500'
branch_labels = None
depends_on = None


def upgrade():
    """Add team_id to entities and entity_relationships tables for multi-tenancy"""

    # Add team_id to entities table
    op.add_column('entities', sa.Column('team_id', sa.String(50), nullable=True))

    # Add team_id to entity_relationships table
    op.add_column('entity_relationships', sa.Column('team_id', sa.String(50), nullable=True))

    # Backfill team_id for existing entities using the first channel's team_id
    # Get the current team_id from channels (assuming single workspace for now)
    op.execute("""
        UPDATE entities e
        INNER JOIN (
            SELECT DISTINCT channel_id, team_id
            FROM channels
            WHERE team_id IS NOT NULL
        ) c ON JSON_CONTAINS(e.related_channels, JSON_QUOTE(c.channel_id))
        SET e.team_id = c.team_id
        WHERE e.team_id IS NULL
    """)

    # Backfill team_id for entity_relationships by joining with entities
    op.execute("""
        UPDATE entity_relationships er
        LEFT JOIN entities e ON er.entity_id_1 = e.id
        SET er.team_id = e.team_id
        WHERE er.team_id IS NULL AND e.team_id IS NOT NULL
    """)

    # Make team_id NOT NULL after backfill
    op.alter_column('entities', 'team_id',
                   existing_type=sa.String(50),
                   nullable=False)
    op.alter_column('entity_relationships', 'team_id',
                   existing_type=sa.String(50),
                   nullable=False)

    # Add indexes for better query performance
    op.create_index('idx_entities_team_id', 'entities', ['team_id'])
    op.create_index('idx_entities_team_canonical', 'entities', ['team_id', 'canonical_form'])
    op.create_index('idx_entity_relationships_team_id', 'entity_relationships', ['team_id'])


def downgrade():
    """Remove team_id from entities and entity_relationships tables"""

    # Drop indexes
    op.drop_index('idx_entity_relationships_team_id', table_name='entity_relationships')
    op.drop_index('idx_entities_team_canonical', table_name='entities')
    op.drop_index('idx_entities_team_id', table_name='entities')

    # Drop columns
    op.drop_column('entity_relationships', 'team_id')
    op.drop_column('entities', 'team_id')
