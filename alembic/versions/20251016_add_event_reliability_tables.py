"""add event reliability tables

Revision ID: event_reliability_001
Revises: 6fd3c947f0fd
Create Date: 2025-10-16 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'event_reliability_001'
down_revision = '6fd3c947f0fd'
branch_labels = None
depends_on = None


def upgrade():
    # Create event_buffer table
    op.create_table(
        'event_buffer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.String(length=255), nullable=True),
        sa.Column('team_id', sa.String(length=255), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=True),
        sa.Column('event_data', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'queued', 'failed', name='eventbufferstatus'), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_event_buffer_id', 'event_buffer', ['id'])
    op.create_index('ix_event_buffer_event_id', 'event_buffer', ['event_id'], unique=True)
    op.create_index('ix_event_buffer_team_id', 'event_buffer', ['team_id'])
    op.create_index('ix_event_buffer_event_type', 'event_buffer', ['event_type'])
    op.create_index('ix_event_buffer_status', 'event_buffer', ['status'])
    op.create_index('ix_event_buffer_created_at', 'event_buffer', ['created_at'])
    op.create_index('idx_status_retry', 'event_buffer', ['status', 'retry_count'])
    op.create_index('idx_created_status', 'event_buffer', ['created_at', 'status'])

    # Create processed_events table
    op.create_table(
        'processed_events',
        sa.Column('event_id', sa.String(length=255), nullable=False),
        sa.Column('team_id', sa.String(length=255), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index('ix_processed_events_team_id', 'processed_events', ['team_id'])
    op.create_index('ix_processed_events_processed_at', 'processed_events', ['processed_at'])
    op.create_index('idx_processed_at_cleanup', 'processed_events', ['processed_at'])
    op.create_index('idx_team_processed', 'processed_events', ['team_id', 'processed_at'])


def downgrade():
    # Drop processed_events table
    op.drop_index('idx_team_processed', table_name='processed_events')
    op.drop_index('idx_processed_at_cleanup', table_name='processed_events')
    op.drop_index('ix_processed_events_processed_at', table_name='processed_events')
    op.drop_index('ix_processed_events_team_id', table_name='processed_events')
    op.drop_table('processed_events')

    # Drop event_buffer table
    op.drop_index('idx_created_status', table_name='event_buffer')
    op.drop_index('idx_status_retry', table_name='event_buffer')
    op.drop_index('ix_event_buffer_created_at', table_name='event_buffer')
    op.drop_index('ix_event_buffer_status', table_name='event_buffer')
    op.drop_index('ix_event_buffer_event_type', table_name='event_buffer')
    op.drop_index('ix_event_buffer_team_id', table_name='event_buffer')
    op.drop_index('ix_event_buffer_event_id', table_name='event_buffer')
    op.drop_index('ix_event_buffer_id', table_name='event_buffer')
    op.drop_table('event_buffer')
