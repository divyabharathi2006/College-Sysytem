"""Add names to affiliated-college staff accounts.

Revision ID: 20261009_13
Revises: 20261009_12
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261009_13'
down_revision = '20261009_12'
branch_labels = None
depends_on = None


def upgrade():
    if 'display_name' not in {
        column['name'] for column in inspect(op.get_bind()).get_columns('users')
    }:
        op.add_column(
            'users',
            sa.Column('display_name', sa.String(length=120), nullable=True),
        )


def downgrade():
    if 'display_name' in {
        column['name'] for column in inspect(op.get_bind()).get_columns('users')
    }:
        op.drop_column('users', 'display_name')
