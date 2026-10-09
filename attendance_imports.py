"""Validated, owner-bound staging for attendance CSV imports."""

import csv
from datetime import datetime, timedelta
from io import StringIO

from sqlalchemy.exc import IntegrityError

from extensions import db
from models.attendance import Attendance
from models.operational import AttendanceImportStage
from models.student import Student
from models.subject import Subject
from models.academic import Department

_REQUIRED_COLUMNS = {'register_number', 'subject_code', 'date', 'status'}
_SUPPORTED_STATUSES = {'PRESENT', 'ABSENT', 'LATE', 'EXCUSED', 'OD', 'LEAVE'}


class ImportValidationError(ValueError):
    """A safe user-facing reason an upload cannot be staged."""


def _department_key(student_or_subject):
    aliases = set()
    department_id = getattr(student_or_subject, 'department_id', None)
    if department_id is not None:
        department = db.session.get(Department, department_id)
        aliases.add(f'id:{department_id}')
        if department:
            aliases.update({department.code.strip().casefold(), department.name.strip().casefold()})
    department_text = str(getattr(student_or_subject, 'department', '') or '').strip().casefold()
    if department_text:
        aliases.add(department_text)
        matches = Department.query.filter(db.or_(
            db.func.lower(db.func.trim(Department.code)) == department_text,
            db.func.lower(db.func.trim(Department.name)) == department_text,
        )).all()
        if len(matches) == 1:
            aliases.update({matches[0].code.strip().casefold(), matches[0].name.strip().casefold()})
            aliases.add(f'id:{matches[0].id}')
    return aliases


def validate_csv_upload(upload, filename):
    """Validate a CSV upload completely and return accepted rows plus row errors."""
    from flask import current_app

    if not filename or not filename.lower().endswith('.csv'):
        raise ImportValidationError('Only .csv attendance files are supported.')
    maximum_bytes = int(current_app.config.get('MAX_ATTENDANCE_IMPORT_BYTES', 2 * 1024 * 1024))
    content = upload.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise ImportValidationError(f'CSV exceeds the {maximum_bytes}-byte attendance import limit.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ImportValidationError('CSV must be UTF-8 encoded.') from error

    try:
        reader = csv.DictReader(StringIO(text, newline=''), strict=True)
        headers = reader.fieldnames or []
        normalized_headers = [str(header or '').strip().lower() for header in headers]
        if len(set(normalized_headers)) != len(normalized_headers):
            raise ImportValidationError('CSV column names must be unique after normalization.')
        if not _REQUIRED_COLUMNS.issubset(normalized_headers):
            raise ImportValidationError('CSV must include register_number, subject_code, date, and status columns.')
        unknown = set(normalized_headers) - _REQUIRED_COLUMNS
        if unknown:
            raise ImportValidationError('CSV contains unsupported columns; only the current attendance importer columns are accepted.')
        reader.fieldnames = normalized_headers
        maximum_rows = int(current_app.config.get('MAX_ATTENDANCE_IMPORT_ROWS', 2000))
        accepted = []
        errors = []
        seen = set()

        row_count = 0
        for raw in reader:
            line = reader.line_num
            row_count += 1
            if row_count > maximum_rows:
                raise ImportValidationError(f'CSV exceeds the {maximum_rows}-row attendance import limit.')
            if raw.get(None):
                errors.append({'row': line, 'error': 'Unexpected extra column values.'})
                continue
            row = {key: str(value or '').strip() for key, value in raw.items() if key is not None}
            if not any(row.values()):
                continue
            register_number = row.get('register_number', '').upper()
            subject_code = row.get('subject_code', '').upper()
            student = Student.query.filter_by(register_number=register_number).first() if register_number else None
            subject = Subject.query.filter_by(subject_code=subject_code).first() if subject_code else None
            row_error = None
            if not register_number or not subject_code or not row.get('date') or not row.get('status'):
                row_error = 'Required row values must not be blank.'
            elif not student or not subject:
                row_error = 'Register number or subject code does not exist.'
            else:
                try:
                    record_date = datetime.strptime(row['date'], '%Y-%m-%d').date()
                except (TypeError, ValueError):
                    row_error = 'Date must use YYYY-MM-DD format.'
                status = row.get('status', '').upper()
                if row_error is None and status not in _SUPPORTED_STATUSES:
                    row_error = 'Attendance status is not supported.'
                if row_error is None and not _department_key(student).intersection(_department_key(subject)):
                    row_error = 'Student and subject must belong to the same department.'
                if row_error is None:
                    identity = (student.id, subject.id, record_date.isoformat())
                    if identity in seen:
                        row_error = 'Duplicate attendance row in this upload.'
                    elif Attendance.query.filter_by(
                        student_id=student.id, subject_id=subject.id,
                        date=record_date, session_id=None,
                    ).first():
                        row_error = 'Attendance record already exists.'
                    else:
                        seen.add(identity)
                        accepted.append({
                            'row': line, 'student_id': student.id, 'subject_id': subject.id,
                            'date': record_date.isoformat(), 'status': status,
                        })
            if row_error:
                errors.append({'row': line, 'error': row_error})
    except csv.Error as error:
        raise ImportValidationError('CSV formatting is invalid.') from error

    if not headers:
        raise ImportValidationError('CSV must include a header row.')
    if not accepted and not errors:
        raise ImportValidationError('CSV contains no attendance rows.')
    return {'accepted': accepted, 'errors': errors}


def create_import_stage(owner_id, payload):
    """Persist a short-lived preview without creating attendance rows."""
    from flask import current_app

    now = datetime.utcnow()
    AttendanceImportStage.query.filter(
        AttendanceImportStage.status == 'preview', AttendanceImportStage.expires_at <= now,
    ).update(
        {AttendanceImportStage.status: 'expired', AttendanceImportStage.payload: None},
        synchronize_session=False,
    )
    cutoff = now - timedelta(days=30)
    AttendanceImportStage.query.filter(
        AttendanceImportStage.status.in_(['committed', 'cancelled', 'expired', 'failed']),
        AttendanceImportStage.created_at < cutoff,
    ).delete(synchronize_session=False)
    ttl_minutes = max(1, int(current_app.config.get('ATTENDANCE_IMPORT_STAGE_TTL_MINUTES', 15)))
    stage = AttendanceImportStage(
        owner_id=owner_id, created_at=now, expires_at=now + timedelta(minutes=ttl_minutes),
        status='preview', payload=payload,
    )
    db.session.add(stage)
    db.session.commit()
    return stage


def expire_stage(stage):
    stage.status = 'expired'
    stage.payload = None
    db.session.commit()


def commit_import_stage(stage, actor_id):
    """Commit every staged accepted row atomically after rechecking conflicts."""
    if stage.status != 'preview' or not stage.payload:
        return 'unavailable', 0
    if stage.expires_at <= datetime.utcnow():
        expire_stage(stage)
        return 'expired', 0

    accepted = stage.payload.get('accepted', [])
    if not accepted:
        return 'unavailable', 0
    try:
        for row in accepted:
            record_date = datetime.strptime(row['date'], '%Y-%m-%d').date()
            if Attendance.query.filter_by(
                student_id=row['student_id'], subject_id=row['subject_id'],
                date=record_date, session_id=None,
            ).first():
                raise ImportConflict('Attendance record was created after preview.')
            db.session.add(Attendance(
                student_id=row['student_id'], subject_id=row['subject_id'], date=record_date,
                status=row['status'], session_id=None, marked_by=actor_id,
            ))
        db.session.flush()
        count = len(accepted)
        stage.status = 'committed'
        stage.payload = None
        db.session.commit()
        return 'committed', count
    except (IntegrityError, ImportConflict):
        db.session.rollback()
        return 'conflict', 0
    except Exception:
        db.session.rollback()
        raise


class ImportConflict(Exception):
    """A staged row conflicts with data committed after its preview."""


def import_preview_data(stage):
    """Build safe preview rows without exposing staged internals or credentials."""
    payload = stage.payload or {'accepted': [], 'errors': []}
    accepted = payload.get('accepted', [])
    preview_rows = []
    for row in accepted[:10]:
        student = db.session.get(Student, row['student_id'])
        subject = db.session.get(Subject, row['subject_id'])
        preview_rows.append({
            'row': row['row'],
            'register_number': student.register_number if student else '[unavailable]',
            'subject_code': subject.subject_code if subject else '[unavailable]',
            'date': row['date'], 'status': row['status'],
        })
    errors = payload.get('errors', [])
    duplicate_count = sum(
        1 for item in errors
        if 'duplicate' in item.get('error', '').lower() or 'already exists' in item.get('error', '').lower()
    )
    accepted_count = len(accepted)
    error_count = len(errors)
    return {
        'accepted_count': accepted_count, 'error_count': error_count,
        'processed': accepted_count + error_count, 'successful': accepted_count,
        'failed': error_count - duplicate_count, 'duplicate': duplicate_count,
        'accepted_sample': preview_rows, 'errors': errors,
    }
