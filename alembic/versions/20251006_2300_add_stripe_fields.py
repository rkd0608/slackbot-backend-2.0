"""Add Stripe payment fields to workspaces

Revision ID: 20251006_2300
Revises: 20251006_2200
Create Date: 2025-10-06 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20251006_2300'
down_revision = '20251006_2200'
branch_labels = None
depends_on = None


def upgrade():
    # Add Stripe payment-related columns to workspaces table
    op.add_column('workspaces', sa.Column('last_payment_date', sa.DateTime(), nullable=True))
    op.add_column('workspaces', sa.Column('last_payment_amount', sa.Float(), nullable=True))
    op.add_column('workspaces', sa.Column('cancelled_at', sa.DateTime(), nullable=True))


def downgrade():
    # Remove Stripe payment columns
    op.drop_column('workspaces', 'cancelled_at')
    op.drop_column('workspaces', 'last_payment_amount')
    op.drop_column('workspaces', 'last_payment_date')
