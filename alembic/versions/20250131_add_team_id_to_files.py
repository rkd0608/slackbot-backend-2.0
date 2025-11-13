"""Add team_id to files table for multi-tenancy isolation

Revision ID: 20250131_add_team_id_to_files
Revises: 20250130_integration
Create Date: 2025-01-31 00:00:00.000000

CRITICAL SECURITY FIX:
This migration addresses a critical multi-tenancy vulnerability where files
from different workspaces could be accessed cross-team. Adding team_id ensures
proper workspace isolation and prevents data leakage.

Steps:
1. Add team_id column (nullable initially)
2. Backfill team_id from channels table
3. Make team_id NOT NULL
4. Add indexes for performance and security queries

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '20250131_add_team_id_to_files'
down_revision = '20250130_integration'
branch_labels = None
depends_on = None


def upgrade():
    """Add team_id to files table and backfill from channels"""

    # Step 1: Add team_id column as nullable initially
    # (allows us to backfill data before making it NOT NULL)
    op.add_column(
        'files',
        sa.Column('team_id', sa.String(50), nullable=True)
    )

    # Step 2: Backfill team_id from channels table
    # This joins files with channels to populate team_id
    connection = op.get_bind()
    connection.execute(text("""
        UPDATE files f
        INNER JOIN channels c ON f.channel_id = c.channel_id
        SET f.team_id = c.team_id
        WHERE f.team_id IS NULL
    """))

    # Step 3: Make team_id NOT NULL now that data is backfilled
    # This ensures all future file records MUST have team_id
    op.alter_column(
        'files',
        'team_id',
        existing_type=sa.String(50),
        nullable=False
    )

    # Step 4: Create indexes for security and performance

    # Primary security index - allows efficient team-scoped queries
    op.create_index(
        'idx_team_id',
        'files',
        ['team_id']
    )

    # Composite index for common query pattern: team + channel
    # This supports queries like: "get all files for team X in channel Y"
    op.create_index(
        'idx_team_channel',
        'files',
        ['team_id', 'channel_id']
    )

    # Composite index for file lookups with security
    # Supports: "get file X from team Y"
    op.create_index(
        'idx_team_file',
        'files',
        ['team_id', 'file_id']
    )


def downgrade():
    """Remove team_id from files table"""

    # Drop indexes first
    op.drop_index('idx_team_file', table_name='files')
    op.drop_index('idx_team_channel', table_name='files')
    op.drop_index('idx_team_id', table_name='files')

    # Drop column
    op.drop_column('files', 'team_id')
