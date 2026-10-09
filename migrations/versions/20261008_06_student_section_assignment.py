"""Add explicit student section links and safely backfill unambiguous legacy rows.

Revision ID: 20261008_06
Revises: 20261008_05
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_06'
down_revision = '20261008_05'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column['name'] for column in inspect(bind).get_columns('students')}
    if 'section_id' not in columns:
        if bind.dialect.name == 'sqlite':
            # Alembic emits a second ALTER for ForeignKey metadata, which SQLite
            # cannot apply. A nullable REFERENCES column is supported natively
            # and avoids rebuilding students or disturbing attendance FKs.
            bind.exec_driver_sql(
                'ALTER TABLE "students" ADD COLUMN "section_id" INTEGER '
                'REFERENCES "sections" ("id") ON DELETE RESTRICT'
            )
        else:
            op.add_column('students', sa.Column('section_id', sa.Integer(), nullable=True))
            op.create_foreign_key(
                'fk_students_section_id_sections', 'students', 'sections', ['section_id'], ['id'], ondelete='RESTRICT'
            )
    indexes = {index['name'] for index in inspect(bind).get_indexes('students')}
    if 'ix_students_section_id' not in indexes:
        op.create_index('ix_students_section_id', 'students', ['section_id'], unique=False)

    # Link only when both legacy values identify one department and one section
    # in that department. Do not rewrite the legacy department/section text.
    bind.execute(sa.text('''
        UPDATE students
        SET section_id = (
            SELECT sections.id
            FROM sections
            JOIN batches ON batches.id = sections.batch_id
            JOIN courses ON courses.id = batches.course_id
            WHERE lower(trim(sections.code)) = lower(trim(students.section))
              AND courses.department_id = CASE
                  WHEN students.department_id IS NOT NULL THEN students.department_id
                  WHEN (
                      SELECT COUNT(*) FROM departments
                      WHERE lower(trim(departments.code)) = lower(trim(students.department))
                         OR lower(trim(departments.name)) = lower(trim(students.department))
                  ) = 1 THEN (
                      SELECT MIN(departments.id) FROM departments
                      WHERE lower(trim(departments.code)) = lower(trim(students.department))
                         OR lower(trim(departments.name)) = lower(trim(students.department))
                  )
                  ELSE NULL
              END
        )
        WHERE section_id IS NULL
          AND (
              SELECT COUNT(*)
              FROM sections
              JOIN batches ON batches.id = sections.batch_id
              JOIN courses ON courses.id = batches.course_id
              WHERE lower(trim(sections.code)) = lower(trim(students.section))
                AND courses.department_id = CASE
                    WHEN students.department_id IS NOT NULL THEN students.department_id
                    WHEN (
                        SELECT COUNT(*) FROM departments
                        WHERE lower(trim(departments.code)) = lower(trim(students.department))
                           OR lower(trim(departments.name)) = lower(trim(students.department))
                    ) = 1 THEN (
                        SELECT MIN(departments.id) FROM departments
                        WHERE lower(trim(departments.code)) = lower(trim(students.department))
                           OR lower(trim(departments.name)) = lower(trim(students.department))
                    )
                    ELSE NULL
                END
          ) = 1
    '''))


def downgrade():
    raise RuntimeError(
        'Student section assignments are retained; this additive migration is intentionally irreversible.'
    )
