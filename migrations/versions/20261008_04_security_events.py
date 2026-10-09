"""Add append-only security event history.

Revision ID: 20261008_04
Revises: 20261008_03
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_04'
down_revision = '20261008_03'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if 'security_events' not in inspect(bind).get_table_names():
        op.create_table(
            'security_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('actor_id', sa.Integer(), nullable=True),
            sa.Column('actor_role', sa.String(length=30), nullable=False),
            sa.Column('action', sa.String(length=100), nullable=False),
            sa.Column('resource_type', sa.String(length=60), nullable=False),
            sa.Column('resource_id', sa.String(length=80), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('status', sa.String(length=30), nullable=False),
            sa.Column('response_status', sa.Integer(), nullable=True),
            sa.Column('reason', sa.String(length=120), nullable=False),
            sa.Column('before_data', sa.JSON(), nullable=True),
            sa.Column('after_data', sa.JSON(), nullable=True),
            sa.Column('ip_hash', sa.String(length=64), nullable=False),
            sa.Column('user_agent', sa.String(length=255), nullable=False, server_default=''),
        )
    indexes = {index['name'] for index in inspect(bind).get_indexes('security_events')}
    for name, column in (
        ('ix_security_events_created_at', 'created_at'),
        ('ix_security_events_actor_id', 'actor_id'),
        ('ix_security_events_status', 'status'),
    ):
        if name not in indexes:
            op.create_index(name, 'security_events', [column], unique=False)

    if bind.dialect.name == 'sqlite':
        bind.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS security_events_no_update
            BEFORE UPDATE ON security_events
            BEGIN SELECT RAISE(ABORT, 'security events are append-only'); END
        """)
        bind.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS security_events_no_delete
            BEFORE DELETE ON security_events
            BEGIN SELECT RAISE(ABORT, 'security events are append-only'); END
        """)


def downgrade():
    raise RuntimeError(
        'Security event history is append-only and this migration is intentionally irreversible.'
    )
