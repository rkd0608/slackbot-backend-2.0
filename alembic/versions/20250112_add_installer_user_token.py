"""Add installer_user_token for Glean-style indexing

Revision ID: 20250112_installer_token
Revises: 20250131_add_team_id_to_files
Create Date: 2025-01-12

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250112_installer_token'
down_revision = '20250131_add_team_id_to_files'
branch_labels = None
depends_on = None


def upgrade():
    """Add installer_user_token column to workspaces table"""
    # Add installer_user_token column (nullable for existing workspaces)
    op.add_column(
        'workspaces',
        sa.Column('installer_user_token', sa.Text(), nullable=True,
                  comment='Encrypted user access token for Glean-style indexing')
    )


def downgrade():
    """Remove installer_user_token column from workspaces table"""
    op.drop_column('workspaces', 'installer_user_token')
