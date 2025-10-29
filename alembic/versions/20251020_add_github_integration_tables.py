"""Add GitHub integration tables

Revision ID: add_github_integration
Revises: 20251016_add_event_reliability_tables
Create Date: 2025-10-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'add_github_integration'
down_revision = 'event_reliability_001'
branch_labels = None
depends_on = None


def upgrade():
    # Create integrations table
    op.create_table(
        'integrations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('integration_type', sa.String(50), nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('enabled_at', sa.DateTime(), nullable=False),
        sa.Column('disabled_at', sa.DateTime(), nullable=True),
        sa.Column('config', mysql.JSON(), nullable=True),
        sa.Column('enabled_by_user_id', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )

    # Create indexes for integrations
    op.create_index('idx_team_integration', 'integrations', ['team_id', 'integration_type'], unique=True)
    op.create_index('idx_active', 'integrations', ['is_active'])

    # Create user_integration_connections table
    op.create_table(
        'user_integration_connections',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('integration_id', sa.String(36), nullable=False),
        sa.Column('integration_type', sa.String(50), nullable=False),
        sa.Column('access_token_encrypted', sa.Text(), nullable=False),
        sa.Column('refresh_token_encrypted', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('external_user_id', sa.String(255), nullable=True),
        sa.Column('external_username', sa.String(255), nullable=True),
        sa.Column('external_email', sa.String(255), nullable=True),
        sa.Column('scopes', mysql.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('connected_at', sa.DateTime(), nullable=False),
        sa.Column('disconnected_at', sa.DateTime(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('error_count', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )

    # Create indexes for user_integration_connections
    op.create_index('idx_user_integration', 'user_integration_connections', ['user_id', 'integration_type'])
    op.create_index('idx_team_user', 'user_integration_connections', ['team_id', 'user_id'])
    op.create_index('idx_active_connections', 'user_integration_connections', ['is_active'])
    op.create_index('idx_external_user', 'user_integration_connections', ['external_user_id'])

    # Create external_contents table
    op.create_table(
        'external_contents',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('source_type', sa.String(50), nullable=False),
        sa.Column('source_id', sa.String(500), nullable=False),
        sa.Column('source_url', sa.String(1000), nullable=True),
        sa.Column('title', sa.String(1000), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('content_type', sa.String(50), nullable=True),
        sa.Column('language', sa.String(50), nullable=True),
        sa.Column('author', sa.String(255), nullable=True),
        sa.Column('author_external_id', sa.String(255), nullable=True),
        sa.Column('created_at_source', sa.DateTime(), nullable=True),
        sa.Column('updated_at_source', sa.DateTime(), nullable=True),
        sa.Column('source_metadata', mysql.JSON(), nullable=True),
        sa.Column('visibility', sa.String(50), nullable=False),
        sa.Column('accessible_by_user_ids', mysql.JSON(), nullable=True),
        sa.Column('accessible_by_team_ids', mysql.JSON(), nullable=True),
        sa.Column('repository_id', sa.String(255), nullable=True),
        sa.Column('repository_full_name', sa.String(500), nullable=True),
        sa.Column('repository_visibility', sa.String(50), nullable=True),
        sa.Column('space_id', sa.String(255), nullable=True),
        sa.Column('space_permissions', mysql.JSON(), nullable=True),
        sa.Column('project_id', sa.String(255), nullable=True),
        sa.Column('project_permissions', mysql.JSON(), nullable=True),
        sa.Column('vector_id', sa.String(50), nullable=True, unique=True),
        sa.Column('vector_id_semantic', sa.String(50), nullable=True, unique=True),
        sa.Column('indexed_by_user_id', sa.String(50), nullable=True),
        sa.Column('last_indexed_at', sa.DateTime(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )

    # Create indexes for external_contents
    op.create_index('idx_team_source', 'external_contents', ['team_id', 'source_type'])
    op.create_index('idx_source_id_unique', 'external_contents', ['source_type', 'source_id'], unique=True)
    op.create_index('idx_vector_id_main', 'external_contents', ['vector_id'])
    op.create_index('idx_vector_id_semantic', 'external_contents', ['vector_id_semantic'])
    op.create_index('idx_repository', 'external_contents', ['repository_id'])
    op.create_index('idx_space', 'external_contents', ['space_id'])
    op.create_index('idx_project', 'external_contents', ['project_id'])
    op.create_index('idx_content_type', 'external_contents', ['content_type'])
    op.create_index('idx_language', 'external_contents', ['language'])
    op.create_index('idx_visibility', 'external_contents', ['visibility'])

    # Create integration_sync_jobs table
    op.create_table(
        'integration_sync_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('user_connection_id', sa.String(36), nullable=False),
        sa.Column('integration_type', sa.String(50), nullable=False),
        sa.Column('sync_type', sa.String(50), nullable=False),
        sa.Column('target_resource', sa.String(500), nullable=True),
        sa.Column('status', sa.String(50), default='pending'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('total_items', sa.Integer(), default=0),
        sa.Column('processed_items', sa.Integer(), default=0),
        sa.Column('failed_items', sa.Integer(), default=0),
        sa.Column('items_created', sa.Integer(), default=0),
        sa.Column('items_updated', sa.Integer(), default=0),
        sa.Column('items_deleted', sa.Integer(), default=0),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_details', mysql.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )

    # Create indexes for integration_sync_jobs
    op.create_index('idx_team_status', 'integration_sync_jobs', ['team_id', 'status'])
    op.create_index('idx_user_connection_sync', 'integration_sync_jobs', ['user_connection_id'])
    op.create_index('idx_status_jobs', 'integration_sync_jobs', ['status'])
    op.create_index('idx_integration_type_jobs', 'integration_sync_jobs', ['integration_type'])


def downgrade():
    # Drop tables in reverse order
    op.drop_table('integration_sync_jobs')
    op.drop_table('external_contents')
    op.drop_table('user_integration_connections')
    op.drop_table('integrations')
