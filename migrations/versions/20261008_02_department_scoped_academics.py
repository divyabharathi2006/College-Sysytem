"""Add department links to faculty and classrooms without changing legacy text.

Revision ID: 20261008_02
Revises: 20261008_01
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_02'
down_revision = '20261008_01'
branch_labels = None
depends_on = None


def _add_department_link(table):
    bind = op.get_bind()
    columns = {column['name'] for column in inspect(bind).get_columns(table)}
    if 'department_id' not in columns:
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table(table, recreate='always') as batch:
                batch.add_column(sa.Column('department_id', sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    f'fk_{table}_department_id_departments', 'departments', ['department_id'], ['id']
                )
        else:
            op.add_column(table, sa.Column('department_id', sa.Integer(), nullable=True))
            op.create_foreign_key(
                f'fk_{table}_department_id_departments', table, 'departments', ['department_id'], ['id']
            )
    indexes = {index['name'] for index in inspect(bind).get_indexes(table)}
    index_name = f'ix_{table}_department_id'
    if index_name not in indexes:
        op.create_index(index_name, table, ['department_id'], unique=False)


def upgrade():
    _add_department_link('faculty')
    _add_department_link('classrooms')
    bind = op.get_bind()
    for table in ('students', 'faculty'):
        bind.execute(sa.text(f'''
            UPDATE {table}
            SET department_id = (
                SELECT MIN(departments.id)
                FROM departments
                WHERE lower(trim(departments.code)) = lower(trim({table}.department))
                   OR lower(trim(departments.name)) = lower(trim({table}.department))
            )
            WHERE department_id IS NULL
              AND (
                SELECT COUNT(*)
                FROM departments
                WHERE lower(trim(departments.code)) = lower(trim({table}.department))
                   OR lower(trim(departments.name)) = lower(trim({table}.department))
              ) = 1
        '''))
    bind.execute(sa.text('''
        UPDATE users
        SET department_id = (
            SELECT students.department_id FROM students WHERE students.user_id = users.id
        )
        WHERE department_id IS NULL AND lower(role) = 'student'
          AND EXISTS (SELECT 1 FROM students WHERE students.user_id = users.id AND students.department_id IS NOT NULL)
    '''))
    bind.execute(sa.text('''
        UPDATE users
        SET department_id = (
            SELECT faculty.department_id FROM faculty WHERE faculty.user_id = users.id
        )
        WHERE department_id IS NULL AND lower(role) = 'faculty'
          AND EXISTS (SELECT 1 FROM faculty WHERE faculty.user_id = users.id AND faculty.department_id IS NOT NULL)
    '''))


def downgrade():
    bind = op.get_bind()
    for table in ('classrooms', 'faculty'):
        columns = {column['name'] for column in inspect(bind).get_columns(table)}
        if 'department_id' not in columns:
            continue
        index_name = f'ix_{table}_department_id'
        indexes = {index['name'] for index in inspect(bind).get_indexes(table)}
        if index_name in indexes:
            op.drop_index(index_name, table_name=table)
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table(table, recreate='always') as batch:
                batch.drop_column('department_id')
        else:
            op.drop_constraint(f'fk_{table}_department_id_departments', table, type_='foreignkey')
            op.drop_column(table, 'department_id')