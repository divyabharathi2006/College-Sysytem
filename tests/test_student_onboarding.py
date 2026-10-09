import csv
from io import BytesIO, StringIO

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from models.attendance import Attendance
from models.audit import SecurityEvent
from models.student import Student
from models.user import User
from models.user_session import UserSession
from security import keyed_digest, verify_password_and_update
from student_import import import_student_csv


COLUMNS = [
    'student_id', 'first_name', 'last_name', 'gender', 'date_of_birth', 'email',
    'phone', 'district', 'state', 'pincode', 'course_level', 'department',
    'year_of_study', 'section', 'roll_number', 'admission_year', 'cgpa',
    'attendance_percentage', 'blood_group', 'hostel_status', 'student_status',
]


def roster_csv(rows):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            'student_id': row.get('student_id', 'STU-100'),
            'first_name': row.get('first_name', 'Sample'),
            'last_name': row.get('last_name', 'Student'),
            'gender': 'unspecified', 'date_of_birth': row.get('date_of_birth', '05-04-2005'),
            'email': row.get('email', f"{row.get('student_id', 'STU-100').lower()}@example.test"),
            'phone': row.get('phone', f"555-{row.get('student_id', 'STU-100')[-4:]}"),
            'district': 'Not imported', 'state': 'Not imported', 'pincode': '000000',
            'course_level': 'Not imported', 'department': row.get('department', 'CSE'),
            'year_of_study': row.get('year', '1'), 'section': row.get('section', 'A'),
            'roll_number': row.get('roll_number', 'ROLL-DUP'), 'admission_year': '2025',
            'cgpa': '8.0', 'attendance_percentage': '97.5', 'blood_group': 'unknown',
            'hostel_status': 'unknown', 'student_status': row.get('student_status', 'Active'),
        })
    return output.getvalue().encode('utf-8')


@pytest.fixture

def app():
    app = create_app(testing=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture

def client(app):
    return app.test_client()


def test_student_csv_import_is_dry_run_by_default_and_keeps_rolls_nonunique(app, tmp_path):
    roster_path = tmp_path / 'students.csv'
    roster_path.write_bytes(roster_csv([
        {'student_id': 'STU-100', 'roll_number': 'SAME-ROLL'},
        {'student_id': 'STU-101', 'roll_number': 'SAME-ROLL', 'student_status': 'Inactive'},
    ]))
    runner = app.test_cli_runner()
    dry_run = runner.invoke(args=['import-students', str(roster_path)])
    assert dry_run.exit_code == 0, dry_run.output
    assert 'Dry run valid: 2' in dry_run.output
    assert 'no database changes were made' in dry_run.output
    with app.app_context():
        assert User.query.count() == 0
        assert Student.query.count() == 0
        assert Attendance.query.count() == 0

    committed = runner.invoke(args=['import-students', str(roster_path), '--commit'])
    assert committed.exit_code == 0, committed.output
    assert 'Imported 2 student profile(s) atomically.' in committed.output
    with app.app_context():
        profiles = Student.query.order_by(Student.register_number).all()
        assert [profile.register_number for profile in profiles] == ['STU-100', 'STU-101']
        assert [profile.roll_number for profile in profiles] == ['SAME-ROLL', 'SAME-ROLL']
        assert [profile.semester for profile in profiles] == ['Unspecified', 'Unspecified']
        assert profiles[0].user.username == profiles[0].register_number
        assert profiles[0].user.must_change_password is True
        assert profiles[0].user.is_enabled is True
        assert profiles[1].user.is_enabled is False
        assert profiles[0].dob_verifier == keyed_digest('student-bootstrap-dob', '2005-04-05')
        assert not hasattr(profiles[0], 'date_of_birth')
        assert Attendance.query.count() == 0


def test_student_csv_import_rejects_entire_batch_without_private_output(app, tmp_path):
    with app.app_context():
        db.session.add(User(username='STU-COLLIDE', password_hash='unused', role='student'))
        db.session.commit()
    roster_path = tmp_path / 'conflicts.csv'
    private_values = {
        'email': 'sensitive-person@example.test',
        'date_of_birth': '07-08-2004',
        'first_name': 'PrivateName',
    }
    roster_path.write_bytes(roster_csv([
        {'student_id': 'STU-NEW', **private_values},
        {'student_id': 'STU-COLLIDE', 'email': 'existing@example.test'},
    ]))
    result = app.test_cli_runner().invoke(args=['import-students', str(roster_path), '--commit'])
    assert result.exit_code != 0
    assert 'student_id_conflict' in result.output
    for private_value in private_values.values():
        assert private_value not in result.output
    with app.app_context():
        assert User.query.count() == 1
        assert Student.query.count() == 0
        assert User.query.filter_by(username='STU-NEW').first() is None


def test_student_import_validates_column_date_and_duplicate_student_ids(app, tmp_path):
    invalid = roster_csv([
        {'student_id': 'STU-BAD', 'date_of_birth': '2005-04-05'},
        {'student_id': 'STU-DUP'},
        {'student_id': 'STU-DUP'},
    ])
    roster_path = tmp_path / 'invalid.csv'
    roster_path.write_bytes(invalid)
    result = app.test_cli_runner().invoke(args=['import-students', str(roster_path), '--commit'])
    assert result.exit_code != 0
    assert 'invalid_date_of_birth' in result.output
    assert 'duplicate_student_id_in_file' in result.output
    with app.app_context():
        assert User.query.count() == 0
        assert Student.query.count() == 0


def test_student_import_rejects_duplicate_emails_and_normalized_phones(app):
    rows = roster_csv([
        {'student_id': 'STU-CONTACT-1', 'email': 'same@example.test', 'phone': '+1 202-555-0100'},
        {'student_id': 'STU-CONTACT-2', 'email': 'same@example.test', 'phone': '+1 202-555-0101'},
        {'student_id': 'STU-CONTACT-3', 'email': 'other@example.test', 'phone': '1 (202) 555-0100'},
    ])
    with app.app_context():
        result, committed = import_student_csv(BytesIO(rows), commit=True)
        assert not committed
        assert {error['code'] for error in result.errors} == {'duplicate_contact_in_file'}
        assert Student.query.count() == 0
        assert User.query.count() == 0

    missing_columns = BytesIO(b'student_id,first_name\nSTU-1,Sample\n')
    with app.app_context():
        parsed, committed = import_student_csv(missing_columns, commit=True)
        assert not committed
        assert parsed.errors == [{'row': 0, 'code': 'invalid_columns'}]


def test_imported_student_bootstrap_forces_password_and_rotates_session(app, client):
    with app.app_context():
        result, committed = import_student_csv(BytesIO(roster_csv([
            {'student_id': 'STU-ACTIVE'},
        ])), commit=True)
        assert committed and not result.errors
        user = User.query.filter_by(username='STU-ACTIVE').one()
        user_id = user.id

    rejected = client.post('/login', data={
        'mode': 'student-bootstrap', 'student_id': 'STU-ACTIVE', 'date_of_birth': '06-04-2005',
    })
    assert rejected.status_code == 200
    assert b'Invalid username or password.' in rejected.data
    assert b'06-04-2005' not in rejected.data
    assert client.get('/admin/dashboard').status_code == 302

    accepted = client.post('/login', data={
        'mode': 'student-bootstrap', 'student_id': 'STU-ACTIVE', 'date_of_birth': '05-04-2005',
    })
    assert accepted.status_code == 302
    assert accepted.headers['Location'].endswith('/student/password-setup')
    with client.session_transaction() as cookie_session:
        old_session_id = cookie_session['auth_session_id']
    assert client.get('/student/dashboard').status_code == 302
    assert client.get('/student/password-setup').status_code == 200

    weak = client.post('/student/password-setup', data={
        'new_password': 'short', 'password_confirmation': 'short',
    })
    assert weak.status_code == 400
    assert client.get('/student/dashboard').status_code == 302

    new_password = 'Strong-Student-Password8!'
    completed = client.post('/student/password-setup', data={
        'new_password': new_password, 'password_confirmation': new_password,
    })
    assert completed.status_code == 302
    assert completed.headers['Location'].endswith('/student/dashboard')
    with client.session_transaction() as cookie_session:
        new_session_id = cookie_session['auth_session_id']
    assert new_session_id != old_session_id
    with app.app_context():
        user = db.session.get(User, user_id)
        profile = Student.query.filter_by(user_id=user_id).one()
        assert user.must_change_password is False
        assert profile.dob_verifier is None
        assert verify_password_and_update(user.password_hash, new_password) == (True, None)
        sessions = UserSession.query.filter_by(user_id=user_id).all()
        assert len(sessions) == 2
        assert next(row for row in sessions if row.session_id_hash == keyed_digest('auth-session', old_session_id)).revoked_at is not None
        assert next(row for row in sessions if row.session_id_hash == keyed_digest('auth-session', new_session_id)).revoked_at is None
    assert client.get('/student/dashboard').status_code == 200


def test_inactive_bootstrap_is_denied_and_existing_password_login_still_works(app):
    with app.app_context():
        _result, committed = import_student_csv(BytesIO(roster_csv([
            {'student_id': 'STU-INACTIVE', 'student_status': 'Inactive'},
        ])), commit=True)
        assert committed
        legacy = User(username='legacy-student', password_hash=generate_password_hash('legacy-pass'), role='student')
        db.session.add(legacy)
        db.session.commit()

    client = app.test_client()
    inactive = client.post('/login', data={
        'mode': 'student-bootstrap', 'student_id': 'STU-INACTIVE', 'date_of_birth': '05-04-2005',
    })
    assert inactive.status_code == 200
    assert b'Invalid username or password.' in inactive.data
    with client.session_transaction() as cookie_session:
        assert 'auth_session_id' not in cookie_session

    legacy_login = app.test_client().post('/login', data={
        'username': 'legacy-student', 'password': 'legacy-pass',
    })
    assert legacy_login.status_code == 302
    with app.app_context():
        legacy = User.query.filter_by(username='legacy-student').one()
        assert legacy.must_change_password is False
        assert legacy.password_hash.startswith('$argon2id$')


def test_super_admin_student_search_pagination_duplicate_rolls_and_edit(app):
    with app.app_context():
        administrator = User(username='roster-admin', password_hash=generate_password_hash('admin-pass'), role='SUPER_ADMIN')
        regular_user = User(username='roster-student', password_hash=generate_password_hash('student-pass'), role='student')
        db.session.add_all([administrator, regular_user])
        db.session.flush()
        regular_user_id = regular_user.id
        for index in range(55):
            user = User(username=f'STU-{index:03}', password_hash='unused', role='student')
            db.session.add(user)
            db.session.flush()
            db.session.add(Student(
                register_number=f'STU-{index:03}', roll_number='SHARED-ROLL',
                name=f'Student {index:03}', email=f's{index}@example.test',
                phone=f'555-{index:04}', department='CSE', year='1', section='A',
                semester='Unspecified', user_id=user.id,
            ))
        db.session.commit()
        first_student = Student.query.filter_by(register_number='STU-000').one()
        first_student_id = first_student.id

    client = app.test_client()
    assert client.get('/admin/students').status_code == 302
    assert client.post('/login', data={'username': 'roster-student', 'password': 'student-pass'}).status_code == 302
    with app.app_context():
        assert db.session.get(User, regular_user_id).role == 'student'
    assert client.get('/admin/security').status_code == 403
    assert client.get('/admin/students').status_code == 403
    assert client.post('/admin/students/1/edit', data={'name': 'Unauthorized Change'}).status_code == 403
    assert client.post('/logout').status_code == 302
    assert client.post('/login', data={'username': 'roster-admin', 'password': 'admin-pass'}).status_code == 302

    results = client.get('/admin/students?q=SHARED-ROLL')
    assert results.status_code == 200
    assert b'55 matching profile(s)' in results.data
    assert b'Student 000' in results.data and b'Student 054' not in results.data
    page_two = client.get('/admin/students?q=SHARED-ROLL&page=2')
    assert page_two.status_code == 200
    assert b'Student 054' in page_two.data
    by_phone = client.get('/admin/students?q=555-0001')
    assert by_phone.status_code == 200
    assert b'Student 001' in by_phone.data
    dashboard = client.get('/admin/dashboard')
    assert dashboard.status_code == 200
    assert b'Student 024' in dashboard.data
    assert b'Student 025' not in dashboard.data

    updated = client.post(f'/admin/students/{first_student_id}/edit', data={
        'student_id': 'STU-RENAMED', 'roll_number': 'SHARED-ROLL', 'name': 'Renamed Student',
    })
    assert updated.status_code == 302
    with app.app_context():
        student = db.session.get(Student, first_student_id)
        assert student.register_number == 'STU-RENAMED'
        assert student.roll_number == 'SHARED-ROLL'
        assert student.user.username == 'STU-RENAMED'
        assert student.name == 'Renamed Student'
        event = SecurityEvent.query.filter_by(
            action='admin.edit_student:post', status='success', resource_id=str(first_student_id),
        ).one()
        assert event.status == 'success'
        assert 'name' not in (event.after_data or {})
        assert 'Renamed Student' not in str(event.after_data)

        second_student = Student.query.filter_by(register_number='STU-001').one()
        second_id = second_student.id
    conflict = client.post(f'/admin/students/{second_id}/edit', data={'student_id': 'STU-RENAMED'})
    assert conflict.status_code == 302
    with app.app_context():
        assert db.session.get(Student, second_id).register_number == 'STU-001'
        assert Student.query.filter_by(roll_number='SHARED-ROLL').count() == 55
