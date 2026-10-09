"""Reserve unique email and phone values for new student registrations.

Revision ID: 20261009_11
Revises: 20261009_10
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261009_11'
down_revision = '20261009_10'
branch_labels = None
depends_on = None


def upgrade():
    if 'student_registration_contacts' not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            'student_registration_contacts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column(
                'user_id', sa.Integer(),
                sa.ForeignKey('users.id', ondelete='CASCADE'),
                nullable=False, unique=True,
            ),
            sa.Column('normalized_email', sa.String(length=120), nullable=False, unique=True),
            sa.Column('normalized_phone', sa.String(length=15), nullable=False, unique=True),
        )


def downgrade():
    raise RuntimeError(
        'Student contact claims protect unique registration contact data and are intentionally retained.'
    )
