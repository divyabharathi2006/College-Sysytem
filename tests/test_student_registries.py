from datetime import date, datetime

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from models.attendance import Attendance
from models.registry import (
    AffiliatedCollegeRecord, AffiliatedStudentRecord, StudentRegistrationContact,
    UniversityStudentRecord,
)
from models.student import Student
from models.subject import Subject
from models.user import User


@pytest.fixture
def registry_app():
    app = create_app(testing=True)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def admin_client(registry_app):
    with registry_app.app_context():
        db.session.add(User(
            username='registry-admin',
            password_hash=generate_password_hash('Registry-Admin7!'),
            role='SUPER_ADMIN',
        ))
        db.session.commit()
    client = registry_app.test_client()
    assert client.post('/login', data={
        'username': 'registry-admin', 'password': 'Registry-Admin7!',
    }).status_code == 302
    return client


def college_payload(code, email, phone):
    return {
        'college_code': code, 'name': f'{code} College',
        'address': '1 College Road', 'contact_number': phone, 'email': email,
        'affiliation_details': 'Affiliated with Divyabharathi University',
        'staff_details': 'Teaching and administrative staff',
        'student_strength': '400', 'attendance_summary': 'Monthly summary',
        'infrastructure_details': 'Library and laboratories',
        'courses_offered': 'Computer Science\nMathematics',
        'has_antiragging_committee': 'on',
        'counseling_services': 'On-campus counseling',
        'scholarship_details': 'Merit and need-based scholarships',
        'extracurricular_activities': 'Sports and clubs',
    }


def student_payload(*, kind='university', roll='R001', username='student-r001', email='student@example.test', phone='+1 202-555-0101', college_code=''):
    return {
        'student_type': kind, 'college_code': college_code, 'roll_number': roll,
        'username': username, 'password': 'Student-Temporary7!',
        'name': 'Example Student', 'email': email, 'phone': phone,
        'gender': 'Prefer not to say', 'date_of_birth': '2005-04-05',
        'father_name': 'Parent One', 'mother_name': 'Parent Two',
        'address': '1 University Way', 'community': 'Not specified',
        'identification_type': 'University photo ID',
        'identification_verified': 'on', 'is_differently_abled': 'on',
        'disability_details': 'Accessible seating requested',
        'department': 'CSE', 'year': '2', 'section': 'A', 'semester': '4',
    }


def college_account_payload(username, *, role='AFF_REGISTRAR', display_name='College Staff'):
    return {
        'username': username,
        'display_name': display_name,
        'role': role,
        'password': 'College-Staff9!',
    }


def set_up_college_staff(client, username):
    login = client.post('/login', data={
        'username': username, 'password': 'College-Staff9!',
    })
    assert login.status_code == 302
    setup = client.post('/student/password-setup', data={
        'new_password': 'College-Staff-New9!',
        'password_confirmation': 'College-Staff-New9!',
    })
    assert setup.status_code == 302
    return client


def test_colleges_get_stable_prefixes_and_keep_requested_details(admin_client, registry_app):
    first = admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    second = admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-B', 'college-b@example.test', '+1 202-555-0112'),
    )
    assert first.status_code == second.status_code == 302
    with registry_app.app_context():
        colleges = AffiliatedCollegeRecord.query.order_by(AffiliatedCollegeRecord.id).all()
        assert [college.registration_prefix for college in colleges] == ['DBU301', 'DBU302']
        assert colleges[0].has_antiragging_committee is True
        assert colleges[0].student_strength == 400
        assert colleges[0].scholarship_details == 'Merit and need-based scholarships'


def test_admin_dashboard_has_direct_student_and_college_account_creation_actions(admin_client):
    dashboard = admin_client.get('/admin/dashboard')
    assert dashboard.status_code == 200
    assert b'Create user accounts' in dashboard.data
    assert b'Create student login' in dashboard.data
    assert b'href="/admin/students"' in dashboard.data
    assert b'Create affiliated-college login' in dashboard.data
    assert b'href="/admin/registries/colleges"' in dashboard.data


def test_admin_registry_pages_show_create_forms_when_ready(admin_client):
    college_page = admin_client.get('/admin/registries/colleges')
    student_page = admin_client.get('/admin/students')
    assert college_page.status_code == student_page.status_code == 200
    assert b'Create affiliated college' in college_page.data
    assert b'name="college_code"' in college_page.data
    assert b'Create a university or affiliated-college student login' in student_page.data
    assert b'name="student_type"' in student_page.data
    assert b'>Create registration<' in student_page.data


def test_registry_pages_show_admin_initialization_action_when_stores_are_missing(
    admin_client, registry_app,
):
    with registry_app.app_context():
        db.drop_all(bind_key=[
            'university_students', 'affiliated_students', 'affiliated_colleges',
        ])
    college_page = admin_client.get('/admin/registries/colleges')
    student_page = admin_client.get('/admin/students')
    assert college_page.status_code == 503
    assert b'Initialize registries to create college records' in college_page.data
    assert b'name="csrf_token"' in college_page.data
    assert student_page.status_code == 200
    assert b'Initialize registries to create student records' in student_page.data
    assert b'name="csrf_token"' in student_page.data


def test_college_staff_can_only_register_students_for_their_assigned_college(
    admin_client, registry_app,
):
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-B', 'college-b@example.test', '+1 202-555-0112'),
    )
    with registry_app.app_context():
        college_a = AffiliatedCollegeRecord.query.filter_by(college_code='COLLEGE-A').one()
        college_a_id = college_a.id
    account_response = admin_client.post(
        f'/admin/registries/colleges/{college_a_id}/accounts',
        data=college_account_payload(
            'college-a-staff', role='AFF_REGISTRAR', display_name='A Registrar',
        ),
    )
    assert account_response.status_code == 302

    staff_client = registry_app.test_client()
    set_up_college_staff(staff_client, 'college-a-staff')
    assert staff_client.get('/affiliated/portal').status_code == 200

    portal = staff_client.get('/affiliated/portal')
    assert portal.status_code == 200
    assert b'COLLEGE-A College' in portal.data
    assert b'COLLEGE-B College' not in portal.data
    assert b'name="roll_number"' in portal.data
    assert b'name="date_of_birth"' in portal.data
    assert b'name="identification_type"' in portal.data
    assert b'Student roster' in portal.data
    assert b'A Registrar' not in portal.data
    assert b'Create a student registration' in portal.data
    assert b'value="DBU3011"' in portal.data
    registration = staff_client.post(
        '/admin/registries/students',
        data=student_payload(
            kind='university', college_code='COLLEGE-B',
            username='college-a-student-r001',
            email='college-a-student@example.test',
            phone='+1 202-555-0121',
        ),
    )
    assert registration.status_code == 302
    assert registration.headers['Location'].endswith('/affiliated/portal')
    with registry_app.app_context():
        account = User.query.filter_by(username='college-a-staff').one()
        assert account.display_name == 'A Registrar'
        assert account.role == 'AFF_REGISTRAR'
        assert account.affiliated_college_code == 'COLLEGE-A'
        assert account.must_change_password is False
        assert Student.query.filter_by(register_number='DBU3011').one()
        assert not Student.query.filter_by(register_number='DBU3021').first()
        affiliated_record = AffiliatedStudentRecord.query.filter_by(
            register_number='DBU3011',
        ).one()
        assert affiliated_record.college_code == 'COLLEGE-A'
        assert StudentRegistrationContact.query.count() == 1
        student_id = Student.query.filter_by(register_number='DBU3011').one().id
    denied_edit = staff_client.post(
        f'/admin/registries/students/{student_id}/details',
        data={'gender': 'Prefer not to say'},
    )
    assert denied_edit.status_code == 403


def test_college_accounts_are_scoped_and_deactivated_with_college(admin_client, registry_app):
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    with registry_app.app_context():
        college = AffiliatedCollegeRecord.query.filter_by(college_code='COLLEGE-A').one()
        college_id = college.id
    account_response = admin_client.post(
        f'/admin/registries/colleges/{college_id}/accounts',
        data=college_account_payload(
            'college-a-staff', role='AFF_COLLEGE_ADMIN', display_name='College Admin',
        ),
    )
    assert account_response.status_code == 302
    college_management = admin_client.get('/admin/registries/colleges')
    assert college_management.status_code == 200
    assert b'College login accounts' in college_management.data
    assert b'college-a-staff' in college_management.data
    assert b'College Admin' in college_management.data
    assert b'name="display_name"' in college_management.data
    assert b'name="role"' in college_management.data

    staff_client = registry_app.test_client()
    assert staff_client.get('/affiliated/portal').status_code == 302
    assert staff_client.post('/login', data={
        'username': 'college-a-staff', 'password': 'College-Staff9!',
    }).status_code == 302
    with registry_app.app_context():
        staff_id = User.query.filter_by(username='college-a-staff').one().id
    update = admin_client.post(
        f'/admin/registries/colleges/{college_id}/accounts/{staff_id}/edit',
        data={
            'display_name': 'Updated College Admin',
            'role': 'AFF_REGISTRAR',
        },
    )
    assert update.status_code == 302
    with registry_app.app_context():
        updated_account = db.session.get(User, staff_id)
        assert updated_account.display_name == 'Updated College Admin'
        assert updated_account.role == 'AFF_REGISTRAR'
    disable = admin_client.post(
        f'/admin/registries/colleges/{college_id}/accounts/{staff_id}/disable',
    )
    assert disable.status_code == 302
    with registry_app.app_context():
        assert db.session.get(User, staff_id).is_enabled is False
    assert staff_client.get('/affiliated/portal').status_code == 302

    second_account = admin_client.post(
        f'/admin/registries/colleges/{college_id}/accounts',
        data=college_account_payload(
            'college-a-staff-two', role='AFF_ATTEND_OFFICER',
            display_name='Attendance Officer',
        ),
    )
    assert second_account.status_code == 302
    deactivate = admin_client.post(
        f'/admin/registries/colleges/{college_id}/delete',
    )
    assert deactivate.status_code == 302
    with registry_app.app_context():
        assert not User.query.filter_by(
            username='college-a-staff-two',
        ).one().is_enabled
    assert staff_client.post('/login', data={
        'username': 'college-a-staff-two', 'password': 'College-Staff9!',
    }).status_code == 200


def test_college_admin_can_edit_only_own_student_and_generic_role_edit_is_blocked(
    admin_client, registry_app,
):
    for code, email, phone in (
        ('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
        ('COLLEGE-B', 'college-b@example.test', '+1 202-555-0112'),
    ):
        admin_client.post('/admin/registries/colleges', data=college_payload(code, email, phone))
    account_ids = {}
    for code in ('COLLEGE-A', 'COLLEGE-B'):
        with registry_app.app_context():
            college_id = AffiliatedCollegeRecord.query.filter_by(college_code=code).one().id
        username = f'{code.lower()}-admin'
        admin_client.post(
            f'/admin/registries/colleges/{college_id}/accounts',
            data=college_account_payload(
                username, role='AFF_COLLEGE_ADMIN', display_name=f'{code} Admin',
            ),
        )
        with registry_app.app_context():
            account_ids[code] = User.query.filter_by(username=username).one().id
    first = admin_client.post(
        '/admin/registries/students',
        data=student_payload(
            kind='affiliated', username='student-a', email='student-a@example.test',
            phone='+1 202-555-0121', college_code='COLLEGE-A',
        ),
    )
    second = admin_client.post(
        '/admin/registries/students',
        data=student_payload(
            kind='affiliated', roll='R002', username='student-b',
            email='student-b@example.test', phone='+1 202-555-0122',
            college_code='COLLEGE-B',
        ),
    )
    assert first.status_code == second.status_code == 302
    with registry_app.app_context():
        student_a = Student.query.filter_by(register_number='DBU3011').one()
        student_b = Student.query.filter_by(register_number='DBU3021').one()
        student_a_id, student_b_id = student_a.id, student_b.id
    staff_client = set_up_college_staff(
        registry_app.test_client(), 'college-a-admin',
    )
    edit_data = {
        'gender': 'Prefer not to say',
        'date_of_birth': '2005-04-05',
        'father_name': 'Updated Parent',
        'mother_name': 'Parent Two',
        'community': 'Not specified',
        'identification_type': 'University photo ID',
        'address': '2 University Way',
        'disability_details': '',
    }
    own_edit = staff_client.post(
        f'/admin/registries/students/{student_a_id}/details', data=edit_data,
    )
    other_college_edit = staff_client.post(
        f'/admin/registries/students/{student_b_id}/details', data=edit_data,
    )
    assert own_edit.status_code == 302
    assert own_edit.headers['Location'].endswith('/affiliated/portal')
    assert other_college_edit.status_code == 404
    generic_role_change = admin_client.post(
        f'/admin/users/{account_ids["COLLEGE-A"]}/edit',
        data={'username': 'college-a-admin', 'role': 'STUDENT'},
    )
    assert generic_role_change.status_code == 302
    with registry_app.app_context():
        assert db.session.get(User, account_ids['COLLEGE-A']).role == 'AFF_COLLEGE_ADMIN'
        record = AffiliatedStudentRecord.query.filter_by(
            register_number='DBU3011',
        ).one()
        assert record.address == '2 University Way'


def test_attendance_officer_gets_only_scoped_read_only_attendance_summary(
    admin_client, registry_app,
):
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-B', 'college-b@example.test', '+1 202-555-0112'),
    )
    with registry_app.app_context():
        college_a_id = AffiliatedCollegeRecord.query.filter_by(
            college_code='COLLEGE-A',
        ).one().id
    admin_client.post(
        f'/admin/registries/colleges/{college_a_id}/accounts',
        data=college_account_payload(
            'college-a-officer', role='AFF_ATTEND_OFFICER',
            display_name='Attendance Officer',
        ),
    )
    admin_client.post(
        '/admin/registries/students',
        data=student_payload(
            kind='affiliated', username='student-a', email='student-a@example.test',
            phone='+1 202-555-0121', college_code='COLLEGE-A',
        ),
    )
    admin_client.post(
        '/admin/registries/students',
        data=student_payload(
            kind='affiliated', roll='R002', username='student-b',
            email='student-b@example.test', phone='+1 202-555-0122',
            college_code='COLLEGE-B',
        ),
    )
    with registry_app.app_context():
        student_a = Student.query.filter_by(register_number='DBU3011').one()
        student_b = Student.query.filter_by(register_number='DBU3021').one()
        subject = Subject(
            subject_code='CSE101', subject_name='Foundations',
            department='CSE', semester='1',
        )
        db.session.add(subject)
        db.session.flush()
        db.session.add_all([
            Attendance(
                student_id=student_a.id, subject_id=subject.id,
                date=date(2026, 10, 1), status='PRESENT',
            ),
            Attendance(
                student_id=student_a.id, subject_id=subject.id,
                date=date(2026, 10, 2), status='ABSENT',
            ),
            Attendance(
                student_id=student_b.id, subject_id=subject.id,
                date=date(2026, 10, 1), status='PRESENT',
            ),
        ])
        db.session.commit()
    officer = set_up_college_staff(
        registry_app.test_client(), 'college-a-officer',
    )
    portal = officer.get('/affiliated/portal')
    assert portal.status_code == 200
    assert b'DBU3011' in portal.data
    assert b'DBU3021' not in portal.data
    assert b'1 / 2 (50.0%)' in portal.data
    assert b'College attendance summary' in portal.data
    assert b'student-a@example.test' not in portal.data
    assert b'+1 202-555-0121' not in portal.data
    assert b'Create a student registration' not in portal.data
    denied_registration = officer.post(
        '/admin/registries/students',
        data=student_payload(
            kind='affiliated', username='officer-created',
            email='officer-created@example.test', phone='+1 202-555-0123',
            college_code='COLLEGE-A',
        ),
    )
    assert denied_registration.status_code == 403
    denied_edit = officer.post(
        '/admin/registries/students/1/details',
        data={'gender': 'Prefer not to say'},
    )
    assert denied_edit.status_code == 403


def test_non_college_user_cannot_open_affiliated_portal_or_register(admin_client, registry_app):
    with registry_app.app_context():
        db.session.add(User(
            username='regular-student',
            password_hash=generate_password_hash('Regular-Student9!'),
            role='STUDENT',
        ))
        db.session.commit()
    student_client = registry_app.test_client()
    assert student_client.post('/login', data={
        'username': 'regular-student', 'password': 'Regular-Student9!',
    }).status_code == 302
    assert student_client.get('/affiliated/portal').status_code == 403
    assert student_client.post('/admin/registries/initialize').status_code == 403
    denied = student_client.post(
        '/admin/registries/students', data=student_payload(),
    )
    assert denied.status_code == 403


def test_legacy_affiliated_college_role_keeps_portal_access(admin_client, registry_app):
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    with registry_app.app_context():
        db.session.add(User(
            username='legacy-college-staff',
            password_hash=generate_password_hash('Legacy-College9!'),
            role='AFFILIATED_COLLEGE',
            affiliated_college_code='COLLEGE-A',
        ))
        db.session.commit()
    client = registry_app.test_client()
    login = client.post('/login', data={
        'username': 'legacy-college-staff', 'password': 'Legacy-College9!',
    })
    assert login.status_code == 302
    assert login.headers['Location'].endswith('/affiliated/portal')
    portal = client.get('/affiliated/portal')
    assert portal.status_code == 200
    assert b'Student roster' in portal.data
    assert b'Create a student registration' in portal.data


def test_university_and_affiliated_student_registrations_use_separate_stores(admin_client, registry_app):
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    university = admin_client.post('/admin/registries/students', data=student_payload())
    affiliate = admin_client.post(
        '/admin/registries/students',
        data=student_payload(
            kind='affiliated', roll='R001', username='affiliate-r001',
            email='affiliate@example.test', phone='+1 202-555-0102',
            college_code='COLLEGE-A',
        ),
    )
    assert university.status_code == affiliate.status_code == 302
    with registry_app.app_context():
        university_profile = Student.query.filter_by(register_number='DBU3001').one()
        affiliated_profile = Student.query.filter_by(register_number='DBU3011').one()
        assert university_profile.roll_number == 'R001'
        assert affiliated_profile.roll_number == 'R001'
        assert UniversityStudentRecord.query.filter_by(
            user_id=university_profile.user_id,
        ).one().identification_verified is True
        affiliated_detail = AffiliatedStudentRecord.query.filter_by(
            user_id=affiliated_profile.user_id,
        ).one()
        assert affiliated_detail.college_code == 'COLLEGE-A'
        assert affiliated_detail.registration_prefix == 'DBU301'
        assert affiliated_detail.is_differently_abled is True
        assert StudentRegistrationContact.query.count() == 2
        assert affiliated_profile.section == 'A'


def test_registration_numbers_increment_independently_for_each_prefix_and_preview_next_id(
    admin_client, registry_app,
):
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-A', 'college-a@example.test', '+1 202-555-0111'),
    )
    admin_client.post(
        '/admin/registries/colleges',
        data=college_payload('COLLEGE-B', 'college-b@example.test', '+1 202-555-0112'),
    )
    assert b'value="DBU3001"' in admin_client.get('/admin/students').data
    first_university = admin_client.post(
        '/admin/registries/students', data=student_payload(),
    )
    assert first_university.status_code == 302
    assert b'value="DBU3002"' in admin_client.get('/admin/students').data

    responses = [
        admin_client.post(
            '/admin/registries/students',
            data=student_payload(
                roll='R002', username='university-second',
                email='university-second@example.test', phone='+1 202-555-0102',
            ),
        ),
        admin_client.post(
            '/admin/registries/students',
            data=student_payload(
                kind='affiliated', roll='A001', username='college-a-first',
                email='college-a-first@example.test', phone='+1 202-555-0103',
                college_code='COLLEGE-A',
            ),
        ),
        admin_client.post(
            '/admin/registries/students',
            data=student_payload(
                kind='affiliated', roll='A002', username='college-a-second',
                email='college-a-second@example.test', phone='+1 202-555-0104',
                college_code='COLLEGE-A',
            ),
        ),
        admin_client.post(
            '/admin/registries/students',
            data=student_payload(
                kind='affiliated', roll='B001', username='college-b-first',
                email='college-b-first@example.test', phone='+1 202-555-0105',
                college_code='COLLEGE-B',
            ),
        ),
    ]
    assert all(response.status_code == 302 for response in responses)
    with registry_app.app_context():
        assert {
            register_number
            for (register_number,) in Student.query.with_entities(Student.register_number).all()
        } == {
            'DBU3001', 'DBU3002', 'DBU3011', 'DBU3012', 'DBU3021',
        }
        assert UniversityStudentRecord.query.filter_by(
            register_number='DBU3002',
        ).one().roll_number == 'R002'
        assert AffiliatedStudentRecord.query.filter_by(
            register_number='DBU3012',
        ).one().roll_number == 'A002'


def test_email_and_phone_cannot_be_reused_for_student_registration(admin_client, registry_app):
    admin_client.post('/admin/registries/students', data=student_payload())
    duplicate_email = admin_client.post(
        '/admin/registries/students',
        data=student_payload(roll='R002', username='student-r002', phone='+1 202-555-0102'),
    )
    duplicate_phone = admin_client.post(
        '/admin/registries/students',
        data=student_payload(roll='R003', username='student-r003', email='other@example.test'),
    )
    invalid_gender_payload = student_payload(
        roll='R004', username='student-r004', email='gender@example.test',
        phone='+1 202-555-0104',
    )
    invalid_gender_payload['gender'] = 'unapproved'
    invalid_gender = admin_client.post(
        '/admin/registries/students', data=invalid_gender_payload,
    )
    assert duplicate_email.status_code == duplicate_phone.status_code == invalid_gender.status_code == 302
    with registry_app.app_context():
        assert Student.query.count() == 1
        assert StudentRegistrationContact.query.count() == 1


def test_contact_verification_activates_account_only_once(admin_client, registry_app, monkeypatch):
    import routes.registries as registries

    delivered = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            delivered.append(message)

    monkeypatch.setattr(registries.smtplib, 'SMTP', FakeSMTP)
    registry_app.config.update(
        STUDENT_EMAIL_VERIFICATION_REQUIRED=True,
        SMTP_HOST='smtp.example.test', SMTP_FROM='registrar@example.test',
        PUBLIC_BASE_URL='https://university.example.test',
    )
    created = admin_client.post(
        '/admin/registries/students', data=student_payload(),
    )
    assert created.status_code == 302
    assert len(delivered) == 1
    token = delivered[0].get_content().strip().rsplit('#', 1)[1]
    with registry_app.app_context():
        user = User.query.filter_by(username='student-r001').one()
        user_id = user.id
        assert user.is_enabled is False
        assert UniversityStudentRecord.query.filter_by(user_id=user.id).one().email_verified_at is None

    assert admin_client.get('/student/verify-email').status_code == 200
    verified = admin_client.post('/student/verify-email', data={'token': token})
    assert verified.status_code == 302
    with registry_app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_enabled is True
        record = UniversityStudentRecord.query.filter_by(user_id=user_id).one()
        assert isinstance(record.email_verified_at, datetime)
    reused = admin_client.post('/student/verify-email', data={'token': token})
    assert reused.status_code == 400
