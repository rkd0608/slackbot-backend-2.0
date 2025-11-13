"""Create integration status tables

Revision ID: 20250130_integration
Revises: 20250129_team_vocabulary
Create Date: 2025-01-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20250130_integration'
down_revision = '20250129_team_vocabulary'
branch_labels = None
depends_on = None


def upgrade():
    """Create integration tracking tables"""

    # 1. Create integration_status table
    op.create_table(
        'integration_status',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.String(100), nullable=False, index=True),
        sa.Column('source_type', sa.String(50), nullable=False, index=True),

        # Connection status
        sa.Column('is_connected', sa.String(10), nullable=False, server_default='false'),
        sa.Column('connected_at', sa.DateTime(), nullable=True),
        sa.Column('disconnected_at', sa.DateTime(), nullable=True),

        # Indexing status
        sa.Column('indexing_status', sa.String(50), nullable=False, server_default='not_started'),
        sa.Column('indexing_started_at', sa.DateTime(), nullable=True),
        sa.Column('indexing_completed_at', sa.DateTime(), nullable=True),
        sa.Column('indexing_failed_at', sa.DateTime(), nullable=True),
        sa.Column('indexing_error', sa.Text(), nullable=True),

        # Progress tracking
        sa.Column('total_items', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('items_indexed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('items_failed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('progress_percentage', sa.Float(), nullable=False, server_default='0.0'),

        # Checkpoint for resumability
        sa.Column('last_checkpoint', sa.JSON(), nullable=True),

        # Entity counts
        sa.Column('entities_extracted', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('messages_indexed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('files_indexed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('code_snippets_indexed', sa.BigInteger(), nullable=False, server_default='0'),

        # Last sync
        sa.Column('last_full_sync', sa.DateTime(), nullable=True),
        sa.Column('last_incremental_sync', sa.DateTime(), nullable=True),
        sa.Column('next_scheduled_sync', sa.DateTime(), nullable=True),

        # Configuration
        sa.Column('config', sa.JSON(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=sa.text('CURRENT_TIMESTAMP')),

        sa.PrimaryKeyConstraint('id')
    )

    # Create composite unique index for team_id + source_type
    op.create_index('idx_team_source_unique', 'integration_status', ['team_id', 'source_type'], unique=True)
    op.create_index('idx_indexing_status', 'integration_status', ['indexing_status'])

    # 2. Create cross_source_graph_status table
    op.create_table(
        'cross_source_graph_status',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.String(100), nullable=False, unique=True, index=True),

        # Graph building status
        sa.Column('graph_status', sa.String(50), nullable=False, server_default='not_started'),
        sa.Column('graph_building_started_at', sa.DateTime(), nullable=True),
        sa.Column('graph_building_completed_at', sa.DateTime(), nullable=True),

        # Progress tracking
        sa.Column('total_edges_to_build', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('edges_built', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('edges_failed', sa.BigInteger(), nullable=False, server_default='0'),

        # Edge type counts
        sa.Column('slack_to_github_edges', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('github_to_slack_edges', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('user_identity_links', sa.BigInteger(), nullable=False, server_default='0'),

        # Last rebuild
        sa.Column('last_full_rebuild', sa.DateTime(), nullable=True),
        sa.Column('last_incremental_update', sa.DateTime(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=sa.text('CURRENT_TIMESTAMP')),

        sa.PrimaryKeyConstraint('id')
    )

    # 3. Create indexing_jobs table
    op.create_table(
        'indexing_jobs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.String(100), nullable=False, unique=True, index=True),
        sa.Column('team_id', sa.String(100), nullable=False, index=True),
        sa.Column('source_type', sa.String(50), nullable=False),

        # Job type
        sa.Column('job_type', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='queued', index=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='5'),

        # Progress
        sa.Column('total_items', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('items_processed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('items_failed', sa.BigInteger(), nullable=False, server_default='0'),

        # Job data
        sa.Column('job_data', sa.JSON(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),

        # Timing
        sa.Column('queued_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('failed_at', sa.DateTime(), nullable=True),

        # Worker info
        sa.Column('worker_id', sa.String(100), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=sa.text('CURRENT_TIMESTAMP')),

        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for indexing_jobs
    op.create_index('idx_job_team_status', 'indexing_jobs', ['team_id', 'status'])
    op.create_index('idx_job_status_priority', 'indexing_jobs', ['status', 'priority'])


def downgrade():
    """Drop integration tracking tables"""
    op.drop_table('indexing_jobs')
    op.drop_table('cross_source_graph_status')
    op.drop_table('integration_status')
