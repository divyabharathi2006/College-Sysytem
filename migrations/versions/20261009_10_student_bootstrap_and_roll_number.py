"""Add one-time student bootstrap credentials and a non-unique roll number.

Revision ID: 20261009_10
Revises: 20261008_09
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261009_10'
down_revision = '20261008_09'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    user_columns = {column['name'] for column in inspector.get_columns('users')}
    student_columns = {column['name'] for column in inspector.get_columns('students')}
    if 'must_change_password' not in user_columns:
        op.add_column(
            'users',
            sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if 'roll_number' not in student_columns:
        op.add_column('students', sa.Column('roll_number', sa.String(length=50), nullable=True))
    if 'dob_verifier' not in student_columns:
        op.add_column('students', sa.Column('dob_verifier', sa.String(length=64), nullable=True))
    indexes = {index['name'] for index in inspect(bind).get_indexes('students')}
    if 'ix_students_roll_number' not in indexes:
        op.create_index('ix_students_roll_number', 'students', ['roll_number'], unique=False)


def downgrade():
    raise RuntimeError('Student bootstrap verifier history is intentionally retained.')