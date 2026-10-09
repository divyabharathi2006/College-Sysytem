"""Link affiliated-college accounts to their college scope.

Revision ID: 20261009_12
Revises: 20261009_11
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261009_12'
down_revision = '20261009_11'
branch_labels = None
depends_on = None


def upgrade():
    if 'affiliated_college_code' not in {
        column['name'] for column in inspect(op.get_bind()).get_columns('users')
    }:
        op.add_column(
            'users',
            sa.Column('affiliated_college_code', sa.String(length=30), nullable=True),
        )
        op.create_index(
            'ix_users_affiliated_college_code',
            'users',
            ['affiliated_college_code'],
        )


def downgrade():
    if 'affiliated_college_code' in {
        column['name'] for column in inspect(op.get_bind()).get_columns('users')
    }:
        op.drop_index('ix_users_affiliated_college_code', table_name='users')
        op.drop_column('users', 'affiliated_college_code')
