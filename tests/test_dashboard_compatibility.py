from datetime import date, time, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from attendance_engine import required_continuous_classes, required_classes_to_attend, risk_level
from models.academic import AcademicYear, Batch, ClassSession, Course, Department, Section, Semester
from models.attendance import Attendance, AttendanceCorrectionRequest, LeaveRequest
from models.faculty import Faculty
from models.notifications import Notification
from models.prediction import PredictionHistory
from models.student import Student
from models.subject import Subject
from models.user import User
from prediction_model import predict_attendance


@pytest.fixture
def dashboard_app():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _seed(dashboard_app):
    today = date.today()
    with dashboard_app.app_context():
        cse = Department(code='CSE', name='Computer Science')
        it = Department(code='IT', name='Information Technology')
        db.session.add_all([cse, it])
        db.session.flush()
        year = AcademicYear(label='2026-2027', starts_on=date(2026, 6, 1), ends_on=date(2027, 5, 31))
        db.session.add(year)
        db.session.flush()
        semester = Semester(
            academic_year_id=year.id, number=1, starts_on=date(2026, 6, 1), ends_on=date(2026, 12, 31),
        )
        cse_course = Course(department_id=cse.id, code='CS', name='Computer Science')
        it_course = Course(department_id=it.id, code='IT', name='Information Technology')
        db.session.add_all([semester, cse_course, it_course])
        db.session.flush()
        cse_batch = Batch(course_id=cse_course.id, academic_year_id=year.id, code='2026')
        it_batch = Batch(course_id=it_course.id, academic_year_id=year.id, code='2026')
        db.session.add_all([cse_batch, it_batch])
        db.session.flush()
        cse_section_a = Section(batch_id=cse_batch.id, code='A')
        cse_section_b = Section(batch_id=cse_batch.id, code='B')
        it_section = Section(batch_id=it_batch.id, code='A')
        db.session.add_all([cse_section_a, cse_section_b, it_section])
        db.session.flush()

        role_users = {
            name: User(
                username=name, password_hash=generate_password_hash('test-password'),
                role=role, department_id=department_id,
            )
            for name, role, department_id in (
                ('hod-cse-dashboard', 'HOD', cse.id),
                ('admin-cse-dashboard', 'DEPARTMENT_ADMIN', cse.id),
                ('faculty-cse-dashboard', 'FACULTY', cse.id),
                ('faculty-cse-other-section', 'FACULTY', cse.id),
                ('student-cse-dashboard', 'STUDENT', cse.id),
                ('student-cse-other-section', 'STUDENT', cse.id),
                ('student-it-dashboard', 'STUDENT', it.id),
                ('faculty-it-dashboard', 'FACULTY', it.id),
            )
        }
        db.session.add_all(role_users.values())
        db.session.flush()
        cse_faculty = Faculty(
            employee_id='CSE-F1', name='CSE Faculty', email='faculty-cse@example.test',
            department='CSE', department_id=cse.id, user_id=role_users['faculty-cse-dashboard'].id,
        )
        cse_other_faculty = Faculty(
            employee_id='CSE-F2', name='CSE Other Faculty', email='faculty-other@example.test',
            department='CSE', department_id=cse.id, user_id=role_users['faculty-cse-other-section'].id,
        )
        it_faculty = Faculty(
            employee_id='IT-F1', name='IT Faculty', email='faculty-it@example.test',
            department='IT', department_id=it.id, user_id=role_users['faculty-it-dashboard'].id,
        )
        students = [
            Student(register_number='CSE-A1', name='CSE Student A', email='a@example.test', phone='1',
                    department='CSE', department_id=cse.id, year='1', section='A', semester='1',
                    section_id=cse_section_a.id, user_id=role_users['student-cse-dashboard'].id),
            Student(register_number='CSE-B1', name='CSE Student B', email='b@example.test', phone='2',
                    department='CSE', department_id=cse.id, year='1', section='B', semester='1',
                    section_id=cse_section_b.id, user_id=role_users['student-cse-other-section'].id),
            Student(register_number='IT-A1', name='IT Student A', email='it@example.test', phone='3',
                    department='IT', department_id=it.id, year='1', section='A', semester='1',
                    section_id=it_section.id, user_id=role_users['student-it-dashboard'].id),
        ]
        db.session.add_all([cse_faculty, cse_other_faculty, it_faculty, *students])
        db.session.flush()
        subject_a = Subject(
            subject_code='CS101', subject_name='Algorithms', department='CSE', semester='1', faculty_id=cse_faculty.id,
        )
        subject_b = Subject(
            subject_code='CS102', subject_name='Data Structures', department='CSE', semester='1', faculty_id=cse_other_faculty.id,
        )
        unassigned_subject = Subject(
            subject_code='CS103', subject_name='Unscheduled Elective', department='CSE', semester='1', faculty_id=cse_faculty.id,
        )
        subject_it = Subject(
            subject_code='IT101', subject_name='Networks', department='IT', semester='1', faculty_id=it_faculty.id,
        )
        db.session.add_all([subject_a, subject_b, unassigned_subject, subject_it])
        db.session.flush()
        session_a = ClassSession(
            section_id=cse_section_a.id, semester_id=semester.id, subject_id=subject_a.id,
            faculty_id=cse_faculty.id, weekday=today.weekday(), starts_at=time(9, 0), ends_at=time(10, 0),
        )
        session_b = ClassSession(
            section_id=cse_section_b.id, semester_id=semester.id, subject_id=subject_b.id,
            faculty_id=cse_other_faculty.id, weekday=today.weekday(), starts_at=time(10, 0), ends_at=time(11, 0),
        )
        session_it = ClassSession(
            section_id=it_section.id, semester_id=semester.id, subject_id=subject_it.id,
            faculty_id=it_faculty.id, weekday=today.weekday(), starts_at=time(11, 0), ends_at=time(12, 0),
        )
        db.session.add_all([session_a, session_b, session_it])
        db.session.flush()
        record_present = Attendance(
            student_id=students[0].id, subject_id=subject_a.id, session_id=session_a.id,
            date=today - timedelta(days=7), status='PRESENT', marked_by=role_users['faculty-cse-dashboard'].id,
        )
        record_absent = Attendance(
            student_id=students[0].id, subject_id=subject_a.id, session_id=session_a.id,
            date=today - timedelta(days=14), status='ABSENT', marked_by=role_users['faculty-cse-dashboard'].id,
        )
        other_section_record = Attendance(
            student_id=students[1].id, subject_id=subject_b.id, session_id=session_b.id,
            date=today - timedelta(days=7), status='PRESENT', marked_by=role_users['faculty-cse-other-section'].id,
        )
        it_record = Attendance(
            student_id=students[2].id, subject_id=subject_it.id, session_id=session_it.id,
            date=today - timedelta(days=7), status='PRESENT', marked_by=role_users['faculty-it-dashboard'].id,
        )
        db.session.add_all([record_present, record_absent, other_section_record, it_record])
        db.session.flush()
        db.session.add_all([
            LeaveRequest(
                student_id=students[0].id, starts_on=today, ends_on=today,
                reason='Medical appointment', requested_by=role_users['student-cse-dashboard'].id,
            ),
            LeaveRequest(
                student_id=students[2].id, starts_on=today, ends_on=today,
                reason='Family appointment', requested_by=role_users['student-it-dashboard'].id,
            ),
            AttendanceCorrectionRequest(
                attendance_id=record_present.id, previous_status='PRESENT', new_status='LATE',
                reason='Verified against the class register', requested_by=role_users['faculty-cse-dashboard'].id,
                requester_role='FACULTY',
            ),
            PredictionHistory(
                student_id=students[0].id, model_version='deterministic-v1',
                feature_snapshot={'attendance_records': 2, 'present_records': 1},
                current_percentage=50.0, predicted_percentage=80.0, risk='LOW RISK',
            ),
            Notification(
                user_id=role_users['student-cse-dashboard'].id, category='leave',
                title='Leave update', message='Your request is pending.',
            ),
            Notification(
                user_id=role_users['student-it-dashboard'].id, category='leave',
                title='Other student notice', message='Private notice.',
            ),
        ])
        db.session.commit()
        return {
            'cse_department_id': cse.id,
            'it_department_id': it.id,
            'cse_student_id': students[0].id,
            'cse_student_user_id': role_users['student-cse-dashboard'].id,
            'it_student_id': students[2].id,
            'session_a_id': session_a.id,
            'subject_a_id': subject_a.id,
        }


def _login(dashboard_app, username):
    client = dashboard_app.test_client()
    response = client.post('/login', data={'username': username, 'password': 'test-password'})
    assert response.status_code == 302
    return client


def test_hod_department_dashboard_is_scoped_and_has_department_analytics(dashboard_app):
    _seed(dashboard_app)
    client = _login(dashboard_app, 'hod-cse-dashboard')
    response = client.get('/admin/hod/dashboard')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'CSE Student A' in page
    assert 'IT Student A' not in page
    assert 'Networks' not in page
    assert 'Algorithms' in page
    assert 'Attendance by class' in page and 'Attendance by section' in page
    assert 'Faculty performance' in page
    assert 'Pending leave requests' in page and '>1</h3>' in page
    assert 'Pending correction requests' in page
    assert 'Defaulters' in page and 'At-risk students' in page
    assert client.get('/admin/department/dashboard').status_code == 403


def test_department_admin_has_distinct_aggregate_overview_and_navigation(dashboard_app):
    _seed(dashboard_app)
    client = _login(dashboard_app, 'admin-cse-dashboard')
    response = client.get('/admin/department/dashboard')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'Department Overview' in page
    assert 'Academic setup' in page
    assert 'Department attendance report' in page
    assert 'CSE Student A' not in page and 'IT Student A' not in page
    assert 'Pending leave requests' not in page and 'Correction requests' not in page
    assert client.get('/admin/hod/dashboard').status_code == 403
    assert client.get('/admin/academic').status_code == 200


def test_faculty_dashboard_uses_only_assigned_sessions_for_timetable_and_metrics(dashboard_app):
    _seed(dashboard_app)
    client = _login(dashboard_app, 'faculty-cse-dashboard')
    response = client.get('/faculty/dashboard')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Today's timetable" in page
    assert 'Algorithms' in page
    assert 'CS · 2026' in page and '>A<' in page
    assert 'Unscheduled Elective' not in page
    assert 'CSE Student A' in page
    assert 'CSE Student B' not in page and 'IT Student A' not in page
    assert 'Attendance by assigned subject and section' in page
    assert 'IT101' not in page and 'CS102' not in page


def test_student_dashboard_shows_only_owned_leave_notifications_and_prediction_data(dashboard_app):
    ids = _seed(dashboard_app)
    client = _login(dashboard_app, 'student-cse-dashboard')
    response = client.get('/student/dashboard')
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'Notifications (1 unread)' in page
    assert 'Leave history and requests' in page
    assert 'Prediction history' in page
    assert 'Current risk:' in page and 'HIGH RISK' in page
    assert 'Consecutive attended classes needed:' in page
    assert 'CSE Student B' not in page and 'IT Student A' not in page
    assert client.get('/student/prediction#prediction-history-heading').status_code == 200
    prediction_page = client.get('/student/prediction').get_data(as_text=True)
    assert 'Target:' in prediction_page and 'Consecutive classes to target' in prediction_page
    assert '80.0%' in prediction_page and 'NORMAL' in prediction_page
    assert 'Other student notice' not in prediction_page
    api_result = client.get(f"/api/prediction/student/{ids['cse_student_id']}").get_json()
    v1_result = client.get(f"/api/v1/prediction/student/{ids['cse_student_id']}").get_json()
    assert api_result == v1_result
    assert api_result['target_percentage'] == 75.0
    assert api_result['required_continuous_classes'] == 2
    assert {'current_percentage', 'predicted_percentage', 'trend', 'risk', 'recommended_action', 'subject_name'}.issubset(api_result)


def test_risk_thresholds_required_classes_and_additive_prediction_fields():
    assert [risk_level(value) for value in (90, 89.99, 75, 74.99, 65, 64.99, 50, 49.99)] == [
        'SAFE', 'NORMAL', 'NORMAL', 'WARNING', 'WARNING', 'HIGH RISK', 'HIGH RISK', 'CRITICAL',
    ]
    assert required_continuous_classes(34, 50, 75) == 14
    assert required_continuous_classes(3, 4, 75) == 0
    assert required_continuous_classes(2, 4, 75) == 4
    assert required_classes_to_attend(68, 75, 50) == 14
    assert required_classes_to_attend(90, 100, 10) is None

    result = predict_attendance(50, 2, 1, recent_trend=0)
    assert result['current_percentage'] == 50.0
    assert result['predicted_percentage'] == 50.0
    assert result['target_percentage'] == 75.0
    assert result['required_continuous_classes'] == 2
    assert result['risk'] == 'HIGH RISK'
    assert {'trend', 'recommended_action', 'subject_name'}.issubset(result)
    assert 'confidence' not in result


def test_api_blueprint_keeps_old_prefix_and_registers_v1_alias(dashboard_app):
    _seed(dashboard_app)
    client = _login(dashboard_app, 'hod-cse-dashboard')
    legacy = client.get('/api/students')
    versioned = client.get('/api/v1/students')
    assert legacy.status_code == versioned.status_code == 200
    assert legacy.get_json() == versioned.get_json()
    assert all(item['department'] == 'CSE' for item in versioned.get_json())
    assert client.get('/api/v1/dashboard/stats').status_code == 200
    endpoints = {rule.endpoint for rule in dashboard_app.url_map.iter_rules()}
    assert 'api.students' in endpoints and 'api_v1.students' in endpoints
