"""Add scoped institutional announcements.

Revision ID: 20261008_09
Revises: 20261008_08
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_09'
down_revision = '20261008_08'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if 'announcements' not in tables:
        op.create_table(
            'announcements',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('title', sa.String(length=160), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('target_type', sa.String(length=20), nullable=False),
            sa.Column('department_id', sa.Integer(), sa.ForeignKey('departments.id', ondelete='RESTRICT'), nullable=True),
            sa.Column('section_id', sa.Integer(), sa.ForeignKey('sections.id', ondelete='RESTRICT'), nullable=True),
            sa.Column('creator_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.CheckConstraint(
                "target_type IN ('institution', 'department', 'section')",
                name='ck_announcement_target_type',
            ),
            sa.CheckConstraint(
                "(target_type = 'institution' AND department_id IS NULL AND section_id IS NULL) OR "
                "(target_type = 'department' AND department_id IS NOT NULL AND section_id IS NULL) OR "
                "(target_type = 'section' AND department_id IS NOT NULL AND section_id IS NOT NULL)",
                name='ck_announcement_target_scope',
            ),
            sa.CheckConstraint('length(trim(title)) BETWEEN 1 AND 160', name='ck_announcement_title_length'),
            sa.CheckConstraint('length(trim(body)) BETWEEN 1 AND 5000', name='ck_announcement_body_length'),
            sa.CheckConstraint('expires_at IS NULL OR expires_at > created_at', name='ck_announcement_expiry'),
        )
    indexes = {index['name'] for index in inspect(bind).get_indexes('announcements')}
    for name, columns in (
        ('ix_announcements_department_id', ['department_id']),
        ('ix_announcements_section_id', ['section_id']),
        ('ix_announcements_creator_id', ['creator_id']),
        ('ix_announcements_created_at', ['created_at']),
        ('ix_announcements_expires_at', ['expires_at']),
        ('ix_announcement_active_created', ['is_active', 'created_at']),
    ):
        if name not in indexes:
            op.create_index(name, 'announcements', columns, unique=False)


def downgrade():
    raise RuntimeError(
        'Announcement history is retained; this additive migration is intentionally irreversible.'
    )
