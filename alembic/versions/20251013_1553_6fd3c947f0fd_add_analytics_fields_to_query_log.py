"""add_analytics_fields_to_query_log

Revision ID: 6fd3c947f0fd
Revises: add_profile_completed
Create Date: 2025-10-13 15:53:16.751979

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6fd3c947f0fd'
down_revision = 'add_profile_completed'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add team_id column
    op.add_column('query_logs', sa.Column('team_id', sa.String(length=50), nullable=True))

    # Add feedback_type column
    op.add_column('query_logs', sa.Column('feedback_type', sa.String(length=20), nullable=True))

    # Add topic_tags column
    op.add_column('query_logs', sa.Column('topic_tags', sa.JSON(), nullable=True))

    # Create indexes
    op.create_index('idx_team_created', 'query_logs', ['team_id', 'created_at'])
    op.create_index('idx_team_rating', 'query_logs', ['team_id', 'user_rating'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_team_rating', table_name='query_logs')
    op.drop_index('idx_team_created', table_name='query_logs')

    # Drop columns
    op.drop_column('query_logs', 'topic_tags')
    op.drop_column('query_logs', 'feedback_type')
    op.drop_column('query_logs', 'team_id')
