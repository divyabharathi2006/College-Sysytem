from datetime import date, datetime, time, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from models.attendance import Attendance, AttendanceCorrectionRequest, LeaveRequest
from models.notifications import Notification
from models.academic import AcademicYear, Batch, ClassSession, Course, Department, Section, Semester
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from models.user import User


@pytest.fixture
def workflow_app():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, ATTENDANCE_CORRECTION_WINDOW_HOURS=24)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _login(app, username):
    if username == 'hod-cse':
        with app.app_context():
            assert User.query.filter_by(username=username).one().role == 'HOD'
    client = app.test_client()
    client.post('/logout')
    response = client.post('/login', data={'username': username, 'password': 'password'})
    assert response.status_code == 302
    expected_route = '/admin/hod/dashboard' if username == 'hod-cse' else '/faculty/dashboard' if username.startswith('faculty-') else '/student/dashboard'
    assert response.headers['Location'].endswith(expected_route), response.headers['Location']
    return client


def _seed_workflow_data(app):
    with app.app_context():
        cse = Department(code='CSE', name='Computer Science')
        it = Department(code='IT', name='Information Technology')
        db.session.add_all([cse, it])
        db.session.flush()
        academic_year = AcademicYear(
            label='2026-2027', starts_on=date(2026, 6, 1), ends_on=date(2027, 5, 31),
        )
        db.session.add(academic_year)
        db.session.flush()
        semester = Semester(
            academic_year_id=academic_year.id, number=1,
            starts_on=date(2026, 6, 1), ends_on=date(2026, 12, 31),
        )
        cse_course = Course(department_id=cse.id, code='CS', name='Computer Science')
        it_course = Course(department_id=it.id, code='IT', name='Information Technology')
        db.session.add_all([semester, cse_course, it_course])
        db.session.flush()
        cse_batch = Batch(course_id=cse_course.id, academic_year_id=academic_year.id, code='2026')
        it_batch = Batch(course_id=it_course.id, academic_year_id=academic_year.id, code='2026')
        db.session.add_all([cse_batch, it_batch])
        db.session.flush()
        cse_section = Section(batch_id=cse_batch.id, code='A')
        it_section = Section(batch_id=it_batch.id, code='A')
        db.session.add_all([cse_section, it_section])
        db.session.flush()
        cse_student_user = User(username='student-cse', password_hash=generate_password_hash('password'), role='STUDENT', department_id=cse.id)
        it_student_user = User(username='student-it', password_hash=generate_password_hash('password'), role='STUDENT', department_id=it.id)
        cse_faculty_user = User(username='faculty-cse', password_hash=generate_password_hash('password'), role='FACULTY', department_id=cse.id)
        it_faculty_user = User(username='faculty-it', password_hash=generate_password_hash('password'), role='FACULTY', department_id=it.id)
        cse_hod_user = User(username='hod-cse', password_hash=generate_password_hash('password'), role='HOD', department_id=cse.id)
        db.session.add_all([cse_student_user, it_student_user, cse_faculty_user, it_faculty_user, cse_hod_user])
        db.session.flush()
        cse_student = Student(
            register_number='CSE-1', name='CSE Student', email='cse@example.test', phone='1',
            department='CSE', department_id=cse.id, year='1', section='A', semester='1', user_id=cse_student_user.id,
            section_id=cse_section.id,
        )
        it_student = Student(
            register_number='IT-1', name='IT Student', email='it@example.test', phone='2',
            department='IT', department_id=it.id, year='1', section='A', semester='1', user_id=it_student_user.id,
            section_id=it_section.id,
        )
        cse_faculty = Faculty(
            employee_id='CSE-F1', name='CSE Faculty', email='cf@example.test', department='CSE',
            department_id=cse.id, user_id=cse_faculty_user.id,
        )
        it_faculty = Faculty(
            employee_id='IT-F1', name='IT Faculty', email='if@example.test', department='IT',
            department_id=it.id, user_id=it_faculty_user.id,
        )
        db.session.add_all([cse_student, it_student, cse_faculty, it_faculty])
        db.session.flush()
        cse_subject = Subject(
            subject_code='CS-1', subject_name='Systems', department='CSE', semester='1', faculty_id=cse_faculty.id,
        )
        it_subject = Subject(
            subject_code='IT-1', subject_name='Networks', department='IT', semester='1', faculty_id=it_faculty.id,
        )
        db.session.add_all([cse_subject, it_subject])
        db.session.flush()
        cse_session = ClassSession(
            section_id=cse_section.id, semester_id=semester.id, subject_id=cse_subject.id,
            faculty_id=cse_faculty.id, weekday=date(2026, 10, 1).weekday(),
            starts_at=time(9, 0), ends_at=time(10, 0),
        )
        it_session = ClassSession(
            section_id=it_section.id, semester_id=semester.id, subject_id=it_subject.id,
            faculty_id=it_faculty.id, weekday=date(2026, 10, 1).weekday(),
            starts_at=time(10, 0), ends_at=time(11, 0),
        )
        db.session.add_all([cse_session, it_session])
        db.session.flush()
        cse_record = Attendance(
            student_id=cse_student.id, subject_id=cse_subject.id, date=date(2026, 10, 1),
            status='PRESENT', marked_by=cse_faculty_user.id, session_id=cse_session.id,
        )
        it_record = Attendance(
            student_id=it_student.id, subject_id=it_subject.id, date=date(2026, 10, 1),
            status='ABSENT', marked_by=it_faculty_user.id, session_id=it_session.id,
        )
        db.session.add_all([cse_record, it_record])
        db.session.commit()
        return {
            'cse_department_id': cse.id, 'it_department_id': it.id,
            'cse_student_user_id': cse_student_user.id, 'it_student_user_id': it_student_user.id,
            'cse_faculty_user_id': cse_faculty_user.id,
            'hod_user_id': cse_hod_user.id,
            'cse_student_id': cse_student.id, 'it_student_id': it_student.id,
            'cse_faculty_id': cse_faculty.id, 'it_faculty_id': it_faculty.id,
            'cse_subject_id': cse_subject.id, 'it_subject_id': it_subject.id,
            'cse_record_id': cse_record.id, 'it_record_id': it_record.id,
        }


def test_student_can_submit_view_leave_but_cannot_mutate_attendance(workflow_app):
    ids = _seed_workflow_data(workflow_app)
    client = _login(workflow_app, 'student-cse')

    response = client.post('/student/leave-requests', data={
        'starts_on': '2026-10-12', 'ends_on': '2026-10-13', 'reason': 'Medical appointment and recovery',
    })
    assert response.status_code == 302
    assert client.get('/student/leave-requests').status_code == 200
    assert client.post('/api/attendance', json={
        'student_id': ids['cse_student_id'], 'subject_id': ids['cse_subject_id'],
        'date': '2026-10-02', 'status': 'PRESENT',
    }).status_code == 403
    assert client.put(f"/api/attendance/{ids['cse_record_id']}", json={'status': 'ABSENT'}).status_code == 403
    assert client.delete(f"/api/attendance/{ids['cse_record_id']}").status_code == 403

    with workflow_app.app_context():
        assert Attendance.query.count() == 2
        assert db.session.get(Attendance, ids['cse_record_id']).status == 'PRESENT'
        leave_request = LeaveRequest.query.one()
        assert leave_request.status == 'PENDING'
        assert leave_request.student_id == ids['cse_student_id']


def test_correction_request_is_department_scoped_transactional_and_provenanced(workflow_app):
    ids = _seed_workflow_data(workflow_app)
    faculty_client = _login(workflow_app, 'faculty-cse')
    denied = faculty_client.post('/attendance/corrections', data={
        'attendance_id': ids['it_record_id'], 'new_status': 'LATE', 'reason': 'Corrected after checking register',
    })
    assert denied.status_code == 403

    submitted = faculty_client.post('/attendance/corrections', data={
        'attendance_id': ids['cse_record_id'], 'new_status': 'LATE', 'reason': 'Corrected after checking register',
    })
    assert submitted.status_code == 302
    with workflow_app.app_context():
        correction = AttendanceCorrectionRequest.query.one()
        assert correction.status == 'PENDING'
        assert correction.previous_status == 'PRESENT'
        assert correction.new_status == 'LATE'
        assert correction.requester_role == 'FACULTY'
        assert correction.requested_by == ids['cse_faculty_user_id']
        assert correction.attendance.status == 'PRESENT'
        correction_id = correction.id

    # Requesters may not approve their own submitted correction.
    assert faculty_client.post(
        f'/attendance/corrections/{correction_id}/review', data={'decision': 'APPROVE'},
    ).status_code == 403
    hod_client = _login(workflow_app, 'hod-cse')
    assert ids['hod_user_id'] != ids['cse_faculty_user_id']
    assert hod_client.get('/attendance/corrections').status_code == 200
    approval = hod_client.post(
        f'/attendance/corrections/{correction_id}/review', data={'decision': 'APPROVE'},
    )
    assert approval.status_code == 302, approval.get_data(as_text=True)

    with workflow_app.app_context():
        correction = db.session.get(AttendanceCorrectionRequest, correction_id)
        record = db.session.get(Attendance, ids['cse_record_id'])
        assert correction.status == 'APPROVED'
        assert correction.reviewed_by is not None
        assert correction.reviewer_role == 'HOD'
        assert correction.reviewed_at is not None
        assert record.status == 'LATE'
        notice = Notification.query.filter_by(user_id=ids['cse_faculty_user_id'], category='correction').one()
        assert 'approved' in notice.message

    it_client = _login(workflow_app, 'faculty-it')
    assert it_client.post(
        f'/attendance/corrections/{correction_id}/review', data={'decision': 'REJECT'},
    ).status_code == 403
    hod_client = _login(workflow_app, 'hod-cse')
    assert hod_client.post(
        f'/attendance/corrections/{correction_id}/review', data={'decision': 'REJECT'},
    ).status_code == 409


def test_overdue_faculty_api_edit_creates_request_until_approved(workflow_app):
    ids = _seed_workflow_data(workflow_app)
    workflow_app.config['ATTENDANCE_CORRECTION_WINDOW_HOURS'] = 1
    with workflow_app.app_context():
        record = db.session.get(Attendance, ids['cse_record_id'])
        record.created_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()

    faculty_client = _login(workflow_app, 'faculty-cse')
    response = faculty_client.put(
        f"/api/attendance/{ids['cse_record_id']}",
        json={'status': 'EXCUSED', 'reason': 'Correction verified with the class register'},
    )
    assert response.status_code == 202
    with workflow_app.app_context():
        record = db.session.get(Attendance, ids['cse_record_id'])
        correction = AttendanceCorrectionRequest.query.one()
        assert record.status == 'PRESENT'
        assert correction.new_status == 'EXCUSED'


def test_leave_approval_is_department_scoped_and_does_not_create_attendance(workflow_app):
    ids = _seed_workflow_data(workflow_app)
    student_client = _login(workflow_app, 'student-cse')
    assert student_client.post('/student/leave-requests', data={
        'starts_on': '2026-11-01', 'ends_on': '2026-11-02', 'reason': 'Family obligation requiring travel',
    }).status_code == 302
    with workflow_app.app_context():
        leave_id = LeaveRequest.query.one().id
        record_count = Attendance.query.count()

    it_client = _login(workflow_app, 'faculty-it')
    assert it_client.post(
        f'/attendance/leave-requests/{leave_id}/review', data={'decision': 'APPROVE'},
    ).status_code == 403
    hod_client = _login(workflow_app, 'hod-cse')
    assert hod_client.get('/attendance/leave-requests').status_code == 200
    assert hod_client.post(
        f'/attendance/leave-requests/{leave_id}/review', data={'decision': 'APPROVE'},
    ).status_code == 302
    with workflow_app.app_context():
        leave_request = db.session.get(LeaveRequest, leave_id)
        assert leave_request.status == 'APPROVED'
        assert leave_request.reviewer_role == 'HOD'
        assert leave_request.reviewed_at is not None
        notice = Notification.query.filter_by(user_id=ids['cse_student_user_id'], category='leave').one()
        assert 'approved' in notice.message
        assert Attendance.query.count() == record_count
