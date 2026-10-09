"""Add operational policies and staged attendance imports.

Revision ID: 20261008_08
Revises: 20261008_07
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_08'
down_revision = '20261008_07'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if 'system_policies' not in tables:
        op.create_table(
            'system_policies',
            sa.Column('key', sa.String(length=80), primary_key=True),
            sa.Column('value', sa.String(length=20), nullable=False, server_default='false'),
            sa.Column('updated_by', sa.Integer(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if 'attendance_import_stages' not in tables:
        op.create_table(
            'attendance_import_stages',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='preview'),
            sa.Column('payload', sa.JSON(), nullable=True),
            sa.CheckConstraint(
                "status IN ('preview', 'committed', 'cancelled', 'expired', 'failed')",
                name='ck_attendance_import_stage_status',
            ),
        )
    indexes = {index['name'] for index in inspect(bind).get_indexes('attendance_import_stages')}
    for name, columns in (
        ('ix_attendance_import_stages_owner_status', ['owner_id', 'status']),
        ('ix_attendance_import_stages_expires_at', ['expires_at']),
    ):
        if name not in indexes:
            op.create_index(name, 'attendance_import_stages', columns, unique=False)


def downgrade():
    raise RuntimeError(
        'Operational policy and import audit state is retained; this migration is intentionally irreversible.'
    )