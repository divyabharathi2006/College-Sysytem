from datetime import date
from io import BytesIO
import re

import pytest

from app import create_app, db
from attendance_engine import calculate_percentage, projected_attendance, required_classes_to_attend, safe_absence_count
from models.user import User
from models.student import Student
from models.subject import Subject
from models.attendance import Attendance
from models.faculty import Faculty
from werkzeug.security import generate_password_hash
from security import verify_password_and_update


@pytest.fixture
def app():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_login_page_loads(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'Welcome to Divyabharathi University!' in response.data
    assert b'href="/login"' in response.data


def test_login_page_preserves_sign_in_flows_and_accessible_controls(client):
    response = client.get('/login')
    assert response.status_code == 200
    page = response.data.decode('utf-8')
    assert 'action="/login"' in page
    assert 'name="username"' in page
    assert 'name="password"' in page
    assert 'name="mode" value="student-bootstrap"' in page
    assert 'name="student_id"' in page
    assert 'name="date_of_birth"' in page
    assert 'Forgot password?' in page
    assert 'data-password-toggle="password"' in page
    assert 'aria-label="Show password"' in page
    assert 'id="sign-in"' in page


def test_homepage_book_intro_is_skippable_and_reduced_motion_aware(client):
    response = client.get('/')
    assert response.status_code == 200
    page = response.data.decode('utf-8')
    assert 'id="home-loader"' in page
    assert 'id="home-loader-skip"' in page
    assert 'Opening a world of learning' in page
    assert 'js/home-loader.js' in page


def test_public_homepage_sections_and_navigation(client):
    response = client.get('/')
    assert response.status_code == 200
    for anchor in (b'id="about"', b'id="courses"', b'id="faculty"', b'id="admissions"', b'id="gallery"', b'id="contact"'):
        assert anchor in response.data
    for label in (b'About Us', b'Courses Offered', b'Faculty', b'Admissions', b'Photo Gallery', b'Contact Us'):
        assert label in response.data
    page = response.data.decode('utf-8')
    gallery_cards = re.findall(r'<figure class="home-gallery-card[^\"]*">(.*?)</figure>', page, re.DOTALL)
    assert len(gallery_cards) == 3
    assert page.count('class="home-gallery-image"') == 3
    for card in gallery_cards:
        image = re.search(r'<img\b([^>]*)>', card)
        assert image is not None
        attributes = image.group(1)
        assert re.search(r'\balt="[^"]+"', attributes)
        assert 'loading="lazy"' in attributes
        assert 'decoding="async"' in attributes
        assert 'referrerpolicy="no-referrer"' in attributes
        assert 'Illustrative stock photo only — not an actual Divyabharathi University campus or event.' in card
        assert 'Photo by <a href="https://www.pexels.com/@' in card
        assert re.search(r'href="https://www\.pexels\.com/photo/[^\"]+" target="_blank" rel="noopener noreferrer">View source on Pexels</a>', card)
    assert 'the people shown do not endorse Divyabharathi University' in page


def test_attendance_calculation():
    assert calculate_percentage(42, 50) == 84.0
    assert safe_absence_count(42, 50, 75) == 6
    assert required_classes_to_attend(68, 75, 50) == 14
    assert projected_attendance(42, 50, 10, 8) == 83.33
    assert projected_attendance(0, 0, 10, 8) == 80.0


def test_student_creation(app):
    with app.app_context():
        user = User(username='studentnew', password_hash='hash', role='student')
        db.session.add(user)
        db.session.flush()
        student = Student(register_number='22CS101', name='New Student', email='new@example.com', phone='99999', department='CSE', year='2', section='A', semester='4', user_id=user.id)
        db.session.add(student)
        db.session.commit()
        assert Student.query.count() == 1


def test_attendance_insertion_and_duplicate_prevention(app):
    with app.app_context():
        user = User(username='admin', password_hash='hash', role='admin')
        db.session.add(user)
        db.session.flush()
        student = Student(register_number='22CS102', name='Student One', email='s1@example.com', phone='111', department='CSE', year='2', section='A', semester='4', user_id=user.id)
        subject = Subject(subject_code='CS101', subject_name='DBMS', department='CSE', semester='4', faculty_id=None)
        db.session.add_all([student, subject])
        db.session.commit()
        entry = Attendance(student_id=student.id, subject_id=subject.id, date=date(2026, 10, 1), status='PRESENT', marked_by=user.id)
        db.session.add(entry)
        db.session.commit()
        duplicate = Attendance(student_id=student.id, subject_id=subject.id, date=date(2026, 10, 1), status='ABSENT', marked_by=user.id)
        db.session.add(duplicate)
        with pytest.raises(Exception):
            db.session.commit()


def test_prediction_api(client):
    response = client.get('/api/prediction/student/1')
    assert response.status_code == 302


def test_role_authorization(client):
    response = client.get('/admin/dashboard')
    assert response.status_code == 302
    response = client.get('/student/dashboard')
    assert response.status_code == 302
    assert client.get('/api/students').status_code == 302


def test_admin_student_crud(client, app):
    with app.app_context():
        db.session.add(User(username='admin-test', password_hash=generate_password_hash('password'), role='admin'))
        db.session.commit()
    assert client.post('/login', data={'username': 'admin-test', 'password': 'password'}).status_code == 302
    response = client.post('/admin/registries/students', data={
        'student_type': 'university', 'username': 'newstudent',
        'password': 'Student-Temporary7!', 'roll_number': '22CS777',
        'name': 'New Student', 'email': 'new@example.com', 'phone': '+1 202-555-0100',
        'gender': 'Prefer not to say', 'date_of_birth': '2005-04-05',
        'father_name': 'Parent One', 'mother_name': 'Parent Two',
        'address': '1 University Way', 'community': 'Not specified',
        'identification_type': 'University photo ID', 'identification_verified': 'on',
        'department': 'CSE', 'year': '2', 'section': 'A', 'semester': '4',
    })
    assert response.status_code == 302
    with app.app_context():
        student = Student.query.filter_by(register_number='DBU30022CS777').first()
        assert student is not None
        student_id = student.id
        assert db.session.get(User, student.user_id).password_hash.startswith('$argon2id$')
    response = client.post(f'/admin/students/{student_id}/edit', data={'name': 'Updated Student'})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Student, student_id).name == 'Updated Student'
    response = client.post(f'/admin/students/{student_id}/delete')
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Student, student_id) is None


def test_admin_csv_import(client, app):
    with app.app_context():
        admin = User(username='csv-admin', password_hash=generate_password_hash('password'), role='admin')
        db.session.add(admin)
        db.session.flush()
        student_user = User(username='csv-student', password_hash=generate_password_hash('password'), role='student')
        db.session.add(student_user)
        db.session.flush()
        student = Student(
            register_number='22CS888', name='CSV Student', email='csv@example.com', phone='123',
            department='CSE', year='2', section='A', semester='4', user_id=student_user.id,
        )
        subject = Subject(subject_code='CS888', subject_name='CSV Subject', department='CSE', semester='4')
        db.session.add_all([student, subject])
        db.session.commit()
    client.post('/login', data={'username': 'csv-admin', 'password': 'password'})
    payload = {
        'file': (BytesIO(b'register_number,subject_code,date,status\n22CS888,CS888,2026-10-01,PRESENT\n'), 'attendance.csv')
    }
    response = client.post('/admin/import-csv', data=payload, content_type='multipart/form-data')
    assert response.status_code == 302
    stage_id = int(response.headers['Location'].rstrip('/').split('/')[-1])
    with app.app_context():
        assert Attendance.query.count() == 0
    preview = client.get(response.headers['Location'])
    assert preview.status_code == 200
    committed = client.post(f'/admin/import-csv/{stage_id}/commit', data={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    })
    assert committed.status_code == 302
    with app.app_context():
        assert Attendance.query.count() == 1
        assert Attendance.query.first().status == 'PRESENT'


def test_fresh_app_does_not_seed_records(app):
    with app.app_context():
        assert User.query.count() == 0
        assert Student.query.count() == 0
        assert Subject.query.count() == 0
        assert Attendance.query.count() == 0


def test_admin_manages_faculty_subjects_and_attendance(client, app):
    with app.app_context():
        db.session.add(User(username='manage-admin', password_hash=generate_password_hash('test-password'), role='admin'))
        db.session.commit()
    client.post('/login', data={'username': 'manage-admin', 'password': 'test-password'})
    assert client.get('/admin/dashboard').status_code == 200

    response = client.post('/admin/faculty', data={
        'username': 'faculty-one', 'password': 'faculty-password', 'employee_id': 'FAC-1',
        'name': 'Faculty One', 'email': 'faculty@example.test', 'department': 'CSE',
    })
    assert response.status_code == 302
    with app.app_context():
        faculty = Faculty.query.filter_by(employee_id='FAC-1').one()
        faculty_id = faculty.id
        faculty_user_id = faculty.user_id
        assert db.session.get(User, faculty_user_id).password_hash.startswith('$argon2id$')
    client.post(f'/admin/faculty/{faculty_id}/edit', data={
        'employee_id': 'FAC-01', 'name': 'Updated Faculty', 'email': 'updated@example.test', 'department': 'IT',
    })
    with app.app_context():
        assert db.session.get(Faculty, faculty_id).name == 'Updated Faculty'

    with app.app_context():
        student_user = User(username='attendance-student', password_hash='hash', role='student')
        db.session.add(student_user)
        db.session.flush()
        student = Student(register_number='ST-100', name='Attendance Student', email='student@example.test', phone='100', department='IT', year='1', section='A', semester='1', user_id=student_user.id)
        db.session.add(student)
        db.session.commit()
        student_id = student.id

    response = client.post('/admin/subjects', data={
        'subject_code': 'IT100', 'subject_name': 'Systems', 'department': 'IT', 'semester': '1', 'faculty_id': faculty_id,
    })
    assert response.status_code == 302
    with app.app_context():
        subject = Subject.query.filter_by(subject_code='IT100').one()
        subject_id = subject.id
    client.post(f'/admin/subjects/{subject_id}/edit', data={
        'subject_code': 'IT101', 'subject_name': 'Updated Systems', 'department': 'IT', 'semester': '2', 'faculty_id': faculty_id,
    })
    with app.app_context():
        subject = db.session.get(Subject, subject_id)
        assert subject.subject_code == 'IT101'
        assert subject.faculty_id == faculty_id

    response = client.post('/admin/attendance', data={
        'student_id': student_id, 'subject_id': subject_id, 'date': '2026-10-01', 'status': 'PRESENT',
    })
    assert response.status_code == 302
    with app.app_context():
        record = Attendance.query.one()
        attendance_id = record.id
    client.post(f'/admin/attendance/{attendance_id}/edit', data={
        'student_id': student_id, 'subject_id': subject_id, 'date': '2026-10-02', 'status': 'ABSENT',
    })
    with app.app_context():
        record = db.session.get(Attendance, attendance_id)
        assert record.status == 'ABSENT'
        assert record.date == date(2026, 10, 2)
    assert client.get('/admin/dashboard').status_code == 200

    client.post(f'/admin/attendance/{attendance_id}/delete')
    with app.app_context():
        assert db.session.get(Attendance, attendance_id) is None
    client.post(f'/admin/faculty/{faculty_id}/delete')
    with app.app_context():
        assert db.session.get(Faculty, faculty_id) is None
        assert db.session.get(User, faculty_user_id) is None
        assert db.session.get(Subject, subject_id).faculty_id is None
    client.post(f'/admin/subjects/{subject_id}/delete')
    with app.app_context():
        assert db.session.get(Subject, subject_id) is None


def test_admin_user_role_password_and_delete_safeguards(client, app):
    with app.app_context():
        admin = User(username='account-admin', password_hash=generate_password_hash('admin-password'), role='admin')
        account = User(username='managed-account', password_hash=generate_password_hash('initial-password'), role='student')
        db.session.add_all([admin, account])
        db.session.commit()
        account_id = account.id
        admin_id = admin.id
    client.post('/login', data={'username': 'account-admin', 'password': 'admin-password'})

    assert client.post(f'/admin/users/{account_id}/edit', data={'username': 'managed-updated', 'role': 'faculty'}).status_code == 302
    assert client.post(f'/admin/users/{account_id}/password', data={'password': 'Replacement-Password7!'}).status_code == 302
    with app.app_context():
        account = db.session.get(User, account_id)
        assert account.username == 'managed-updated'
        assert account.role == 'faculty'
        assert verify_password_and_update(account.password_hash, 'Replacement-Password7!')[0]
    assert client.post(f'/admin/users/{account_id}/delete').status_code == 302
    assert client.post(f'/admin/users/{admin_id}/delete').status_code == 302
    with app.app_context():
        assert db.session.get(User, account_id) is None
        assert db.session.get(User, admin_id) is not None


def test_bootstrap_admin_is_environment_backed_one_time_and_silent(app, monkeypatch):
    bootstrap_password = 'test-only-bootstrap-password'
    monkeypatch.setenv('BOOTSTRAP_ADMIN_USERNAME', 'first-admin')
    monkeypatch.setenv('BOOTSTRAP_ADMIN_PASSWORD', bootstrap_password)
    runner = app.test_cli_runner()
    result = runner.invoke(args=['bootstrap-admin'])
    assert result.exit_code == 0
    assert bootstrap_password not in result.output
    with app.app_context():
        admin = User.query.filter_by(username='first-admin', role='SUPER_ADMIN').one()
        assert admin.password_hash.startswith('$argon2id$')
        assert verify_password_and_update(admin.password_hash, bootstrap_password)[0]
    second_attempt = runner.invoke(args=['bootstrap-admin'])
    assert second_attempt.exit_code != 0


def test_database_reset_requires_exact_path_and_creates_backup(tmp_path, monkeypatch):
    database_path = tmp_path / 'attendance.sqlite'
    monkeypatch.setenv('SECRET_KEY', 'test-reset-secret')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path.as_posix()}')
    reset_app = create_app()
    with reset_app.app_context():
        db.create_all()
        assert User.query.count() == 0
        db.session.add(User(username='existing', password_hash='hash', role='admin'))
        db.session.commit()

    runner = reset_app.test_cli_runner()
    wrong_path = runner.invoke(args=['reset-database', '--confirm-path', str(tmp_path / 'other.sqlite')])
    assert wrong_path.exit_code != 0
    with reset_app.app_context():
        assert User.query.count() == 1

    reset_result = runner.invoke(args=['reset-database', '--confirm-path', str(database_path)])
    assert reset_result.exit_code == 0, reset_result.output
    assert list(tmp_path.glob('attendance.sqlite.backup-*'))
    with reset_app.app_context():
        assert User.query.count() == 0
