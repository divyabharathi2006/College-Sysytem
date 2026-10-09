"""Add session-linked attendance, correction requests, and leave requests.

Revision ID: 20261008_03
Revises: 20261008_02
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20261008_03'
down_revision = '20261008_02'
branch_labels = None
depends_on = None


def _upgrade_attendance_table():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column['name'] for column in inspector.get_columns('attendance')}
    unique_constraints = inspector.get_unique_constraints('attendance')
    legacy_constraints = [
        constraint for constraint in unique_constraints
        if constraint.get('name') == 'unique_student_subject_day'
        or constraint.get('column_names') == ['student_id', 'subject_id', 'date']
    ]

    if 'session_id' not in columns or legacy_constraints:
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('attendance', recreate='always') as batch:
                if 'session_id' not in columns:
                    batch.add_column(sa.Column('session_id', sa.Integer(), nullable=True))
                    batch.create_foreign_key(
                        'fk_attendance_session_id_class_sessions', 'class_sessions', ['session_id'], ['id'],
                        ondelete='RESTRICT',
                    )
                for constraint in legacy_constraints:
                    if constraint.get('name'):
                        batch.drop_constraint(constraint['name'], type_='unique')
        else:
            if 'session_id' not in columns:
                op.add_column('attendance', sa.Column('session_id', sa.Integer(), nullable=True))
                op.create_foreign_key(
                    'fk_attendance_session_id_class_sessions', 'attendance', 'class_sessions',
                    ['session_id'], ['id'], ondelete='RESTRICT',
                )
            for constraint in legacy_constraints:
                if constraint.get('name'):
                    op.drop_constraint(constraint['name'], 'attendance', type_='unique')

    bind = op.get_bind()
    indexes = {index['name'] for index in inspect(bind).get_indexes('attendance')}
    if bind.dialect.name in {'sqlite', 'postgresql'}:
        if 'uq_attendance_legacy_student_subject_day' not in indexes:
            op.create_index(
                'uq_attendance_legacy_student_subject_day', 'attendance',
                ['student_id', 'subject_id', 'date'], unique=True,
                sqlite_where=sa.text('session_id IS NULL'),
                postgresql_where=sa.text('session_id IS NULL'),
            )
        if 'uq_attendance_session_student_subject_day' not in indexes:
            op.create_index(
                'uq_attendance_session_student_subject_day', 'attendance',
                ['student_id', 'subject_id', 'date', 'session_id'], unique=True,
                sqlite_where=sa.text('session_id IS NOT NULL'),
                postgresql_where=sa.text('session_id IS NOT NULL'),
            )
    elif 'uq_attendance_session_student_subject_day' not in indexes:
        # Other dialects get period uniqueness; legacy daily uniqueness remains
        # represented by the pre-existing application validation on those backends.
        op.create_index(
            'uq_attendance_session_student_subject_day', 'attendance',
            ['student_id', 'subject_id', 'date', 'session_id'], unique=True,
        )

    indexes = {index['name'] for index in inspect(bind).get_indexes('attendance')}
    if 'ix_attendance_session_id' not in indexes:
        op.create_index('ix_attendance_session_id', 'attendance', ['session_id'], unique=False)


def _create_correction_requests():
    bind = op.get_bind()
    if 'attendance_correction_requests' not in inspect(bind).get_table_names():
        op.create_table(
            'attendance_correction_requests',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('attendance_id', sa.Integer(), nullable=False),
            sa.Column('previous_status', sa.String(length=20), nullable=False),
            sa.Column('new_status', sa.String(length=20), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
            sa.Column('requested_by', sa.Integer(), nullable=False),
            sa.Column('requester_role', sa.String(length=30), nullable=False),
            sa.Column('requested_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('reviewed_by', sa.Integer(), nullable=True),
            sa.Column('reviewer_role', sa.String(length=30), nullable=True),
            sa.Column('reviewed_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['attendance_id'], ['attendance.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='RESTRICT'),
            sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name='ck_correction_request_status'),
        )
    indexes = {index['name'] for index in inspect(bind).get_indexes('attendance_correction_requests')}
    if 'ix_attendance_correction_requests_attendance_id' not in indexes:
        op.create_index('ix_attendance_correction_requests_attendance_id', 'attendance_correction_requests', ['attendance_id'])
    if 'ix_attendance_correction_requests_status' not in indexes:
        op.create_index('ix_attendance_correction_requests_status', 'attendance_correction_requests', ['status'])
    if 'uq_attendance_correction_pending' not in indexes:
        if bind.dialect.name in {'sqlite', 'postgresql'}:
            op.create_index(
                'uq_attendance_correction_pending', 'attendance_correction_requests', ['attendance_id'],
                unique=True, sqlite_where=sa.text("status = 'PENDING'"),
                postgresql_where=sa.text("status = 'PENDING'"),
            )


def _create_leave_requests():
    bind = op.get_bind()
    if 'leave_requests' not in inspect(bind).get_table_names():
        op.create_table(
            'leave_requests',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('student_id', sa.Integer(), nullable=False),
            sa.Column('starts_on', sa.Date(), nullable=False),
            sa.Column('ends_on', sa.Date(), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
            sa.Column('requested_by', sa.Integer(), nullable=False),
            sa.Column('requested_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('reviewed_by', sa.Integer(), nullable=True),
            sa.Column('reviewer_role', sa.String(length=30), nullable=True),
            sa.Column('reviewed_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='RESTRICT'),
            sa.CheckConstraint('starts_on <= ends_on', name='ck_leave_request_dates'),
            sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name='ck_leave_request_status'),
        )
    indexes = {index['name'] for index in inspect(bind).get_indexes('leave_requests')}
    if 'ix_leave_requests_student_id' not in indexes:
        op.create_index('ix_leave_requests_student_id', 'leave_requests', ['student_id'])
    if 'ix_leave_requests_status' not in indexes:
        op.create_index('ix_leave_requests_status', 'leave_requests', ['status'])


def upgrade():
    _upgrade_attendance_table()
    _create_correction_requests()
    _create_leave_requests()


def downgrade():
    raise RuntimeError(
        'This data-preserving workflow migration is intentionally irreversible; do not drop attendance requests or session history.'
    )
