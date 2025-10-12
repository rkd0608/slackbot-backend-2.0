"""add workspace tables and multi-tenant support

Revision ID: 20251006_2200
Revises: 2eda4d1ef2e9
Create Date: 2025-10-06 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20251006_2200'
down_revision = '2eda4d1ef2e9'
branch_labels = None
depends_on = None


def upgrade():
    # Create workspaces table
    op.create_table(
        'workspaces',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.String(length=50), nullable=False),
        sa.Column('team_name', sa.String(length=255), nullable=False),
        sa.Column('team_domain', sa.String(length=255), nullable=True),
        sa.Column('team_url', sa.String(length=500), nullable=True),
        sa.Column('bot_access_token', sa.Text(), nullable=False),
        sa.Column('bot_user_id', sa.String(length=50), nullable=False),
        sa.Column('bot_scopes', sa.JSON(), nullable=True),
        sa.Column('installer_user_id', sa.String(length=50), nullable=False),
        sa.Column('installer_email', sa.String(length=255), nullable=True),
        sa.Column('installed_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('subscription_status', sa.String(length=50), nullable=False, server_default='trial'),
        sa.Column('subscription_tier', sa.String(length=50), nullable=False, server_default='starter'),
        sa.Column('trial_started_at', sa.DateTime(), nullable=True),
        sa.Column('trial_ends_at', sa.DateTime(), nullable=True),
        sa.Column('stripe_customer_id', sa.String(length=255), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(length=255), nullable=True),
        sa.Column('stripe_payment_method_id', sa.String(length=255), nullable=True),
        sa.Column('billing_cycle_anchor', sa.Integer(), nullable=True),
        sa.Column('current_period_start', sa.DateTime(), nullable=True),
        sa.Column('current_period_end', sa.DateTime(), nullable=True),
        sa.Column('user_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active_user_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_user_count_sync', sa.DateTime(), nullable=True),
        sa.Column('monthly_query_limit', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('queries_used_this_month', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('queries_reset_at', sa.DateTime(), nullable=True),
        sa.Column('total_queries_all_time', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('indexing_status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('indexing_started_at', sa.DateTime(), nullable=True),
        sa.Column('indexing_completed_at', sa.DateTime(), nullable=True),
        sa.Column('total_messages_indexed', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_channels_indexed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('sso_enabled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('custom_retention_days', sa.Integer(), nullable=True),
        sa.Column('dedicated_support', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('analytics_cache', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(), nullable=True),
        sa.Column('deactivated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id')
    )
    op.create_index('ix_workspaces_team_id', 'workspaces', ['team_id'])
    op.create_index('ix_workspaces_stripe_customer_id', 'workspaces', ['stripe_customer_id'])

    # Create installation_logs table
    op.create_table(
        'installation_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.String(length=50), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('event_data', sa.JSON(), nullable=True),
        sa.Column('user_id', sa.String(length=50), nullable=True),
        sa.Column('user_email', sa.String(length=255), nullable=True),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_installation_logs_team_id', 'installation_logs', ['team_id'])
    op.create_index('ix_installation_logs_event_type', 'installation_logs', ['event_type'])
    op.create_index('ix_installation_logs_created_at', 'installation_logs', ['created_at'])

    # Add indexing control columns to channels table
    op.add_column('channels', sa.Column('is_indexed', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('channels', sa.Column('indexing_enabled', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('channels', sa.Column('indexing_status', sa.String(length=50), nullable=False, server_default='pending'))
    op.add_column('channels', sa.Column('indexing_started_at', sa.DateTime(), nullable=True))
    op.add_column('channels', sa.Column('indexing_completed_at', sa.DateTime(), nullable=True))
    op.add_column('channels', sa.Column('last_message_indexed_ts', sa.String(length=50), nullable=True))


def downgrade():
    # Remove indexing control columns from channels
    op.drop_column('channels', 'last_message_indexed_ts')
    op.drop_column('channels', 'indexing_completed_at')
    op.drop_column('channels', 'indexing_started_at')
    op.drop_column('channels', 'indexing_status')
    op.drop_column('channels', 'indexing_enabled')
    op.drop_column('channels', 'is_indexed')

    # Drop installation_logs table
    op.drop_index('ix_installation_logs_created_at', 'installation_logs')
    op.drop_index('ix_installation_logs_event_type', 'installation_logs')
    op.drop_index('ix_installation_logs_team_id', 'installation_logs')
    op.drop_table('installation_logs')

    # Drop workspaces table
    op.drop_index('ix_workspaces_stripe_customer_id', 'workspaces')
    op.drop_index('ix_workspaces_team_id', 'workspaces')
    op.drop_table('workspaces')
