"""Adopt the existing attendance schema and add academic hierarchy foundations.

Revision ID: 20261008_01
Revises:
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # For a legacy installation this is a no-op for present tables, not a reset.
    # On an empty installation it creates the current schema as the baseline.
    from extensions import db
    import models  # noqa: F401

    db.metadata.create_all(bind=bind, checkfirst=True)

    inspector = inspect(bind)
    user_columns = {column['name'] for column in inspector.get_columns('users')}
    if 'department_id' not in user_columns:
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('users', recreate='always') as batch_op:
                batch_op.add_column(sa.Column('department_id', sa.Integer(), nullable=True))
                batch_op.create_foreign_key('fk_users_department_id_departments', 'departments', ['department_id'], ['id'])
        else:
            op.add_column('users', sa.Column('department_id', sa.Integer(), nullable=True))
            op.create_foreign_key('fk_users_department_id_departments', 'users', 'departments', ['department_id'], ['id'])
    user_indexes = {index['name'] for index in inspect(bind).get_indexes('users')}
    if 'ix_users_department_id' not in user_indexes:
        op.create_index('ix_users_department_id', 'users', ['department_id'], unique=False)

    student_columns = {column['name'] for column in inspector.get_columns('students')}
    if 'department_id' not in student_columns:
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('students', recreate='always') as batch_op:
                batch_op.add_column(sa.Column('department_id', sa.Integer(), nullable=True))
                batch_op.create_foreign_key('fk_students_department_id_departments', 'departments', ['department_id'], ['id'])
        else:
            op.add_column('students', sa.Column('department_id', sa.Integer(), nullable=True))
            op.create_foreign_key('fk_students_department_id_departments', 'students', 'departments', ['department_id'], ['id'])
    student_indexes = {index['name'] for index in inspect(bind).get_indexes('students')}
    if 'ix_students_department_id' not in student_indexes:
        op.create_index('ix_students_department_id', 'students', ['department_id'], unique=False)

    # Normalize role strings in-place. IDs, usernames, password hashes, profiles,
    # attendance, and all other user data remain untouched.
    op.execute(sa.text("UPDATE users SET role = 'SUPER_ADMIN' WHERE lower(role) = 'admin'"))
    op.execute(sa.text("UPDATE users SET role = 'FACULTY' WHERE lower(role) = 'faculty'"))
    op.execute(sa.text("UPDATE users SET role = 'STUDENT' WHERE lower(role) = 'student'"))


def downgrade():
    raise RuntimeError(
        'The baseline adoption migration is intentionally irreversible to protect existing records and department associations.'
    )
