"""Add learning signal tables

Revision ID: 20250129_learning
Revises: 20250124_entity_graph
Create Date: 2025-01-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '20250129_learning'
down_revision = '20250124_entity_graph'
branch_labels = None
depends_on = None


def upgrade():
    # Create learning_signals table
    op.create_table(
        'learning_signals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('signal_id', sa.String(100), nullable=False),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('query_id', sa.String(100), nullable=True),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('signal_type', sa.String(50), nullable=False),
        sa.Column('signal_strength', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('signal_context', sa.JSON(), nullable=True),
        sa.Column('retrieval_method', sa.String(50), nullable=True),
        sa.Column('retrieval_score', sa.Float(), nullable=True),
        sa.Column('results_count', sa.Integer(), nullable=True),
        sa.Column('query_intent', sa.String(50), nullable=True),
        sa.Column('query_entities', sa.JSON(), nullable=True),
        sa.Column('query_embedding', sa.JSON(), nullable=True),
        sa.Column('learned_from', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('learning_iteration', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('signal_id')
    )

    # Indexes for learning_signals
    op.create_index('idx_signal_id', 'learning_signals', ['signal_id'])
    op.create_index('idx_ls_team_id', 'learning_signals', ['team_id'])
    op.create_index('idx_ls_user_id', 'learning_signals', ['user_id'])
    op.create_index('idx_ls_query_id', 'learning_signals', ['query_id'])
    op.create_index('idx_ls_signal_type', 'learning_signals', ['signal_type'])
    op.create_index('idx_ls_created_at', 'learning_signals', ['created_at'])
    op.create_index('idx_signal_type_created', 'learning_signals', ['signal_type', 'created_at'])
    op.create_index('idx_team_signal_type', 'learning_signals', ['team_id', 'signal_type'])
    op.create_index('idx_team_learned', 'learning_signals', ['team_id', 'learned_from'])
    op.create_index('idx_query_signal', 'learning_signals', ['query_id', 'signal_type'])

    # Create successful_query_patterns table
    op.create_table(
        'successful_query_patterns',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('pattern_id', sa.String(100), nullable=False),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('pattern_type', sa.String(50), nullable=False),
        sa.Column('query_template', sa.Text(), nullable=True),
        sa.Column('query_examples', sa.JSON(), nullable=True),
        sa.Column('success_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('total_uses', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('success_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('avg_user_rating', sa.Float(), nullable=True),
        sa.Column('winning_strategy', sa.JSON(), nullable=True),
        sa.Column('entities_present', sa.JSON(), nullable=True),
        sa.Column('intent_type', sa.String(50), nullable=True),
        sa.Column('temporal_filter', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('channel_filter', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('confidence_score', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('last_used', sa.DateTime(), nullable=True),
        sa.Column('times_applied', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pattern_id')
    )

    # Indexes for successful_query_patterns
    op.create_index('idx_sqp_pattern_id', 'successful_query_patterns', ['pattern_id'])
    op.create_index('idx_sqp_team_id', 'successful_query_patterns', ['team_id'])
    op.create_index('idx_sqp_pattern_type', 'successful_query_patterns', ['pattern_type'])
    op.create_index('idx_pattern_type_success', 'successful_query_patterns', ['pattern_type', 'success_rate'])
    op.create_index('idx_team_pattern', 'successful_query_patterns', ['team_id', 'pattern_type'])
    op.create_index('idx_sqp_confidence', 'successful_query_patterns', ['confidence_score'])

    # Create failed_query_patterns table
    op.create_table(
        'failed_query_patterns',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('pattern_id', sa.String(100), nullable=False),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('failure_type', sa.String(50), nullable=False),
        sa.Column('query_template', sa.Text(), nullable=True),
        sa.Column('query_examples', sa.JSON(), nullable=True),
        sa.Column('failure_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('total_occurrences', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('failure_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('failure_reasons', sa.JSON(), nullable=True),
        sa.Column('improvement_suggestions', sa.JSON(), nullable=True),
        sa.Column('entities_present', sa.JSON(), nullable=True),
        sa.Column('intent_type', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pattern_id')
    )

    # Indexes for failed_query_patterns
    op.create_index('idx_fqp_pattern_id', 'failed_query_patterns', ['pattern_id'])
    op.create_index('idx_fqp_team_id', 'failed_query_patterns', ['team_id'])
    op.create_index('idx_fqp_failure_type', 'failed_query_patterns', ['failure_type'])
    op.create_index('idx_failure_type_rate', 'failed_query_patterns', ['failure_type', 'failure_rate'])
    op.create_index('idx_team_failure', 'failed_query_patterns', ['team_id', 'failure_type'])

    # Create query_rewrite_rules table
    op.create_table(
        'query_rewrite_rules',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('rule_id', sa.String(100), nullable=False),
        sa.Column('team_id', sa.String(50), nullable=False),
        sa.Column('rule_type', sa.String(50), nullable=False),
        sa.Column('trigger_pattern', sa.Text(), nullable=False),
        sa.Column('trigger_intent', sa.String(50), nullable=True),
        sa.Column('rewrite_template', sa.Text(), nullable=False),
        sa.Column('rewrite_examples', sa.JSON(), nullable=True),
        sa.Column('applications_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('success_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('improvement_rate', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('avg_score_improvement', sa.Float(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        sa.Column('last_applied', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('rule_id')
    )

    # Indexes for query_rewrite_rules
    op.create_index('idx_qrr_rule_id', 'query_rewrite_rules', ['rule_id'])
    op.create_index('idx_qrr_team_id', 'query_rewrite_rules', ['team_id'])
    op.create_index('idx_qrr_rule_type', 'query_rewrite_rules', ['rule_type'])
    op.create_index('idx_qrr_is_active', 'query_rewrite_rules', ['is_active'])
    op.create_index('idx_rule_type_active', 'query_rewrite_rules', ['rule_type', 'is_active'])
    op.create_index('idx_team_active', 'query_rewrite_rules', ['team_id', 'is_active'])
    op.create_index('idx_qrr_improvement', 'query_rewrite_rules', ['improvement_rate'])


def downgrade():
    # Drop tables in reverse order
    op.drop_table('query_rewrite_rules')
    op.drop_table('failed_query_patterns')
    op.drop_table('successful_query_patterns')
    op.drop_table('learning_signals')
