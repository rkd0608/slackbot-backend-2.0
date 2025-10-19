"""add profile_completed to users

Revision ID: add_profile_completed
Revises: add_company_info
Create Date: 2025-10-12

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_profile_completed'
down_revision = 'add_company_info'
branch_labels = None
depends_on = None


def upgrade():
    """Add profile_completed column to users table"""

    # Add profile_completed column (0=incomplete, 1=complete)
    op.add_column('users', sa.Column('profile_completed', sa.Integer, nullable=False, server_default='0'))

    # Set existing users to profile_completed=1 (they've already been through setup)
    op.execute("UPDATE users SET profile_completed = 1 WHERE password_hash IS NOT NULL")


def downgrade():
    """Remove profile_completed column from users table"""

    op.drop_column('users', 'profile_completed')
