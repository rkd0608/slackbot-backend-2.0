"""Add authentication fields to users and create api_keys table

Revision ID: 20251006_2330
Revises: 20251006_2300
Create Date: 2025-10-06 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20251006_2330'
down_revision = '20251006_2300'
branch_labels = None
depends_on = None


def upgrade():
    # Add authentication columns to users table
    op.add_column('users', sa.Column('password_hash', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('role', sa.String(50), nullable=False, server_default='member'))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(), nullable=True))

    # Create api_keys table
    op.create_table(
        'api_keys',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('key_hash', sa.String(255), nullable=False),
        sa.Column('key_prefix', sa.String(20), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('created_by_user_id', sa.BigInteger(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('usage_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )

    # Create indexes for api_keys
    op.create_index('idx_api_keys_key_hash', 'api_keys', ['key_hash'], unique=True)
    op.create_index('idx_api_keys_team_id', 'api_keys', ['team_id'])
    op.create_index('idx_api_keys_created_by', 'api_keys', ['created_by_user_id'])


def downgrade():
    # Drop api_keys table
    op.drop_index('idx_api_keys_created_by', table_name='api_keys')
    op.drop_index('idx_api_keys_team_id', table_name='api_keys')
    op.drop_index('idx_api_keys_key_hash', table_name='api_keys')
    op.drop_table('api_keys')

    # Remove authentication columns from users table
    op.drop_column('users', 'last_login_at')
    op.drop_column('users', 'role')
    op.drop_column('users', 'password_hash')
