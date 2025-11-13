"""Create team_vocabulary table

Revision ID: 20250129_team_vocabulary
Revises: 20250129_conversations
Create Date: 2025-01-29 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20250129_team_vocabulary'
down_revision = '20250129_conversations'
branch_labels = None
depends_on = None


def upgrade():
    """Create team_vocabulary table"""
    op.create_table(
        'team_vocabulary',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('team_id', sa.String(100), nullable=False, index=True),
        sa.Column('term', sa.String(200), nullable=False),
        sa.Column('canonical_form', sa.String(500), nullable=False),
        sa.Column('term_type', sa.String(50), nullable=False, server_default='abbreviation'),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('confidence_score', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('context_examples', sa.JSON(), nullable=True),
        sa.Column('learned_from', sa.String(50), nullable=False, server_default='usage'),
        sa.Column('first_seen', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_seen', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('is_active', sa.String(10), nullable=False, server_default='true'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('idx_team_term_unique', 'team_vocabulary', ['team_id', 'term'], unique=True)
    op.create_index('idx_team_confidence', 'team_vocabulary', ['team_id', 'confidence_score'])
    op.create_index('idx_team_type', 'team_vocabulary', ['team_id', 'term_type'])


def downgrade():
    """Drop team_vocabulary table"""
    op.drop_index('idx_team_type', table_name='team_vocabulary')
    op.drop_index('idx_team_confidence', table_name='team_vocabulary')
    op.drop_index('idx_team_term_unique', table_name='team_vocabulary')
    op.drop_table('team_vocabulary')
