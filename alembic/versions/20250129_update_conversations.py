"""Update conversations table with context fields

Revision ID: 20250129_conversations
Revises: 20250129_user_prefs
Create Date: 2025-01-29 21:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20250129_conversations'
down_revision = '20250129_user_prefs'
branch_labels = None
depends_on = None


def upgrade():
    """Add context tracking fields to conversations table"""
    # Add new columns
    op.add_column('conversations', sa.Column('team_id', sa.String(100), nullable=True))
    op.add_column('conversations', sa.Column('channel_id', sa.String(100), nullable=True))
    op.add_column('conversations', sa.Column('message_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('conversations', sa.Column('context', sa.JSON(), nullable=True))

    # Create index for team_id
    op.create_index('idx_team_id', 'conversations', ['team_id'])

    # Update existing rows to have a default team_id (you may need to customize this)
    # For now, we'll leave them as NULL and they'll be updated on next use


def downgrade():
    """Remove context tracking fields"""
    op.drop_index('idx_team_id', table_name='conversations')
    op.drop_column('conversations', 'context')
    op.drop_column('conversations', 'message_count')
    op.drop_column('conversations', 'channel_id')
    op.drop_column('conversations', 'team_id')
