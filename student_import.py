"""Explicit, all-or-nothing import of student profiles from the provided roster CSV."""

import csv
import io
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from extensions import db
from models.student import Student
from models.user import User
from security import hash_password, keyed_digest

REQUIRED_COLUMNS = {
    'student_id', 'first_name', 'last_name', 'date_of_birth', 'email', 'phone',
    'department', 'year_of_study', 'section', 'roll_number', 'student_status',
}
MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_ROWS = 10000
INACTIVE_STATUSES = {'inactive', 'disabled', 'withdrawn', 'graduated', 'left', 'deceased'}


@dataclass
class StudentImportResult:
    valid_rows: list
    errors: list
    total_rows: int
    existing_ids: set


def _value(row, field):
    return str(row.get(field, '') or '').strip()


def _valid_email(value):
    local, separator, domain = value.partition('@')
    return bool(separator and local and domain and '.' in domain and not any(c.isspace() for c in value))


def _normalized_phone(value):
    digits = ''.join(character for character in value if character.isdigit())
    return digits or value.casefold()


def _existing_identifiers(identifiers):
    existing_usernames = set()
    existing_register_numbers = set()
    values = list(identifiers)
    for offset in range(0, len(values), 500):
        batch = values[offset:offset + 500]
        existing_usernames.update(
            username for (username,) in db.session.query(User.username).filter(User.username.in_(batch)).all()
        )
        existing_register_numbers.update(
            register_number for (register_number,) in db.session.query(Student.register_number)
            .filter(Student.register_number.in_(batch)).all()
        )
    return existing_usernames, existing_register_numbers


def validate_student_csv(stream):
    """Parse and validate a roster without writing records or exposing cell contents."""
    payload = stream.read(MAX_IMPORT_BYTES + 1)
    if len(payload) > MAX_IMPORT_BYTES:
        return StudentImportResult([], [{'row': 0, 'code': 'file_too_large'}], 0, set())
    try:
        text = payload.decode('utf-8-sig')
    except UnicodeDecodeError:
        return StudentImportResult([], [{'row': 0, 'code': 'invalid_encoding'}], 0, set())

    reader = csv.DictReader(io.StringIO(text, newline=''))
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not REQUIRED_COLUMNS.issubset(set(headers)):
        return StudentImportResult([], [{'row': 0, 'code': 'invalid_columns'}], 0, set())

    valid_rows = []
    errors = []
    identifiers = set()
    seen_ids = set()
    seen_emails = set()
    seen_phones = set()
    total_rows = 0
    for row_number, row in enumerate(reader, start=2):
        total_rows += 1
        if total_rows > MAX_IMPORT_ROWS:
            errors.append({'row': row_number, 'code': 'too_many_rows'})
            break
        if None in row:
            errors.append({'row': row_number, 'code': 'invalid_row_shape'})
            continue
        values = {field: _value(row, field) for field in REQUIRED_COLUMNS}
        if any(not values[field] for field in REQUIRED_COLUMNS):
            errors.append({'row': row_number, 'code': 'required_field_missing'})
            continue

        student_id = values['student_id']
        if len(student_id) > 50:
            errors.append({'row': row_number, 'code': 'student_id_too_long'})
            continue
        if student_id in seen_ids:
            errors.append({'row': row_number, 'code': 'duplicate_student_id_in_file'})
            continue
        seen_ids.add(student_id)
        if len(values['first_name']) + len(values['last_name']) + 1 > 120:
            errors.append({'row': row_number, 'code': 'name_too_long'})
            continue
        bounds = (
            ('email', 120), ('phone', 30), ('department', 80),
            ('year_of_study', 20), ('section', 20), ('roll_number', 50),
        )
        too_long = next((field for field, maximum in bounds if len(values[field]) > maximum), None)
        if too_long:
            errors.append({'row': row_number, 'code': f'{too_long}_too_long'})
            continue
        if not _valid_email(values['email']):
            errors.append({'row': row_number, 'code': 'invalid_email'})
            continue
        normalized_email = values['email'].casefold()
        normalized_phone = _normalized_phone(values['phone'])
        if normalized_email in seen_emails or normalized_phone in seen_phones:
            errors.append({'row': row_number, 'code': 'duplicate_contact_in_file'})
            continue
        seen_emails.add(normalized_email)
        seen_phones.add(normalized_phone)
        try:
            birth_date = datetime.strptime(values['date_of_birth'], '%d-%m-%Y').date()
            if birth_date.strftime('%d-%m-%Y') != values['date_of_birth']:
                raise ValueError
        except ValueError:
            errors.append({'row': row_number, 'code': 'invalid_date_of_birth'})
            continue

        identifiers.add(student_id)
        valid_rows.append({
            'source_row': row_number,
            'student_id': student_id,
            'name': f"{values['first_name']} {values['last_name']}",
            'email': values['email'], 'phone': values['phone'],
            'department': values['department'], 'year': values['year_of_study'],
            'section': values['section'], 'roll_number': values['roll_number'],
            'is_enabled': values['student_status'].casefold() not in INACTIVE_STATUSES,
            'dob_verifier': keyed_digest('student-bootstrap-dob', birth_date.isoformat()),
        })

    existing_usernames, existing_register_numbers = _existing_identifiers(identifiers)
    existing_ids = existing_usernames | existing_register_numbers
    if existing_ids:
        colliding_rows = {item['student_id'] for item in valid_rows} & existing_ids
        errors.extend({'row': item['source_row'], 'code': 'student_id_conflict'}
                      for item in valid_rows if item['student_id'] in colliding_rows)
        valid_rows = [item for item in valid_rows if item['student_id'] not in existing_ids]
    existing_contacts = Student.query.with_entities(Student.email, Student.phone).all()
    existing_emails = {str(email).strip().casefold() for email, _phone in existing_contacts}
    existing_phones = {_normalized_phone(str(phone).strip()) for _email, phone in existing_contacts}
    retained_rows = []
    for item in valid_rows:
        errors_added = False
        if item['email'].casefold() in existing_emails:
            errors.append({'row': item['source_row'], 'code': 'email_conflict'})
            errors_added = True
        if _normalized_phone(item['phone']) in existing_phones:
            errors.append({'row': item['source_row'], 'code': 'phone_conflict'})
            errors_added = True
        if not errors_added:
            retained_rows.append(item)
    valid_rows = retained_rows
    return StudentImportResult(valid_rows, errors, total_rows, existing_ids)


def import_student_csv(stream, *, commit=False):
    """Validate the entire CSV; persist only when explicitly opted in and error-free."""
    result = validate_student_csv(stream)
    if result.errors or not commit:
        return result, False

    # Imported accounts cannot use this deliberately unknown placeholder: password login
    # is blocked while must_change_password is true; DOB bootstrap is the only first login.
    placeholder_hash = hash_password(secrets.token_urlsafe(48))
    try:
        for item in result.valid_rows:
            user = User(
                username=item['student_id'], password_hash=placeholder_hash, role='student',
                is_enabled=item['is_enabled'], must_change_password=True,
            )
            profile = Student(
                register_number=item['student_id'], roll_number=item['roll_number'],
                name=item['name'], email=item['email'], phone=item['phone'],
                department=item['department'], year=item['year'], section=item['section'],
                semester='Unspecified', dob_verifier=item['dob_verifier'], user=user,
            )
            db.session.add_all([user, profile])
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        result.errors.append({'row': 0, 'code': 'database_conflict'})
        return result, False
    except Exception:
        db.session.rollback()
        raise
    return result, True