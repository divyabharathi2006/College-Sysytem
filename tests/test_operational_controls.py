from datetime import date, datetime, timedelta
from io import BytesIO

import pytest

from app import create_app, db
from models.academic import Department
from models.attendance import Attendance
from models.audit import SecurityEvent
from models.operational import AttendanceImportStage, SystemPolicy
from models.student import Student
from models.subject import Subject
from models.user import User
from models.user_session import UserSession
from security import hash_password


def _make_model(model, **attributes):
    instance = model()
    for name, value in attributes.items():
        setattr(instance, name, value)
    return instance


@pytest.fixture
def controls_app():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        department = _make_model(Department, code='CSE', name='Computer Science')
        db.session.add(department)
        db.session.flush()
        admin = _make_model(User, username='controls-admin', password_hash=hash_password('Admin-Password9!'), role='SUPER_ADMIN')
        other_admin = _make_model(User, username='controls-admin-two', password_hash=hash_password('Admin-Password8!'), role='SUPER_ADMIN')
        student_user = _make_model(User, username='controls-student', password_hash=hash_password('Student-Password9!'), role='STUDENT')
        db.session.add_all([admin, other_admin, student_user])
        db.session.flush()
        student = _make_model(Student,
            register_number='CSE-001', name='Test Student', email='student@example.test', phone='123',
            department='CSE', department_id=department.id, year='1', section='A', semester='1',
            user_id=student_user.id,
        )
        subject = _make_model(Subject, subject_code='CS101', subject_name='Testing', department='CSE', semester='1')
        db.session.add_all([student, subject])
        db.session.commit()
        app.config['TEST_IDS'] = {'student_id': student.id, 'subject_id': subject.id, 'admin_id': admin.id}
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(app, username, password):
    client = app.test_client()
    assert client.post('/login', data={'username': username, 'password': password}).status_code == 302
    return client


def _csv(rows, filename='attendance.csv'):
    content = 'register_number,subject_code,date,status\n' + rows
    return {'file': (BytesIO(content.encode('utf-8')), filename)}


def _set_policy(client, key, enabled, phrase):
    return client.post(f'/admin/security/controls/{key}', data={
        'enabled': str(enabled).lower(), 'confirmation_phrase': phrase,
    })


def test_controls_are_super_admin_only_audited_and_require_confirmation(controls_app):
    student = _login(controls_app, 'controls-student', 'Student-Password9!')
    denied = student.post('/admin/security/controls/maintenance_mode', data={
        'enabled': 'true', 'confirmation_phrase': 'ENABLE MAINTENANCE',
    })
    assert denied.status_code == 403

    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    assert admin.post('/admin/security/controls/maintenance_mode', data={'enabled': 'true'}).status_code == 400
    enabled = _set_policy(admin, 'maintenance_mode', True, 'ENABLE MAINTENANCE')
    assert enabled.status_code == 302
    assert admin.get('/admin/security').status_code == 200
    assert admin.get('/admin/dashboard').status_code == 503
    assert student.get('/api/students').status_code == 503
    assert _set_policy(admin, 'maintenance_mode', False, 'DISABLE MAINTENANCE').status_code == 302
    with controls_app.app_context():
        policy = db.session.get(SystemPolicy, 'maintenance_mode')
        assert policy is not None
        assert policy.value == 'false'
        assert policy.updated_by == controls_app.config['TEST_IDS']['admin_id']
        assert SecurityEvent.query.filter(SecurityEvent.action.like('%update_operational_policy%')).count() >= 3


def test_read_only_attendance_import_and_export_policies_reject_requests(controls_app):
    ids = controls_app.config['TEST_IDS']
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    assert _set_policy(admin, 'read_only_mode', True, 'ENABLE READ-ONLY').status_code == 302
    response = admin.post('/api/attendance', json={
        'student_id': ids['student_id'], 'subject_id': ids['subject_id'],
        'date': '2026-10-01', 'status': 'PRESENT',
    })
    assert response.status_code == 423
    assert admin.post('/admin/attendance', data={}).status_code == 423
    assert admin.get('/api/export/attendance.csv').status_code == 200
    assert _set_policy(admin, 'read_only_mode', False, 'DISABLE READ-ONLY').status_code == 302

    assert _set_policy(admin, 'attendance_modification_disabled', True, 'DISABLE ATTENDANCE MODIFICATION').status_code == 302
    assert admin.post('/api/attendance', json={
        'student_id': ids['student_id'], 'subject_id': ids['subject_id'],
        'date': '2026-10-02', 'status': 'PRESENT',
    }).status_code == 423
    assert admin.post('/admin/attendance', data={}).status_code == 423
    assert _set_policy(admin, 'attendance_modification_disabled', False, 'ENABLE ATTENDANCE MODIFICATION').status_code == 302

    assert _set_policy(admin, 'attendance_import_disabled', True, 'DISABLE ATTENDANCE IMPORT').status_code == 302
    assert admin.post('/api/import-attendance', data=_csv('CSE-001,CS101,2026-10-03,PRESENT'), content_type='multipart/form-data').status_code == 423
    assert admin.post('/admin/import-csv', data=_csv('CSE-001,CS101,2026-10-03,PRESENT'), content_type='multipart/form-data').status_code == 423
    assert _set_policy(admin, 'attendance_import_disabled', False, 'ENABLE ATTENDANCE IMPORT').status_code == 302

    assert _set_policy(admin, 'attendance_exports_disabled', True, 'DISABLE ATTENDANCE EXPORTS').status_code == 302
    assert admin.get('/api/export/attendance.csv').status_code == 423
    assert admin.get('/api/export/attendance.xlsx').status_code == 423
    assert _set_policy(admin, 'attendance_exports_disabled', False, 'ENABLE ATTENDANCE EXPORTS').status_code == 302
    with controls_app.app_context():
        assert Attendance.query.count() == 0


def test_force_revoke_all_sessions_marks_every_live_session_revoked(controls_app):
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    other = _login(controls_app, 'controls-student', 'Student-Password9!')
    with controls_app.app_context():
        before = UserSession.query.filter_by(revoked_at=None).count()
        assert before >= 2
    response = admin.post('/admin/security/controls/revoke-all-sessions', data={
        'confirmation_phrase': 'REVOKE ALL SESSIONS',
    })
    assert response.status_code == 302
    with controls_app.app_context():
        assert UserSession.query.filter_by(revoked_at=None).count() == 0
        assert SecurityEvent.query.filter(SecurityEvent.action.like('%revoke_all_sessions%')).one().status == 'success'
    assert other.get('/api/students').status_code == 302


def test_import_preview_is_non_mutating_then_owner_commit_is_atomic(controls_app):
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    response = admin.post('/admin/import-csv', data=_csv(
        'CSE-001,CS101,2026-10-01,PRESENT\nCSE-001,CS101,2026-10-02,ABSENT\n',
    ), content_type='multipart/form-data')
    assert response.status_code == 302
    preview_url = response.headers['Location']
    assert '/admin/import-csv/' in preview_url
    with controls_app.app_context():
        stage = AttendanceImportStage.query.one()
        stage_id = stage.id
        assert stage.owner_id == controls_app.config['TEST_IDS']['admin_id']
        assert stage.expires_at > datetime.utcnow()
        assert Attendance.query.count() == 0
    page = admin.get(preview_url)
    assert page.status_code == 200
    assert b'</strong> accepted rows' in page.data

    other_admin = _login(controls_app, 'controls-admin-two', 'Admin-Password8!')
    assert other_admin.post(f'/admin/import-csv/{stage_id}/commit', data={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    }).status_code == 404
    committed = admin.post(f'/admin/import-csv/{stage_id}/commit', data={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    })
    assert committed.status_code == 302
    with controls_app.app_context():
        records = Attendance.query.order_by(Attendance.date).all()
        assert len(records) == 2
        assert [record.status for record in records] == ['PRESENT', 'ABSENT']
        committed_stage = db.session.get(AttendanceImportStage, stage_id)
        assert committed_stage is not None and committed_stage.status == 'committed'
        assert SecurityEvent.query.filter(SecurityEvent.action.like('%import_csv%')).count() == 1
        commit_events = SecurityEvent.query.filter(SecurityEvent.action.like('%commit_import%')).all()
        assert len(commit_events) == 2
        assert {event.status for event in commit_events} == {'denied', 'success'}


def test_api_import_returns_staged_202_and_owner_bound_commit(controls_app):
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    response = admin.post('/api/import-attendance', data=_csv('CSE-001,CS101,2026-10-05,LATE'), content_type='multipart/form-data')
    assert response.status_code == 202
    payload = response.get_json()
    assert payload['accepted_count'] == 1 and payload['error_count'] == 0
    assert (payload['processed'], payload['successful'], payload['failed'], payload['duplicate']) == (1, 1, 0, 0)
    stage_id = payload['stage_id']
    with controls_app.app_context():
        assert Attendance.query.count() == 0
    other = _login(controls_app, 'controls-admin-two', 'Admin-Password8!')
    assert other.post(payload['commit_url'], json={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    }).status_code == 404
    committed = admin.post(payload['commit_url'], json={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    })
    assert committed.status_code == 201
    assert committed.get_json()['imported'] == 1
    with controls_app.app_context():
        assert Attendance.query.count() == 1


def test_import_preview_reports_duplicate_rows_and_never_partially_writes_on_conflict(controls_app):
    ids = controls_app.config['TEST_IDS']
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    response = admin.post('/api/import-attendance', data=_csv(
        'CSE-001,CS101,2026-10-06,PRESENT\nCSE-001,CS101,2026-10-06,ABSENT\n'
        'CSE-001,CS101,not-a-date,LATE\nCSE-001,CS101,2026-10-07,PRESENT\n',
    ), content_type='multipart/form-data')
    assert response.status_code == 202
    payload = response.get_json()
    assert payload['accepted_count'] == 2
    assert payload['error_count'] == 2
    assert payload['processed'] == 4 and payload['duplicate'] == 1 and payload['failed'] == 1
    stage_id = payload['stage_id']
    with controls_app.app_context():
        db.session.add(_make_model(Attendance,
            student_id=ids['student_id'], subject_id=ids['subject_id'],
            date=date(2026, 10, 7), status='EXCUSED',
        ))
        db.session.commit()
    result = admin.post(payload['commit_url'], json={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    })
    assert result.status_code == 409
    with controls_app.app_context():
        rows = Attendance.query.all()
        assert len(rows) == 1 and rows[0].status == 'EXCUSED'
        remaining_stage = db.session.get(AttendanceImportStage, stage_id)
        assert remaining_stage is not None and remaining_stage.status == 'preview'


def test_import_limits_expiry_and_cancellation(controls_app):
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    assert admin.post('/api/import-attendance', data=_csv('CSE-001,CS101,2026-10-10,PRESENT', 'bad.txt'), content_type='multipart/form-data').status_code == 400
    controls_app.config['MAX_ATTENDANCE_IMPORT_BYTES'] = 64
    too_large = admin.post('/api/import-attendance', data=_csv('CSE-001,CS101,2026-10-10,PRESENT'), content_type='multipart/form-data')
    assert too_large.status_code == 400
    controls_app.config['MAX_ATTENDANCE_IMPORT_BYTES'] = 2 * 1024 * 1024

    response = admin.post('/api/import-attendance', data=_csv('CSE-001,CS101,2026-10-10,PRESENT'), content_type='multipart/form-data')
    assert response.status_code == 202
    stage_id = response.get_json()['stage_id']
    with controls_app.app_context():
        stage = db.session.get(AttendanceImportStage, stage_id)
        assert stage is not None
        stage.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
    expired = admin.post(f'/api/import-attendance/{stage_id}/commit', json={
        'confirmation_phrase': f'COMMIT ATTENDANCE IMPORT {stage_id}',
    })
    assert expired.status_code == 410
    with controls_app.app_context():
        expired_stage = db.session.get(AttendanceImportStage, stage_id)
        assert expired_stage is not None and expired_stage.status == 'expired'
        assert Attendance.query.count() == 0

    second = admin.post('/api/import-attendance', data=_csv('CSE-001,CS101,2026-10-11,PRESENT'), content_type='multipart/form-data')
    second_stage_id = second.get_json()['stage_id']
    assert admin.delete(f'/api/import-attendance/{second_stage_id}').status_code == 200
    with controls_app.app_context():
        cancelled_stage = db.session.get(AttendanceImportStage, second_stage_id)
        assert cancelled_stage is not None and cancelled_stage.status == 'cancelled'
        assert Attendance.query.count() == 0


def test_account_disable_requires_phrase_and_blocks_last_self(controls_app):
    admin = _login(controls_app, 'controls-admin', 'Admin-Password9!')
    with controls_app.app_context():
        student = User.query.filter_by(username='controls-student').one()
        student_id = student.id
    assert admin.post(f'/admin/users/{student_id}/status', data={'is_enabled': 'false'}).status_code == 400
    admin_id = controls_app.config['TEST_IDS']['admin_id']
    assert admin.post(f'/admin/users/{admin_id}/status', data={
        'is_enabled': 'false', 'confirmation_phrase': f'DISABLE ACCOUNT {admin_id}',
    }).status_code == 400
    response = admin.post(f'/admin/users/{student_id}/status', data={
        'is_enabled': 'false', 'confirmation_phrase': f'DISABLE ACCOUNT {student_id}',
    })
    assert response.status_code == 302
    with controls_app.app_context():
        disabled_user = db.session.get(User, student_id)
        assert disabled_user is not None and disabled_user.is_enabled is False
