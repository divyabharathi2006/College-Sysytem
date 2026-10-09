"""Add prediction history and in-app notification persistence.

Revision ID: 20261008_05
Revises: 20261008_04
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_05'
down_revision = '20261008_04'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if 'prediction_history' not in tables:
        op.create_table(
            'prediction_history',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('student_id', sa.Integer(), sa.ForeignKey('students.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('model_version', sa.String(length=40), nullable=False),
            sa.Column('feature_snapshot', sa.JSON(), nullable=False),
            sa.Column('current_percentage', sa.Float(), nullable=False),
            sa.Column('predicted_percentage', sa.Float(), nullable=False),
            sa.Column('risk', sa.String(length=20), nullable=False),
        )
    if 'notification_preferences' not in tables:
        op.create_table(
            'notification_preferences',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('leave_updates', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('correction_updates', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('security_alerts', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if 'notifications' not in tables:
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('category', sa.String(length=30), nullable=False),
            sa.Column('title', sa.String(length=120), nullable=False),
            sa.Column('message', sa.String(length=500), nullable=False),
            sa.Column('link', sa.String(length=200), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.Column('dismissed_at', sa.DateTime(), nullable=True),
        )
    for table, name, columns in (
        ('prediction_history', 'ix_prediction_history_student_id', ['student_id']),
        ('prediction_history', 'ix_prediction_history_created_at', ['created_at']),
        ('prediction_history', 'ix_prediction_history_student_created', ['student_id', 'created_at']),
        ('notifications', 'ix_notifications_user_id', ['user_id']),
        ('notifications', 'ix_notifications_created_at', ['created_at']),
        ('notifications', 'ix_notifications_user_created', ['user_id', 'created_at']),
        ('notifications', 'ix_notifications_user_dismissed', ['user_id', 'dismissed_at']),
    ):
        indexes = {index['name'] for index in inspect(bind).get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns, unique=False)


def downgrade():
    raise RuntimeError(
        'Prediction and notification history is retained; this additive migration is intentionally irreversible.'
    )