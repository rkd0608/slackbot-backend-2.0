"""Add user preferences table

Revision ID: 20250129_user_prefs
Revises: 20250129_learning
Create Date: 2025-01-29 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20250129_user_prefs'
down_revision = '20250129_learning'
branch_labels = None
depends_on = None


def upgrade():
    """Add user_preferences table"""
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(100), nullable=False),
        sa.Column('team_id', sa.String(100), nullable=False),

        # Daily Digest Settings
        sa.Column('daily_digest_enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('digest_frequency', sa.String(20), nullable=False, server_default='daily'),
        sa.Column('digest_delivery_time', sa.String(10), nullable=True, server_default='09:00'),
        sa.Column('digest_timezone', sa.String(50), nullable=True, server_default='UTC'),

        # Content Preferences
        sa.Column('include_hot_topics', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('include_discussions', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('include_github_links', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('include_questions', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('include_mentions', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('only_my_channels', sa.Boolean(), nullable=False, server_default='0'),

        # Thresholds
        sa.Column('min_topic_mentions', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('min_discussion_replies', sa.Integer(), nullable=False, server_default='3'),

        # Notification Settings
        sa.Column('proactive_suggestions_enabled', sa.Boolean(), nullable=False, server_default='1'),

        # Metadata
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.Column('last_digest_sent', sa.DateTime(), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )

    # Create indexes
    op.create_index('idx_user_team_unique', 'user_preferences', ['user_id', 'team_id'], unique=True)
    op.create_index('idx_digest_enabled', 'user_preferences', ['team_id', 'daily_digest_enabled'])


def downgrade():
    """Remove user_preferences table"""
    op.drop_index('idx_digest_enabled', table_name='user_preferences')
    op.drop_index('idx_user_team_unique', table_name='user_preferences')
    op.drop_table('user_preferences')
