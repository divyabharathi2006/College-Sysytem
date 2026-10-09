import json
import re
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from models.audit import SecurityEvent
from models.user import User
from models.user_session import PasswordResetToken, UserSession
from security import hash_password, keyed_digest, verify_password_and_update


@pytest.fixture
def security_app():
    app = create_app(testing=True)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_argon2id_new_hash_and_legacy_werkzeug_upgrade(security_app):
    legacy_hash = generate_password_hash('legacy-pass')
    verified, upgraded = verify_password_and_update(legacy_hash, 'legacy-pass')
    assert verified
    assert upgraded.startswith('$argon2id$')
    assert verify_password_and_update(upgraded, 'legacy-pass') == (True, None)
    assert verify_password_and_update(legacy_hash, 'incorrect') == (False, None)
    assert hash_password('new-pass').startswith('$argon2id$')

    with security_app.app_context():
        db.session.add(User(username='legacy-login', password_hash=legacy_hash, role='student'))
        db.session.commit()
    response = security_app.test_client().post('/login', data={
        'username': 'legacy-login', 'password': 'legacy-pass',
    })
    assert response.status_code == 302
    with security_app.app_context():
        assert db.session.get(User, 1).password_hash.startswith('$argon2id$')
        assert SecurityEvent.query.filter_by(action='auth.login', status='success').count() == 1


def test_login_throttle_returns_generic_429(security_app):
    client = security_app.test_client()
    responses = [client.post('/login', data={'username': 'throttle-me', 'password': 'bad'}) for _ in range(6)]
    assert [response.status_code for response in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429
    assert responses[5].get_data(as_text=True) == 'Too many requests. Please try again later.'
    assert 'Traceback' not in responses[5].get_data(as_text=True)
    with security_app.app_context():
        assert SecurityEvent.query.filter_by(action='auth.login', status='failure').count() == 5


def test_security_headers_and_hsts_follow_request_scheme(security_app):
    client = security_app.test_client()
    plain = client.get('/login')
    assert plain.headers['X-Content-Type-Options'] == 'nosniff'
    assert plain.headers['X-Frame-Options'] == 'DENY'
    assert plain.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
    assert plain.headers['Permissions-Policy'] == 'camera=(), microphone=(), geolocation=()'
    assert "frame-ancestors 'none'" in plain.headers['Content-Security-Policy']
    assert "'unsafe-inline'" in plain.headers['Content-Security-Policy']
    assert 'Strict-Transport-Security' not in plain.headers

    secure = client.get('/login', base_url='https://localhost')
    assert secure.headers['Strict-Transport-Security'] == 'max-age=31536000'


def test_session_cookie_defaults_and_finite_lifetime(security_app):
    with security_app.app_context():
        db.session.add(User(username='cookie-user', password_hash=hash_password('cookie-pass'), role='student'))
        db.session.commit()
    response = security_app.test_client().post('/login', data={
        'username': 'cookie-user', 'password': 'cookie-pass',
    })
    cookie = response.headers['Set-Cookie'].lower()
    assert 'httponly' in cookie
    assert 'samesite=lax' in cookie
    assert 'secure' not in cookie
    assert security_app.config['PERMANENT_SESSION_LIFETIME'].total_seconds() == 8 * 60 * 60


def test_audit_fields_are_redacted_and_security_center_is_super_admin_only(security_app):
    with security_app.app_context():
        administrator = User(username='security-admin', password_hash=hash_password('admin-pass'), role='admin')
        account = User(username='managed-user', password_hash=hash_password('old-pass'), role='student')
        student = User(username='regular-student', password_hash=hash_password('student-pass'), role='student')
        db.session.add_all([administrator, account, student])
        db.session.commit()
        account_id = account.id

    admin_client = security_app.test_client()
    assert admin_client.post('/login', data={'username': 'security-admin', 'password': 'admin-pass'}).status_code == 302
    account_client = security_app.test_client()
    assert account_client.post('/login', data={'username': 'managed-user', 'password': 'old-pass'}).status_code == 302
    replacement_password = 'New-Secret-Password7!'
    assert admin_client.post(f'/admin/users/{account_id}/password', data={
        'password': replacement_password,
    }).status_code == 302
    assert account_client.get('/admin/security').status_code == 302
    with security_app.app_context():
        managed_user = db.session.get(User, account_id)
        assert verify_password_and_update(managed_user.password_hash, replacement_password) == (True, None)
        assert UserSession.query.filter_by(user_id=account_id, revoked_at=None).count() == 0
        event = SecurityEvent.query.filter(SecurityEvent.action.like('admin.reset_user_password:%')).one()
        serialized = json.dumps({
            'before': event.before_data, 'after': event.after_data,
            'reason': event.reason, 'agent': event.user_agent,
        })
        assert replacement_password not in serialized
        assert '$argon2id$' not in serialized
        assert 'password' not in serialized.lower()
        assert event.actor_role in {'admin', 'SUPER_ADMIN'}
        assert len(event.ip_hash) == 64

    student_client = security_app.test_client()
    assert student_client.post('/login', data={
        'username': 'regular-student', 'password': 'student-pass',
    }).status_code == 302
    assert student_client.get('/admin/security').status_code == 403
    assert admin_client.get('/admin/security').status_code == 200


def test_security_events_are_append_only_at_the_orm_layer(security_app):
    from audit import record_security_event

    with security_app.test_request_context('/', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        event = record_security_event(
            action='test.event', status='success', reason='test',
            actor_role='anonymous', resource_type='test', response_status=200,
        )
        with pytest.raises(ValueError, match='append-only'):
            event.reason = 'changed'
            db.session.commit()
        db.session.rollback()
        persisted = db.session.get(SecurityEvent, event.id)
        assert persisted.reason == 'test'


def test_csrf_remains_enabled_outside_testing(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'test-csrf-secret')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{(tmp_path / "csrf.sqlite").as_posix()}')
    app = create_app()
    assert app.config['WTF_CSRF_ENABLED'] is True
    response = app.test_client().post('/login', data={'username': 'x', 'password': 'y'})
    assert response.status_code == 400
    assert 'CSRF' not in response.get_data(as_text=True)


def test_safe_http_errors_do_not_expose_details_and_log_server_failure(security_app, caplog):
    @security_app.get('/test-only-error')
    def raise_error():
        raise RuntimeError('private traceback detail')

    security_app.config['PROPAGATE_EXCEPTIONS'] = False
    response = security_app.test_client().get('/test-only-error')
    assert response.status_code == 500
    assert response.get_data(as_text=True) == 'An unexpected server error occurred.'
    assert 'private traceback detail' not in response.get_data(as_text=True)
    assert 'Request failed with HTTP 500.' in caplog.text
    assert 'private traceback detail' in caplog.text
    missing = security_app.test_client().get('/not-a-route')
    assert missing.status_code == 404
    assert 'Traceback' not in missing.get_data(as_text=True)


def test_login_requires_a_live_server_session_and_records_last_login(security_app):
    with security_app.app_context():
        user = User(username='session-admin', password_hash=hash_password('Session-Password7!'), role='SUPER_ADMIN')
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        assert user.last_login_at is None

    client = security_app.test_client()
    assert client.post('/login', data={
        'username': 'session-admin', 'password': 'Session-Password7!',
    }).status_code == 302
    with client.session_transaction() as cookie_session:
        session_id = cookie_session['auth_session_id']
    with security_app.app_context():
        user = db.session.get(User, user_id)
        auth_session = UserSession.query.filter_by(user_id=user_id).one()
        assert user.last_login_at is not None
        assert user.last_login_at <= datetime.utcnow()
        assert auth_session.session_id_hash == keyed_digest('auth-session', session_id)
        assert auth_session.session_id_hash != session_id
        assert auth_session.expires_at > datetime.utcnow()
    assert client.get('/admin/security').status_code == 200

    with security_app.app_context():
        auth_session = UserSession.query.filter_by(user_id=user_id).one()
        auth_session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
    expired = client.get('/admin/security')
    assert expired.status_code == 302
    assert '/login' in expired.headers['Location']

    assert client.post('/login', data={
        'username': 'session-admin', 'password': 'Session-Password7!',
    }).status_code == 302

    with client.session_transaction() as cookie_session:
        cookie_session.pop('auth_session_id')
    rejected = client.get('/admin/security')
    assert rejected.status_code == 302
    assert '/login' in rejected.headers['Location']


def test_idle_timeout_slides_on_activity_and_expires_after_inactivity(security_app):
    security_app.config['SESSION_IDLE_TIMEOUT_MINUTES'] = 5
    with security_app.app_context():
        user = User(username='idle-user', password_hash=hash_password('Idle-Password7!'), role='SUPER_ADMIN')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client = security_app.test_client()
    assert client.post('/login', data={
        'username': 'idle-user', 'password': 'Idle-Password7!',
    }).status_code == 302
    with security_app.app_context():
        auth_session = UserSession.query.filter_by(user_id=user_id).one()
        first_expiry = auth_session.expires_at
        assert first_expiry <= datetime.utcnow() + timedelta(minutes=5)

    assert client.get('/admin/security').status_code == 200
    with security_app.app_context():
        auth_session = UserSession.query.filter_by(user_id=user_id).one()
        assert auth_session.expires_at > first_expiry
        auth_session.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

    expired = client.get('/admin/security')
    assert expired.status_code == 302
    assert '/login' in expired.headers['Location']


def test_logout_prevents_back_navigation_to_cached_authenticated_pages(security_app):
    with security_app.app_context():
        user = User(username='back-user', password_hash=hash_password('Back-Password7!'), role='SUPER_ADMIN')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client = security_app.test_client()
    assert client.post('/login', data={
        'username': 'back-user', 'password': 'Back-Password7!',
    }).status_code == 302
    protected = client.get('/admin/dashboard')
    assert protected.status_code == 200
    assert 'no-store' in protected.headers['Cache-Control']
    assert protected.headers['Pragma'] == 'no-cache'

    logged_out = client.post('/logout')
    assert logged_out.status_code == 302
    assert logged_out.headers['Location'].endswith('/login')
    assert 'no-store' in logged_out.headers['Cache-Control']
    with security_app.app_context():
        assert UserSession.query.filter_by(user_id=user_id).one().revoked_at is not None

    back_navigation = client.get('/admin/dashboard')
    assert back_navigation.status_code == 302
    assert '/login' in back_navigation.headers['Location']


def test_disabled_and_revoked_sessions_stay_invalid_after_reenable(security_app):
    with security_app.app_context():
        administrator = User(
            username='status-admin', password_hash=hash_password('Status-Password7!'), role='SUPER_ADMIN',
        )
        account = User(username='status-user', password_hash=hash_password('Status-Password8!'), role='STUDENT')
        db.session.add_all([administrator, account])
        db.session.commit()
        account_id = account.id

    admin_client = security_app.test_client()
    account_client = security_app.test_client()
    assert admin_client.post('/login', data={
        'username': 'status-admin', 'password': 'Status-Password7!',
    }).status_code == 302
    assert account_client.post('/login', data={
        'username': 'status-user', 'password': 'Status-Password8!',
    }).status_code == 302
    assert account_client.get('/admin/security').status_code == 403

    disabled = admin_client.post(f'/admin/users/{account_id}/status', data={
        'is_enabled': 'false', 'confirmation_phrase': f'DISABLE ACCOUNT {account_id}',
    })
    assert disabled.status_code == 302
    assert account_client.get('/admin/security').status_code == 302
    disabled_login = security_app.test_client().post('/login', data={
        'username': 'status-user', 'password': 'Status-Password8!',
    })
    assert disabled_login.status_code == 200
    assert b'Invalid username or password.' in disabled_login.data
    with security_app.app_context():
        account = db.session.get(User, account_id)
        assert not account.is_enabled
        assert UserSession.query.filter_by(user_id=account_id, revoked_at=None).count() == 0

    enabled = admin_client.post(f'/admin/users/{account_id}/status', data={'is_enabled': 'true'})
    assert enabled.status_code == 302
    assert account_client.get('/admin/security').status_code == 302
    with security_app.app_context():
        assert db.session.get(User, account_id).is_enabled
    fresh_client = security_app.test_client()
    assert fresh_client.post('/login', data={
        'username': 'status-user', 'password': 'Status-Password8!',
    }).status_code == 302
    assert fresh_client.get('/admin/security').status_code == 403

    # The Super Admin force-logout action also invalidates sessions immediately.
    assert admin_client.post(f'/admin/users/{account_id}/sessions/revoke').status_code == 302
    assert fresh_client.get('/admin/security').status_code == 302


def test_logout_is_post_only_and_requires_csrf(security_app):
    with security_app.app_context():
        db.session.add(User(
            username='csrf-logout-admin', password_hash=hash_password('Logout-Password7!'),
            role='SUPER_ADMIN',
        ))
        db.session.commit()
    security_app.config['WTF_CSRF_ENABLED'] = True
    client = security_app.test_client()
    login_page = client.get('/login')
    csrf_token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
    assert client.post('/login', data={
        'username': 'csrf-logout-admin', 'password': 'Logout-Password7!', 'csrf_token': csrf_token,
    }).status_code == 302
    dashboard = client.get('/admin/dashboard')
    csrf_token = re.search(rb'name="csrf_token" value="([^"]+)"', dashboard.data).group(1).decode()

    assert client.get('/logout').status_code == 405
    assert client.post('/logout').status_code == 400
    assert client.get('/admin/security').status_code == 200
    assert client.post('/logout', data={'csrf_token': csrf_token}).status_code == 302
    assert client.get('/admin/security').status_code == 302


def test_password_reset_is_generic_hashed_expiring_single_use_and_revokes_sessions(security_app, monkeypatch):
    from routes import auth

    generated_tokens = iter(('expired-reset-token', 'valid-reset-token'))
    issued_tokens = []

    def issue_test_token(_length):
        value = next(generated_tokens)
        issued_tokens.append(value)
        return value

    security_app.config.update(SMTP_HOST='', SMTP_FROM='')
    with security_app.app_context():
        user = User(username='reset-user', password_hash=hash_password('Original-Password7!'), role='STUDENT')
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        original_password_hash = user.password_hash

    authenticated = security_app.test_client()
    assert authenticated.post('/login', data={
        'username': 'reset-user', 'password': 'Original-Password7!',
    }).status_code == 302
    monkeypatch.setattr(auth.secrets, 'token_urlsafe', issue_test_token)
    reset_client = security_app.test_client()
    known = reset_client.post('/password-reset/request', data={'username': 'reset-user'})
    unknown = reset_client.post('/password-reset/request', data={'username': 'no-such-user'})
    generic_text = b'If an eligible account exists and email delivery is configured'
    assert generic_text in known.data and generic_text in unknown.data
    normalize_csrf = lambda body: re.sub(rb'(name="csrf_token" value=")[^"]+', rb'\1TOKEN', body)
    assert normalize_csrf(known.data) == normalize_csrf(unknown.data)
    assert known.status_code == unknown.status_code == 200
    assert issued_tokens[0].encode() not in known.data
    assert b'valid-reset-token' not in known.data
    with security_app.app_context():
        expired_row = PasswordResetToken.query.filter_by(user_id=user_id).one()
        assert expired_row.token_hash == keyed_digest('password-reset', issued_tokens[0])
        assert issued_tokens[0] not in expired_row.token_hash
        expired_row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

    strong_password = 'Reset-Password7!'
    expired = reset_client.post('/password-reset/consume', data={
        'token': issued_tokens[0], 'password': strong_password,
        'password_confirmation': strong_password,
    })
    assert expired.status_code == 400
    with security_app.app_context():
        assert db.session.get(User, user_id).password_hash == original_password_hash

    second_request = reset_client.post('/password-reset/request', data={'username': 'reset-user'})
    assert second_request.status_code == 200
    completed = reset_client.post('/password-reset/consume', data={
        'token': issued_tokens[1], 'password': strong_password,
        'password_confirmation': strong_password,
    })
    assert completed.status_code == 200
    assert issued_tokens[1].encode() not in completed.data
    reused = reset_client.post('/password-reset/consume', data={
        'token': issued_tokens[1], 'password': strong_password,
        'password_confirmation': strong_password,
    })
    assert reused.status_code == 400
    assert authenticated.get('/admin/security').status_code == 302

    with security_app.app_context():
        user = db.session.get(User, user_id)
        assert user.password_hash.startswith('$argon2id$')
        assert verify_password_and_update(user.password_hash, strong_password) == (True, None)
        assert UserSession.query.filter_by(user_id=user_id, revoked_at=None).count() == 0
        valid_row = PasswordResetToken.query.filter_by(
            token_hash=keyed_digest('password-reset', issued_tokens[1]),
        ).one()
        assert valid_row.used_at is not None


def test_account_admin_routes_are_super_admin_only_and_block_self_disable(security_app):
    with security_app.app_context():
        administrator = User(
            username='account-admin', password_hash=hash_password('Account-Password7!'), role='SUPER_ADMIN',
        )
        student = User(username='account-student', password_hash=hash_password('Student-Password7!'), role='STUDENT')
        db.session.add_all([administrator, student])
        db.session.commit()
        administrator_id, student_id = administrator.id, student.id

    admin_client = security_app.test_client()
    student_client = security_app.test_client()
    assert admin_client.post('/login', data={
        'username': 'account-admin', 'password': 'Account-Password7!',
    }).status_code == 302
    assert student_client.post('/login', data={
        'username': 'account-student', 'password': 'Student-Password7!',
    }).status_code == 302
    assert admin_client.get('/admin/accounts/security').status_code == 200
    assert student_client.get('/admin/accounts/security').status_code == 403
    assert student_client.post(
        f'/admin/users/{administrator_id}/status', data={'is_enabled': 'false'},
    ).status_code == 403
    assert student_client.post(
        f'/admin/users/{student_id}/sessions/revoke',
    ).status_code == 403
    self_disable = admin_client.post(
        f'/admin/users/{administrator_id}/status', data={'is_enabled': 'false'},
    )
    assert self_disable.status_code == 400
    with security_app.app_context():
        assert db.session.get(User, administrator_id).is_enabled
