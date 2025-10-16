"""add company info to workspaces

Revision ID: add_company_info
Revises: add_team_id_kg
Create Date: 2025-10-12

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_company_info'
down_revision = 'add_team_id_kg'
branch_labels = None
depends_on = None


def upgrade():
    """Add company_name and company_size columns to workspaces table"""

    # Add company_name column
    op.add_column('workspaces', sa.Column('company_name', sa.String(255), nullable=True))

    # Add company_size column (e.g., "1-10", "11-50", "51-200", "201-500", "501+")
    op.add_column('workspaces', sa.Column('company_size', sa.String(50), nullable=True))


def downgrade():
    """Remove company_name and company_size columns from workspaces table"""

    op.drop_column('workspaces', 'company_size')
    op.drop_column('workspaces', 'company_name')
