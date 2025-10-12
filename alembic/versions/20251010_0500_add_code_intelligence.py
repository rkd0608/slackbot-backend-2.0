"""Add code intelligence tables

Revision ID: 20251010_0500
Revises: 98c030b0b8a6
Create Date: 2025-10-10 05:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20251010_0500'
down_revision = '98c030b0b8a6'
branch_labels = None
depends_on = None


def upgrade():
    # Create code_snippets table
    op.create_table(
        'code_snippets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('snippet_id', sa.String(100), nullable=False, index=True, unique=True),
        sa.Column('message_id', sa.String(50), nullable=False, index=True),
        sa.Column('snippet_index', sa.Integer(), nullable=False, default=0),
        sa.Column('language', sa.String(50), nullable=True, index=True),
        sa.Column('code_type', sa.String(50), nullable=True, index=True),  # function, class, import, inline
        sa.Column('raw_code', mysql.LONGTEXT(), nullable=False),
        sa.Column('extracted_metadata', sa.JSON(), nullable=True),
        sa.Column('vector_id', sa.String(100), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['message_id'], ['messages.message_id'], ondelete='CASCADE'),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_0900_ai_ci'
    )

    # Add indexes for performance
    op.create_index('idx_code_message_id', 'code_snippets', ['message_id'])
    op.create_index('idx_code_language', 'code_snippets', ['language'])
    op.create_index('idx_code_type', 'code_snippets', ['code_type'])
    op.create_index('idx_code_snippet_id', 'code_snippets', ['snippet_id'])

    # Add new column to messages table
    op.add_column('messages', sa.Column('code_snippet_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    # Remove column from messages
    op.drop_column('messages', 'code_snippet_count')

    # Drop indexes
    op.drop_index('idx_code_snippet_id', table_name='code_snippets')
    op.drop_index('idx_code_type', table_name='code_snippets')
    op.drop_index('idx_code_language', table_name='code_snippets')
    op.drop_index('idx_code_message_id', table_name='code_snippets')

    # Drop table
    op.drop_table('code_snippets')
