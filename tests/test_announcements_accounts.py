from datetime import date, datetime, time, timedelta
import json
import re

import pytest

from app import create_app, db
from models.academic import AcademicYear, Batch, ClassSession, Course, Department, Section, Semester
from models.announcements import Announcement
from models.audit import SecurityEvent
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from models.user import User
from models.user_session import PasswordResetToken, UserSession
from security import hash_password, keyed_digest, verify_password_and_update


@pytest.fixture
def platform():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        cse = Department(code='CSE', name='Computer Science')
        it = Department(code='IT', name='Information Technology')
        db.session.add_all([cse, it])
        db.session.flush()
        year = AcademicYear(label='2026-2027', starts_on=date(2026, 6, 1), ends_on=date(2027, 5, 31))
        cse_course = Course(department_id=cse.id, code='CS', name='Computer Science')
        it_course = Course(department_id=it.id, code='IT', name='Information Technology')
        db.session.add_all([year, cse_course, it_course])
        db.session.flush()
        semester = Semester(academic_year=year, number=1, starts_on=date(2026, 6, 1), ends_on=date(2026, 12, 31))
        cse_batch_a = Batch(course=cse_course, academic_year=year, code='2026A')
        cse_batch_b = Batch(course=cse_course, academic_year=year, code='2026B')
        it_batch = Batch(course=it_course, academic_year=year, code='2026')
        db.session.add_all([semester, cse_batch_a, cse_batch_b, it_batch])
        db.session.flush()
        section_a = Section(batch=cse_batch_a, code='A')
        section_b = Section(batch=cse_batch_b, code='B')
        it_section = Section(batch=it_batch, code='A')
        super_admin = User(username='super-ann', password_hash=hash_password('Platform-Password7!'), role='SUPER_ADMIN')
        manager = User(username='manager-ann', password_hash=hash_password('Platform-Password7!'), role='MANAGEMENT')
        hod = User(username='hod-ann', password_hash=hash_password('Platform-Password7!'), role='HOD', department_id=cse.id)
        department_admin = User(username='dept-admin-ann', password_hash=hash_password('Platform-Password7!'), role='DEPARTMENT_ADMIN', department_id=cse.id)
        faculty_user = User(username='faculty-ann', password_hash=hash_password('Platform-Password7!'), role='FACULTY', department_id=cse.id)
        student_a_user = User(username='student-a-ann', password_hash=hash_password('Platform-Password7!'), role='STUDENT', department_id=cse.id)
        student_b_user = User(username='student-b-ann', password_hash=hash_password('Platform-Password7!'), role='STUDENT', department_id=cse.id)
        db.session.add_all([
            section_a, section_b, it_section, super_admin, manager, hod,
            department_admin, faculty_user, student_a_user, student_b_user,
        ])
        db.session.flush()
        faculty = Faculty(
            employee_id='FAC-ANN', name='Faculty Original', email='faculty@example.test',
            department='CSE', department_id=cse.id, user_id=faculty_user.id,
        )
        student_a = Student(
            register_number='CSE-ANN-A', name='Student A Original', email='student.a@example.test',
            phone='111', department='CSE', department_id=cse.id, section_id=section_a.id,
            year='1', section='A', semester='1', user_id=student_a_user.id,
        )
        student_b = Student(
            register_number='CSE-ANN-B', name='Student B Original', email='student.b@example.test',
            phone='222', department='CSE', department_id=cse.id, section_id=section_b.id,
            year='1', section='B', semester='1', user_id=student_b_user.id,
        )
        subject_a = Subject(
            subject_code='CS-ANN-A', subject_name='Section A Subject', department='CSE',
            semester='1', faculty=faculty,
        )
        subject_b = Subject(
            subject_code='CS-ANN-B', subject_name='Section B Subject', department='CSE',
            semester='1',
        )
        db.session.add_all([faculty, student_a, student_b, subject_a, subject_b])
        db.session.flush()
        assigned_session = ClassSession(
            section_id=section_a.id, semester_id=semester.id, subject_id=subject_a.id,
            faculty_id=faculty.id, weekday=0, starts_at=time(9), ends_at=time(10),
        )
        db.session.add(assigned_session)
        db.session.commit()
        ids = {
            'cse': cse.id, 'it': it.id, 'section_a': section_a.id,
            'section_b': section_b.id, 'it_section': it_section.id,
            'faculty': faculty.id, 'student_a': student_a.id, 'student_b': student_b.id,
            'student_a_user': student_a_user.id, 'student_b_user': student_b_user.id,
            'faculty_user': faculty_user.id, 'assigned_session': assigned_session.id,
        }
    yield app, ids
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(app, username):
    client = app.test_client()
    response = client.post('/login', data={
        'username': username, 'password': 'Platform-Password7!',
    })
    assert response.status_code == 302
    return client


def _add_announcement(title, target_type, department_id=None, section_id=None, *, body='Message', active=True, expires_at=None):
    created_at = expires_at - timedelta(days=1) if expires_at and expires_at < datetime.utcnow() else datetime.utcnow()
    item = Announcement(
        title=title, body=body, target_type=target_type, department_id=department_id,
        section_id=section_id, created_at=created_at, expires_at=expires_at,
        is_active=active,
    )
    db.session.add(item)
    db.session.commit()
    return item.id


def test_students_only_read_institution_own_department_and_assigned_section(platform):
    app, ids = platform
    with app.app_context():
        visible = {
            'institution': _add_announcement('Institution', 'institution'),
            'department': _add_announcement('CSE Dept', 'department', ids['cse']),
            'own_section': _add_announcement('Section A', 'section', ids['cse'], ids['section_a']),
        }
        hidden = {
            'other_section': _add_announcement('Section B', 'section', ids['cse'], ids['section_b']),
            'other_department': _add_announcement('IT Dept', 'department', ids['it']),
            'other_department_section': _add_announcement('IT Section', 'section', ids['it'], ids['it_section']),
            'inactive': _add_announcement('Inactive', 'institution', active=False),
            'expired': _add_announcement('Expired', 'institution', expires_at=datetime.utcnow() - timedelta(minutes=1)),
        }
    client = _login(app, 'student-a-ann')
    response = client.get('/api/announcements')
    assert response.status_code == 200
    listed_ids = {row['id'] for row in response.json}
    assert listed_ids == set(visible.values())
    assert listed_ids.isdisjoint(hidden.values())


def test_faculty_is_limited_to_assigned_section_and_department_admin_cannot_cross_scope(platform):
    app, ids = platform
    with app.app_context():
        other_section_announcement = _add_announcement('Other CSE section', 'section', ids['cse'], ids['section_b'])
        own_section_announcement = _add_announcement('Assigned section', 'section', ids['cse'], ids['section_a'])
        _add_announcement('IT Section', 'section', ids['it'], ids['it_section'])
    faculty = _login(app, 'faculty-ann')
    assert {row['id'] for row in faculty.get('/api/announcements').json} == {own_section_announcement}
    assert other_section_announcement not in {row['id'] for row in faculty.get('/api/announcements').json}
    assert faculty.post('/api/announcements', json={
        'title': 'Forged', 'body': 'No access', 'target_type': 'section',
        'section_id': ids['section_b'],
    }).status_code == 403
    assert faculty.post('/api/announcements', json={
        'title': 'Department scope', 'body': 'No access', 'target_type': 'department',
        'department_id': ids['cse'],
    }).status_code == 403
    mismatch = faculty.post('/api/announcements', json={
        'title': 'Mismatched IDs', 'body': 'No access', 'target_type': 'section',
        'section_id': ids['section_a'], 'department_id': ids['it'],
    })
    assert mismatch.status_code in {400, 403}

    admin = _login(app, 'dept-admin-ann')
    cross_department = admin.post('/api/announcements', json={
        'title': 'Cross dept', 'body': 'No access', 'target_type': 'department',
        'department_id': ids['it'],
    })
    assert cross_department.status_code == 403
    cross_section = admin.post('/api/announcements', json={
        'title': 'Cross section', 'body': 'No access', 'target_type': 'section',
        'section_id': ids['it_section'],
    })
    assert cross_section.status_code == 403


def test_super_admin_can_create_all_scopes_management_is_read_only_and_audit_redacts_content(platform):
    app, ids = platform
    super_client = _login(app, 'super-ann')
    institution = super_client.post('/api/announcements', json={
        'title': 'College notice', 'body': '<script>alert("x")</script>', 'target_type': 'institution',
    })
    department = super_client.post('/api/announcements', json={
        'title': 'CSE notice', 'body': 'Department text', 'target_type': 'department',
        'department_id': ids['cse'],
    })
    section = super_client.post('/api/announcements', json={
        'title': 'Section notice', 'body': 'Section text', 'target_type': 'section',
        'section_id': ids['it_section'],
    })
    assert institution.status_code == department.status_code == section.status_code == 201
    assert institution.json['department_id'] is None and institution.json['section_id'] is None
    assert department.json['department_id'] == ids['cse']
    assert section.json['department_id'] == ids['it']

    page = super_client.get('/announcements')
    assert page.status_code == 200
    assert b'&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;' in page.data
    assert b'<script>alert("x")</script>' not in page.data

    manager = _login(app, 'manager-ann')
    assert len(manager.get('/api/announcements').json) == 3
    denied = manager.post('/api/announcements', json={
        'title': 'Manager write', 'body': 'Not allowed', 'target_type': 'institution',
    })
    assert denied.status_code == 403
    with app.app_context():
        event = SecurityEvent.query.filter_by(action='announcement.created').order_by(SecurityEvent.id.desc()).first()
        serialized = json.dumps({'before': event.before_data, 'after': event.after_data, 'reason': event.reason})
        assert '<script>' not in serialized
        assert 'alert' not in serialized
        assert event.resource_type == 'announcement'


def test_announcement_validates_lengths_dates_and_cross_scope_ids(platform):
    app, ids = platform
    client = _login(app, 'super-ann')
    valid = {'target_type': 'department', 'department_id': ids['cse'], 'title': 'T', 'body': 'B'}
    for payload in (
        {**valid, 'title': '   '},
        {**valid, 'body': '   '},
        {**valid, 'title': 'x' * 161},
        {**valid, 'body': 'x' * 5001},
        {**valid, 'expires_at': 'not-a-date'},
        {**valid, 'expires_at': (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')},
        {**valid, 'section_id': ids['section_a']},
        {'target_type': 'institution', 'department_id': ids['it'], 'title': 'Bad scope', 'body': 'B'},
    ):
        response = client.post('/api/announcements', json=payload)
        assert response.status_code == 400, response.json
    with app.app_context():
        assert Announcement.query.count() == 0


def test_hod_and_student_creation_are_denied_and_page_has_csrf_forms(platform):
    app, ids = platform
    hod = _login(app, 'hod-ann')
    response = hod.post('/api/announcements', json={
        'title': 'Own dept', 'body': 'Allowed', 'target_type': 'department',
        'department_id': ids['cse'],
    })
    assert response.status_code == 201
    student = _login(app, 'student-a-ann')
    assert student.post('/api/announcements', json={
        'title': 'Student write', 'body': 'Denied', 'target_type': 'section',
        'section_id': ids['section_a'],
    }).status_code == 403

    app.config['WTF_CSRF_ENABLED'] = True
    settings_page = hod.get('/account/settings')
    assert settings_page.status_code == 200
    assert b'name="csrf_token"' in settings_page.data
    assert hod.post('/api/announcements', json={
        'title': 'No token', 'body': 'Rejected', 'target_type': 'department',
        'department_id': ids['cse'],
    }).status_code == 400
    assert hod.post('/account/settings', data={
        'action': 'password', 'current_password': 'x', 'new_password': 'New-Password7!',
        'password_confirmation': 'New-Password7!',
    }).status_code == 400


def test_self_service_profile_is_bound_to_authenticated_student_and_limited_fields(platform):
    app, ids = platform
    client = _login(app, 'student-a-ann')
    response = client.get('/account/settings')
    assert response.status_code == 200
    assert b'name="phone"' in response.data
    assert b'name="register_number"' not in response.data
    assert b'name="department_id"' not in response.data
    saved = client.post('/account/settings', data={
        'action': 'profile', 'student_id': str(ids['student_b']),
        'name': 'Updated Own Student', 'email': 'updated.a@example.test', 'phone': '333',
    })
    assert saved.status_code == 302
    with app.app_context():
        own = db.session.get(Student, ids['student_a'])
        other = db.session.get(Student, ids['student_b'])
        assert (own.name, own.email, own.phone) == ('Updated Own Student', 'updated.a@example.test', '333')
        assert (other.name, other.email, other.phone) == ('Student B Original', 'student.b@example.test', '222')
        event = SecurityEvent.query.filter_by(action='account.profile_updated').one()
        serialized = json.dumps({'before': event.before_data, 'after': event.after_data, 'reason': event.reason})
        assert 'Updated Own Student' not in serialized
        assert 'updated.a@example.test' not in serialized
        assert '333' not in serialized

    faculty = _login(app, 'faculty-ann')
    faculty_page = faculty.get('/account/settings')
    assert b'name="name"' in faculty_page.data and b'name="email"' in faculty_page.data
    assert b'name="phone"' not in faculty_page.data
    assert b'name="department"' not in faculty_page.data


def test_password_change_requires_current_password_and_revokes_other_sessions_only(platform):
    app, ids = platform
    client = _login(app, 'student-a-ann')
    second_client = _login(app, 'student-a-ann')
    replacement = 'Replacement-Password8!'

    wrong = client.post('/account/settings', data={
        'action': 'password', 'current_password': 'wrong-password',
        'new_password': replacement, 'password_confirmation': replacement,
    })
    assert wrong.status_code == 302
    with app.app_context():
        user = db.session.get(User, ids['student_a_user'])
        assert verify_password_and_update(user.password_hash, 'Platform-Password7!')[0]
        original_hash = user.password_hash
        reset = PasswordResetToken(
            user_id=user.id, token_hash=keyed_digest('password-reset', 'old-reset'),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        db.session.add(reset)
        db.session.commit()
        reset_id = reset.id

    changed = client.post('/account/settings', data={
        'action': 'password', 'current_password': 'Platform-Password7!',
        'new_password': replacement, 'password_confirmation': replacement,
    })
    assert changed.status_code == 302
    assert client.get('/account/settings').status_code == 200
    assert second_client.get('/account/settings').status_code == 302
    with app.app_context():
        user = db.session.get(User, ids['student_a_user'])
        assert user.password_hash != original_hash
        assert user.password_hash.startswith('$argon2id$')
        assert verify_password_and_update(user.password_hash, replacement) == (True, None)
        sessions = UserSession.query.filter_by(user_id=user.id).all()
        assert sum(item.revoked_at is None for item in sessions) == 1
        current_session_id = None
        with client.session_transaction() as cookie:
            current_session_id = cookie['auth_session_id']
        current_hash = keyed_digest('auth-session', current_session_id)
        current = UserSession.query.filter_by(session_id_hash=current_hash).one()
        assert current.revoked_at is None
        assert db.session.get(PasswordResetToken, reset_id).used_at is not None
        event = SecurityEvent.query.filter_by(action='account.password_changed').one()
        audit_text = json.dumps({
            'before': event.before_data, 'after': event.after_data,
            'reason': event.reason, 'action': event.action,
        })
        assert replacement not in audit_text
        assert 'password_hash' not in audit_text
        assert 'argon2' not in audit_text.lower()
