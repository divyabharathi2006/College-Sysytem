from datetime import date, time
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from models.attendance import Attendance
from models.academic import AcademicYear, Batch, ClassSession, Classroom, Course, Department, Section, Semester
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from models.user import User
from permissions import Role, can_access


@pytest.fixture
def academic_app():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _account(app, username, role, department_id=None):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                password_hash=generate_password_hash('test-password'),
                role=role,
                department_id=department_id,
            )
            db.session.add(user)
            db.session.commit()
    client = app.test_client()
    client.post('/logout')
    response = client.post('/login', data={'username': username, 'password': 'test-password'})
    assert response.status_code == 302
    return client


def _department(app, code):
    with app.app_context():
        department = Department(code=code, name=f'{code} Department')
        db.session.add(department)
        db.session.commit()
        return department.id


def test_scoped_roles_have_distinct_permissions_and_cross_department_crud_is_denied(academic_app):
    cse_id = _department(academic_app, 'CSE')
    it_id = _department(academic_app, 'IT')
    with academic_app.app_context():
        cse_hod = User(username='hod-cse', password_hash=generate_password_hash('test-password'), role='HOD', department_id=cse_id)
        it_course = Course(department_id=it_id, code='IT-1', name='IT Course')
        db.session.add_all([cse_hod, it_course])
        db.session.commit()
        it_course_id = it_course.id
        assert can_access(cse_hod, 'course:manage', department_id=cse_id)
        assert not can_access(cse_hod, 'course:manage', department_id=it_id)
        assert not can_access(cse_hod, 'student:manage', department_id=cse_id)
        dep_admin = User(username='admin-cse', password_hash='hash', role='DEPARTMENT_ADMIN', department_id=cse_id)
        manager = User(username='management', password_hash=generate_password_hash('test-password'), role='MANAGEMENT')
        db.session.add_all([dep_admin, manager])
        db.session.commit()
        assert can_access(dep_admin, 'student:manage', department_id=cse_id)
        assert not can_access(dep_admin, 'attendance:manage', department_id=cse_id)
        assert can_access(manager, 'analytics:read')
        assert not can_access(manager, 'course:manage', department_id=cse_id)

    client = _account(academic_app, 'hod-cse', 'HOD', cse_id)
    assert client.get('/admin/academic').status_code == 200
    assert client.get('/admin/academic/courses').json == []
    response = client.post('/admin/academic/courses', json={
        'department_id': it_id, 'code': 'BAD', 'name': 'Cross-department attempt',
    })
    assert response.status_code == 403
    response = client.post(f'/admin/academic/courses/{it_course_id}', json={'action': 'edit', 'name': 'Stolen'})
    assert response.status_code == 403
    with academic_app.app_context():
        assert Course.query.filter_by(code='BAD').count() == 0
        assert db.session.get(Course, it_course_id).name == 'IT Course'

    client.post('/logout')
    login = client.post('/login', data={'username': 'management', 'password': 'test-password'})
    assert login.status_code == 302
    assert client.get('/admin/management/dashboard').status_code == 200
    assert client.get('/admin/academic').status_code == 403
    assert client.post('/admin/academic/courses', json={'department_id': cse_id, 'code': 'X', 'name': 'Denied'}).status_code == 403


def test_conflicting_department_ids_fail_closed_and_legacy_text_cannot_expand_linked_scope(academic_app):
    cse_id = _department(academic_app, 'CSE')
    it_id = _department(academic_app, 'IT')
    with academic_app.app_context():
        conflicting_hod = SimpleNamespace(
            id=901, role='HOD', department_id=cse_id,
            faculty_profile=SimpleNamespace(department_id=it_id, department='CSE'),
            student_profile=None,
        )
        assert not can_access(conflicting_hod, 'course:manage', department_id=cse_id)
        assert not can_access(conflicting_hod, 'course:manage', department_id=it_id)
        linked_hod_with_stale_text = SimpleNamespace(
            id=902, role='HOD', department_id=cse_id,
            faculty_profile=SimpleNamespace(department_id=None, department='IT'),
            student_profile=None,
        )
        assert can_access(linked_hod_with_stale_text, 'course:manage', department_id=cse_id)
        assert not can_access(linked_hod_with_stale_text, 'course:manage', department_id=it_id)


def test_super_admin_academic_crud_validates_duplicates_foreign_keys_and_preserves_links(academic_app):
    department_id = _department(academic_app, 'CSE')
    client = _account(academic_app, 'super', 'admin')
    duplicate_name = client.post('/admin/academic/departments', json={
        'code': 'CSE-ALT', 'name': 'cse department',
    })
    assert duplicate_name.status_code == 409

    created = client.post('/admin/academic/courses', json={
        'department_id': department_id, 'code': 'CS-1', 'name': 'Computer Science',
    })
    assert created.status_code == 201
    course_id = created.json['id']
    duplicate = client.post('/admin/academic/courses', json={
        'department_id': department_id, 'code': 'CS-1', 'name': 'Duplicate course',
    })
    assert duplicate.status_code == 409
    invalid_fk = client.post('/admin/academic/batches', json={
        'course_id': 99999, 'academic_year_id': 99999, 'code': '2026',
    })
    assert invalid_fk.status_code in {400, 403}

    year = client.post('/admin/academic/years', json={
        'label': '2026-2027', 'starts_on': '2026-06-01', 'ends_on': '2027-05-31',
    })
    assert year.status_code == 201
    year_id = year.json['id']
    assert client.post('/admin/academic/semesters', json={
        'academic_year_id': year_id, 'number': 1, 'starts_on': '2026-06-01', 'ends_on': '2026-11-30',
    }).status_code == 201
    batch = client.post('/admin/academic/batches', json={
        'course_id': course_id, 'academic_year_id': year_id, 'code': '2026',
    })
    assert batch.status_code == 201
    section = client.post('/admin/academic/sections', json={'batch_id': batch.json['id'], 'code': 'A'})
    assert section.status_code == 201
    classroom = client.post('/admin/academic/classrooms', json={
        'department_id': department_id, 'code': 'CSE-101', 'building': 'North', 'capacity': 40,
    })
    assert classroom.status_code == 201

    with academic_app.app_context():
        department = db.session.get(Department, department_id)
        faculty_user = User(username='faculty', password_hash='hash', role='FACULTY', department_id=department_id)
        db.session.add(faculty_user)
        db.session.flush()
        faculty = Faculty(
            employee_id='F-1', name='Faculty One', email='faculty@example.test', department='CSE',
            department_id=department_id, user_id=faculty_user.id,
        )
        subject = Subject(subject_code='CS101', subject_name='Intro CS', department=department.code, semester='1')
        db.session.add_all([faculty, subject])
        db.session.commit()
        faculty_id, subject_id = faculty.id, subject.id

    session = client.post('/admin/academic/sessions', json={
        'section_id': section.json['id'], 'semester_id': 1, 'subject_id': subject_id,
        'faculty_id': faculty_id, 'classroom_id': classroom.json['id'],
        'weekday': 0, 'starts_at': '09:00', 'ends_at': '10:00',
    })
    assert session.status_code == 201
    overlap = client.post('/admin/academic/sessions', json={
        'section_id': section.json['id'], 'semester_id': 1, 'subject_id': subject_id,
        'faculty_id': faculty_id, 'classroom_id': classroom.json['id'],
        'weekday': 0, 'starts_at': '09:30', 'ends_at': '10:30',
    })
    assert overlap.status_code == 400

    edited = client.post(f'/admin/academic/courses/{course_id}', json={'action': 'edit', 'name': 'Updated Course'})
    assert edited.status_code == 200
    deleted = client.post(f'/admin/academic/courses/{course_id}', json={'action': 'delete'})
    assert deleted.status_code == 409  # Existing batch reference is retained.
    with academic_app.app_context():
        assert db.session.get(Course, course_id).name == 'Updated Course'
        assert db.session.get(ClassSession, session.json['id']) is not None
        assert db.session.get(Classroom, classroom.json['id']).department_id == department_id


def test_department_role_assignment_links_existing_profile_without_replacing_legacy_text(academic_app):
    department_id = _department(academic_app, 'CSE')
    with academic_app.app_context():
        user = User(username='faculty-to-link', password_hash='hash', role='student')
        db.session.add(user)
        db.session.flush()
        profile = Faculty(employee_id='F-22', name='Legacy Faculty', email='f@example.test', department='CSE', user_id=user.id)
        db.session.add(profile)
        db.session.commit()
        user_id, profile_id = user.id, profile.id
    client = _account(academic_app, 'super', 'admin')
    response = client.post(f'/admin/academic/assignments/{user_id}', json={
        'role': 'FACULTY', 'department_id': department_id,
    })
    assert response.status_code == 200
    with academic_app.app_context():
        assert db.session.get(User, user_id).department_id == department_id
        profile = db.session.get(Faculty, profile_id)
        assert profile.department_id == department_id
        assert profile.department == 'CSE'


def test_csrf_is_required_for_academic_mutations_when_enabled(academic_app):
    department_id = _department(academic_app, 'CSE')
    client = _account(academic_app, 'csrf-admin', 'admin')
    academic_app.config['WTF_CSRF_ENABLED'] = True
    response = client.post('/admin/academic/courses', json={
        'department_id': department_id, 'code': 'CS-CSRF', 'name': 'CSRF check',
    })
    assert response.status_code == 400
    with academic_app.app_context():
        assert Course.query.filter_by(code='CS-CSRF').count() == 0


def test_faculty_api_denies_unlinked_students_and_sessionless_historical_records(academic_app):
    cse_id = _department(academic_app, 'CSE')
    it_id = _department(academic_app, 'IT')
    with academic_app.app_context():
        faculty_user = User(
            username='faculty-scope', password_hash=generate_password_hash('test-password'),
            role='FACULTY', department_id=cse_id,
        )
        cse_student_user = User(username='cse-student-api', password_hash='hash', role='STUDENT', department_id=cse_id)
        it_student_user = User(username='it-student-api', password_hash='hash', role='STUDENT', department_id=it_id)
        db.session.add_all([faculty_user, cse_student_user, it_student_user])
        db.session.flush()
        faculty = Faculty(
            employee_id='FAC-API', name='CSE Faculty', email='faculty-api@example.test',
            department='CSE', department_id=cse_id, user_id=faculty_user.id,
        )
        cse_student = Student(
            register_number='CSE-API', name='CSE Student', email='cse-api@example.test', phone='1',
            department='CSE', department_id=cse_id, year='1', section='A', semester='1', user_id=cse_student_user.id,
        )
        it_student = Student(
            register_number='IT-API', name='IT Student', email='it-api@example.test', phone='2',
            department='IT', department_id=it_id, year='1', section='A', semester='1', user_id=it_student_user.id,
        )
        cse_subject = Subject(
            subject_code='CSE-API', subject_name='CSE API Subject', department='CSE', semester='1',
        )
        db.session.add_all([faculty, cse_student, it_student, cse_subject])
        db.session.flush()
        cse_subject.faculty_id = faculty.id
        own_record = Attendance(
            student_id=cse_student.id, subject_id=cse_subject.id, date=date(2026, 10, 1),
            status='PRESENT', marked_by=faculty_user.id,
        )
        foreign_record = Attendance(
            student_id=it_student.id, subject_id=cse_subject.id, date=date(2026, 10, 1),
            status='PRESENT', marked_by=faculty_user.id,
        )
        db.session.add_all([own_record, foreign_record])
        db.session.commit()
        cse_student_id, it_student_id = cse_student.id, it_student.id
        own_record_id, foreign_record_id, subject_id = own_record.id, foreign_record.id, cse_subject.id

    client = _account(academic_app, 'faculty-scope', 'FACULTY', cse_id)
    assert client.get('/admin/academic/years').status_code == 403
    assert client.get(f'/api/attendance/student/{cse_student_id}').status_code == 403
    assert client.get(f'/api/attendance/student/{it_student_id}').status_code == 403
    assert client.put(f'/api/attendance/{foreign_record_id}', json={'status': 'ABSENT'}).status_code == 403
    assert client.put(f'/api/attendance/{own_record_id}', json={'status': 'ABSENT'}).status_code == 403
    response = client.post('/api/attendance', json={
        'student_id': it_student_id, 'subject_id': subject_id, 'date': '2026-10-02', 'status': 'PRESENT',
    })
    assert response.status_code == 403


def test_faculty_student_and_attendance_access_requires_assigned_section_session(academic_app):
    cse_id = _department(academic_app, 'CSE')
    it_id = _department(academic_app, 'IT')
    with academic_app.app_context():
        year = AcademicYear(label='2027-2028', starts_on=date(2027, 6, 1), ends_on=date(2028, 5, 31))
        cse_course = Course(department_id=cse_id, code='CS', name='Computer Science')
        it_course = Course(department_id=it_id, code='IT', name='Information Technology')
        db.session.add_all([year, cse_course, it_course])
        db.session.flush()
        semester = Semester(
            academic_year_id=year.id, number=1, starts_on=date(2027, 6, 1), ends_on=date(2027, 12, 31),
        )
        db.session.add(semester)
        db.session.flush()
        cse_batch = Batch(course_id=cse_course.id, academic_year_id=year.id, code='2027')
        cse_batch_other = Batch(course_id=cse_course.id, academic_year_id=year.id, code='2027B')
        it_batch = Batch(course_id=it_course.id, academic_year_id=year.id, code='2027')
        db.session.add_all([cse_batch, cse_batch_other, it_batch])
        db.session.flush()
        section_a = Section(batch_id=cse_batch.id, code='A')
        section_b = Section(batch_id=cse_batch_other.id, code='B')
        it_section = Section(batch_id=it_batch.id, code='A')
        assigned_user = User(username='faculty-section', password_hash=generate_password_hash('test-password'), role='FACULTY', department_id=cse_id)
        idle_user = User(username='faculty-no-class', password_hash=generate_password_hash('test-password'), role='FACULTY', department_id=cse_id)
        admin_user = User(username='department-admin-section', password_hash=generate_password_hash('test-password'), role='DEPARTMENT_ADMIN', department_id=cse_id)
        student_users = [
            User(username='section-student-a', password_hash='hash', role='STUDENT', department_id=cse_id),
            User(username='section-student-b', password_hash='hash', role='STUDENT', department_id=cse_id),
            User(username='section-student-unlinked', password_hash='hash', role='STUDENT', department_id=cse_id),
            User(username='section-student-it', password_hash='hash', role='STUDENT', department_id=it_id),
            User(username='section-student-mislinked', password_hash='hash', role='STUDENT', department_id=it_id),
        ]
        db.session.add_all([section_a, section_b, it_section, assigned_user, idle_user, admin_user, *student_users])
        db.session.flush()
        assigned_faculty = Faculty(
            employee_id='FAC-SECTION', name='Assigned Faculty', email='assigned@example.test',
            department='CSE', department_id=cse_id, user_id=assigned_user.id,
        )
        idle_faculty = Faculty(
            employee_id='FAC-IDLE', name='Faculty without classes', email='idle@example.test',
            department='CSE', department_id=cse_id, user_id=idle_user.id,
        )
        subject = Subject(
            subject_code='CS-SECTION', subject_name='Section Subject', department='CSE',
            semester='1', faculty=assigned_faculty,
        )
        idle_subject = Subject(
            subject_code='CS-IDLE', subject_name='Unscheduled Subject', department='CSE',
            semester='1', faculty=idle_faculty,
        )
        students = [
            Student(register_number='SEC-A', name='Student A', email='a@example.test', phone='1',
                    department='CSE', department_id=cse_id, year='1', section='A', semester='1', user_id=student_users[0].id),
            Student(register_number='SEC-B', name='Student B', email='b@example.test', phone='2',
                    department='CSE', department_id=cse_id, year='1', section='B', semester='1', user_id=student_users[1].id),
            Student(register_number='SEC-UNLINKED', name='Unlinked Student', email='u@example.test', phone='3',
                    department='CSE', department_id=cse_id, year='1', section='A', semester='1', user_id=student_users[2].id),
            Student(register_number='SEC-IT', name='IT Student', email='it@example.test', phone='4',
                    department='IT', department_id=it_id, year='1', section='A', semester='1', user_id=student_users[3].id),
                Student(register_number='SEC-MISLINKED', name='Mislinked Student', email='m@example.test', phone='5',
                    department='IT', department_id=it_id, year='1', section='A', semester='1', user_id=student_users[4].id,
                    section_id=section_a.id),
        ]
        db.session.add_all([assigned_faculty, idle_faculty, subject, idle_subject, *students])
        db.session.flush()
        class_session = ClassSession(
            section_id=section_a.id, semester_id=semester.id, subject_id=subject.id,
            faculty_id=assigned_faculty.id, weekday=0, starts_at=time(9, 0), ends_at=time(10, 0),
        )
        db.session.add(class_session)
        db.session.commit()
        student_a_id, student_b_id, unlinked_id, it_student_id, mislinked_student_id = [student.id for student in students]
        section_a_id, section_b_id, it_section_id = section_a.id, section_b.id, it_section.id
        user_a_id, user_b_id, user_it_id = student_users[0].id, student_users[1].id, student_users[3].id
        class_session_id, subject_id, idle_subject_id = class_session.id, subject.id, idle_subject.id
        assigned_user_id, idle_user_id, admin_user_id = assigned_user.id, idle_user.id, admin_user.id
        legacy_text = (students[0].department, students[0].section)

    # Department Admin can assign inside their department and is barred from other departments.
    department_admin = _account(academic_app, 'department-admin-section', 'DEPARTMENT_ADMIN', cse_id)
    assigned = department_admin.post(
        f'/admin/academic/students/{student_a_id}/section', json={'section_id': section_a_id},
    )
    assert assigned.status_code == 200
    assert department_admin.post(
        f'/admin/academic/students/{it_student_id}/section', json={'section_id': it_section_id},
    ).status_code == 403

    super_admin = _account(academic_app, 'super-section', 'admin')
    assert super_admin.post(
        f'/admin/academic/students/{student_b_id}/section', json={'section_id': section_b_id},
    ).status_code == 200
    assert super_admin.get('/admin/academic').status_code == 200
    with academic_app.app_context():
        student_a = db.session.get(Student, student_a_id)
        assert student_a.section_id == section_a_id
        assert (student_a.department, student_a.section) == legacy_text
        assert db.session.get(Student, student_b_id).section_id == section_b_id
        assert db.session.get(Student, unlinked_id).section_id is None
        assert db.session.get(Student, it_student_id).section_id is None
        assert db.session.get(Student, mislinked_student_id).section_id == section_a_id

    faculty = _account(academic_app, 'faculty-section', 'FACULTY', cse_id)
    listed = faculty.get('/api/students')
    assert listed.status_code == 200
    assert [row['id'] for row in listed.json] == [student_a_id]
    assert faculty.get(f'/api/attendance/student/{student_a_id}').status_code == 200
    assert faculty.get(f'/api/attendance/student/{student_b_id}').status_code == 403
    assert faculty.get(f'/api/attendance/student/{unlinked_id}').status_code == 403
    assert faculty.get(f'/api/attendance/student/{it_student_id}').status_code == 403
    assert faculty.get(f'/api/attendance/student/{mislinked_student_id}').status_code == 403
    dashboard = faculty.get('/faculty/dashboard')
    assert dashboard.status_code == 200
    assert b'Student A' in dashboard.data and b'Student B' not in dashboard.data

    selected_roster = faculty.get(
        f'/faculty/mark-attendance?subject_id={subject_id}&session_id={class_session_id}&date=2027-06-07',
    )
    assert selected_roster.status_code == 200
    assert b'SEC-A' in selected_roster.data and b'SEC-B' not in selected_roster.data
    before = None
    with academic_app.app_context():
        before = Attendance.query.count()
    # A forged form cannot smuggle a same-department student from another section.
    tampered = faculty.post('/faculty/mark-attendance', data={
        'subject_id': str(subject_id), 'session_id': str(class_session_id),
        'date': '2027-06-07', f'status_{student_b_id}': 'PRESENT',
    })
    assert tampered.status_code == 302
    with academic_app.app_context():
        assert Attendance.query.count() == before

    valid = faculty.post('/api/attendance', json={
        'student_id': student_a_id, 'subject_id': subject_id,
        'session_id': class_session_id, 'date': '2027-06-07', 'status': 'PRESENT',
    })
    assert valid.status_code == 201
    marked_form = faculty.post('/faculty/mark-attendance', data={
        'subject_id': str(subject_id), 'session_id': str(class_session_id),
        'date': '2027-06-07', f'status_{student_a_id}': 'PRESENT',
    })
    assert marked_form.status_code == 302
    with academic_app.app_context():
        assert Attendance.query.filter_by(
            student_id=student_a_id, subject_id=subject_id, session_id=class_session_id,
        ).count() == 1
    cross_section = faculty.post('/api/attendance', json={
        'student_id': student_b_id, 'subject_id': subject_id,
        'session_id': class_session_id, 'date': '2027-06-07', 'status': 'PRESENT',
    })
    assert cross_section.status_code == 400
    assert faculty.post('/api/attendance', json={
        'student_id': student_a_id, 'subject_id': subject_id,
        'date': '2027-06-07', 'status': 'PRESENT',
    }).status_code == 403

    unassigned_faculty = _account(academic_app, 'faculty-no-class', 'FACULTY', cse_id)
    assert unassigned_faculty.get(f'/api/attendance/student/{student_a_id}').status_code == 403
    assert unassigned_faculty.get('/api/students').json == []
    assert unassigned_faculty.post('/api/attendance', json={
        'student_id': student_a_id, 'subject_id': idle_subject_id,
        'date': '2027-06-07', 'status': 'PRESENT',
    }).status_code == 403
