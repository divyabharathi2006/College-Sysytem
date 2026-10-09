from datetime import datetime, timedelta
from email.message import EmailMessage
import hmac
import secrets
import smtplib

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from audit import record_security_event
from models.audit import SecurityEvent
from models.faculty import Faculty
from models.student import Student
from models.user import User
from models.user_session import PasswordResetToken, UserSession
from permissions import AFFILIATED_COLLEGE_ROLES, Role, has_role, role_for
from security import (
    hash_password, invalidate_password_reset_tokens, keyed_digest,
    password_strength_error, revoke_user_sessions, verify_password_and_update,
)
from notifications_service import notify_security_admins
from extensions import db

auth_bp = Blueprint('auth', __name__)


def _start_validated_session(user):
    session_id = secrets.token_urlsafe(32)
    idle_timeout = timedelta(minutes=current_app.config['SESSION_IDLE_TIMEOUT_MINUTES'])
    session.clear()
    session.permanent = True
    session['auth_session_id'] = session_id
    user.last_login_at = datetime.utcnow()
    db.session.add(UserSession(
        user_id=user.id,
        session_id_hash=keyed_digest('auth-session', session_id),
        expires_at=datetime.utcnow() + idle_timeout,
    ))
    login_user(user)
    db.session.commit()
    record_security_event(
        action='auth.login', status='success', reason='credentials_accepted',
        actor=user, resource_type='user', resource_id=user.id,
        after_data={'role': user.role}, response_status=302,
    )


def _login_destination(user):
    if user.must_change_password:
        return redirect(url_for('auth.student_password_setup'))
    if has_role(user, Role.SUPER_ADMIN):
        return redirect(url_for('admin.dashboard'))
    if has_role(user, Role.MANAGEMENT):
        return redirect(url_for('admin.management_dashboard'))
    if has_role(user, Role.HOD):
        return redirect(url_for('admin.hod_dashboard'))
    if has_role(user, Role.DEPARTMENT_ADMIN):
        return redirect(url_for('admin.department_dashboard'))
    if has_role(user, Role.FACULTY):
        return redirect(url_for('faculty.dashboard'))
    if has_role(user, *AFFILIATED_COLLEGE_ROLES):
        return redirect(url_for('registries.affiliated_portal'))
    return redirect(url_for('student.dashboard'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return _login_destination(current_user)

    if request.method == 'POST':
        if request.form.get('mode') == 'student-bootstrap':
            student_id = request.form.get('student_id', '').strip()
            supplied_dob = request.form.get('date_of_birth', '').strip()
            student_user = User.query.filter_by(username=student_id).first() if student_id else None
            profile = Student.query.filter_by(user_id=student_user.id).first() if student_user else None
            verifier = None
            try:
                parsed_dob = datetime.strptime(supplied_dob, '%d-%m-%Y').date()
                if parsed_dob.strftime('%d-%m-%Y') != supplied_dob:
                    raise ValueError
                verifier = keyed_digest('student-bootstrap-dob', parsed_dob.isoformat())
            except ValueError:
                pass
            if (
                student_user and student_user.is_enabled and student_user.must_change_password
                and profile and profile.dob_verifier and verifier
                and hmac.compare_digest(profile.dob_verifier, verifier)
            ):
                _start_validated_session(student_user)
                flash('Verify your identity by choosing a new password.', 'info')
                return redirect(url_for('auth.student_password_setup'))
            record_security_event(
                action='auth.login', status='failure', reason='credentials_rejected',
                actor_role='anonymous', resource_type='authentication', response_status=200,
            )
            flash('Invalid username or password.', 'danger')
            return render_template('login.html')

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        valid_password, upgraded_hash = verify_password_and_update(
            user.password_hash if user else None, password,
        )
        if user and user.is_enabled and not user.must_change_password and valid_password:
            if upgraded_hash:
                user.password_hash = upgraded_hash
            _start_validated_session(user)
            flash('Login successful.', 'success')
            return _login_destination(user)
        record_security_event(
            action='auth.login', status='failure', reason='credentials_rejected',
            actor_role='anonymous', resource_type='authentication',
            response_status=200,
        )
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        ip_hash = keyed_digest('audit-ip', request.remote_addr or 'unknown')
        recent_failures = SecurityEvent.query.filter(
            SecurityEvent.action == 'auth.login', SecurityEvent.status == 'failure',
            SecurityEvent.ip_hash == ip_hash, SecurityEvent.created_at >= cutoff,
        ).count()
        if recent_failures >= 5 and recent_failures % 5 == 0:
            notify_security_admins(
                'Repeated failed sign-ins',
                f'{recent_failures} rejected sign-in attempts were recorded from one network address in the last 15 minutes.',
            )
            db.session.commit()
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@auth_bp.route('/student/password-setup', methods=['GET', 'POST'])
@login_required
def student_password_setup():
    user = current_user._get_current_object()
    if not user.must_change_password:
        return _login_destination(user)
    if request.method == 'POST':
        password = request.form.get('new_password', '')
        confirmation = request.form.get('password_confirmation', '')
        strength_error = password_strength_error(password)
        if strength_error:
            flash(strength_error, 'danger')
            return render_template('student_password_setup.html'), 400
        if password != confirmation:
            flash('The password confirmation does not match.', 'danger')
            return render_template('student_password_setup.html'), 400

        old_session_id = session.get('auth_session_id')
        now = datetime.utcnow()
        old_hash = keyed_digest('auth-session', old_session_id) if isinstance(old_session_id, str) else None
        live_session = UserSession.query.filter_by(
            user_id=user.id, session_id_hash=old_hash, revoked_at=None,
        ).filter(UserSession.expires_at > now).first() if old_hash else None
        if not live_session:
            logout_user()
            session.clear()
            flash('Your session is no longer valid. Sign in again.', 'danger')
            return redirect(url_for('auth.login'))

        user.password_hash = hash_password(password)
        user.must_change_password = False
        profile = Student.query.filter_by(user_id=user.id).first()
        if profile:
            profile.dob_verifier = None
        revoke_user_sessions(user.id, now=now)
        new_session_id = secrets.token_urlsafe(32)
        session.clear()
        session.permanent = True
        session['auth_session_id'] = new_session_id
        db.session.add(UserSession(
            user_id=user.id,
            session_id_hash=keyed_digest('auth-session', new_session_id),
            expires_at=now + timedelta(
                minutes=current_app.config['SESSION_IDLE_TIMEOUT_MINUTES'],
            ),
        ))
        login_user(user, fresh=True)
        invalidate_password_reset_tokens(user.id, now=now)
        db.session.commit()
        record_security_event(
            action='account.password_changed', status='success', reason='first_sign_in_password_set',
            actor=user, resource_type='user', resource_id=user.id, response_status=302,
        )
        flash('Your password has been set. Welcome.', 'success')
        return _login_destination(user)
    return render_template('student_password_setup.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    user = current_user._get_current_object()
    session_id = session.get('auth_session_id')
    if isinstance(session_id, str):
        UserSession.query.filter_by(
            user_id=user.id,
            session_id_hash=keyed_digest('auth-session', session_id),
            revoked_at=None,
        ).update({UserSession.revoked_at: datetime.utcnow()}, synchronize_session=False)
    logout_user()
    session.clear()
    db.session.commit()
    record_security_event(
        action='auth.logout', status='success', reason='user_logout',
        actor=user, resource_type='user', resource_id=user.id, response_status=302,
    )
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


def _self_service_profile(user):
    if has_role(user, Role.STUDENT):
        return Student.query.filter_by(user_id=user.id).one_or_none(), ('name', 'email', 'phone')
    if has_role(user, Role.FACULTY):
        return Faculty.query.filter_by(user_id=user.id).one_or_none(), ('name', 'email')
    return None, ()


def _valid_profile_email(value):
    if not value or len(value) > 120 or any(char.isspace() for char in value):
        return False
    local, separator, domain = value.partition('@')
    return bool(separator and local and domain and '@' not in domain and '.' in domain)


@auth_bp.route('/account/settings', methods=['GET', 'POST'])
@login_required
def account_settings():
    user = current_user._get_current_object()
    profile, editable_fields = _self_service_profile(user)
    if request.method == 'GET':
        return render_template(
            'account_settings.html', profile=profile, editable_fields=editable_fields,
            role=role_for(user),
        )

    action = str(request.form.get('action', '')).strip().lower()
    if action == 'profile':
        if profile is None or not editable_fields:
            flash('No self-service profile fields are available for this account.', 'danger')
            return redirect(url_for('auth.account_settings'))
        updates = {}
        for field in editable_fields:
            if field not in request.form:
                continue
            value = request.form.get(field, '').strip()
            maximum = {'name': 120, 'email': 120, 'phone': 30}[field]
            if not value or len(value) > maximum:
                flash(f'Enter a value for {field} within {maximum} characters.', 'danger')
                return redirect(url_for('auth.account_settings'))
            if field == 'email' and not _valid_profile_email(value):
                flash('Enter a valid email address.', 'danger')
                return redirect(url_for('auth.account_settings'))
            updates[field] = value
        if not updates:
            flash('No profile changes were submitted.', 'danger')
            return redirect(url_for('auth.account_settings'))
        for field, value in updates.items():
            setattr(profile, field, value)
        db.session.commit()
        record_security_event(
            action='account.profile_updated', status='success', reason='self_service_profile_updated',
            actor=user, resource_type='user', resource_id=user.id,
            response_status=302,
        )
        flash('Profile settings saved.', 'success')
        return redirect(url_for('auth.account_settings'))

    if action != 'password':
        flash('Choose a valid account settings action.', 'danger')
        return redirect(url_for('auth.account_settings'))
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirmation = request.form.get('password_confirmation', '')
    verified, _upgrade = verify_password_and_update(user.password_hash, current_password)
    if not verified:
        flash('The current password is incorrect.', 'danger')
        return redirect(url_for('auth.account_settings'))
    strength_error = password_strength_error(new_password)
    if strength_error:
        flash(strength_error, 'danger')
        return redirect(url_for('auth.account_settings'))
    if new_password != confirmation:
        flash('The password confirmation does not match.', 'danger')
        return redirect(url_for('auth.account_settings'))
    if verify_password_and_update(user.password_hash, new_password)[0]:
        flash('Choose a new password different from your current password.', 'danger')
        return redirect(url_for('auth.account_settings'))

    session_id = session.get('auth_session_id')
    if not isinstance(session_id, str):
        flash('Your session is no longer valid. Sign in again before changing your password.', 'danger')
        return redirect(url_for('auth.login'))
    now = datetime.utcnow()
    current_hash = keyed_digest('auth-session', session_id)
    live_session = UserSession.query.filter_by(
        user_id=user.id, session_id_hash=current_hash, revoked_at=None,
    ).filter(UserSession.expires_at > now).first()
    if not live_session:
        flash('Your session is no longer valid. Sign in again before changing your password.', 'danger')
        return redirect(url_for('auth.login'))

    user.password_hash = hash_password(new_password)
    UserSession.query.filter(
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
        UserSession.session_id_hash != current_hash,
    ).update({UserSession.revoked_at: now}, synchronize_session=False)
    invalidate_password_reset_tokens(user.id, now=now)
    db.session.commit()
    record_security_event(
        action='account.password_changed', status='success', reason='self_service_password_changed',
        actor=user, resource_type='user', resource_id=user.id,
        response_status=302,
    )
    flash('Password changed. Other active sessions have been signed out.', 'success')
    return redirect(url_for('auth.account_settings'))


def _reset_recipient(user):
    student = Student.query.filter_by(user_id=user.id).first()
    if student and student.email:
        return student.email
    faculty = Faculty.query.filter_by(user_id=user.id).first()
    return faculty.email if faculty and faculty.email else None


def _send_password_reset_email(recipient, token):
    config = current_app.config
    message = EmailMessage()
    message['Subject'] = 'Password reset instructions'
    message['From'] = config['SMTP_FROM']
    message['To'] = recipient
    base_url = config.get('PUBLIC_BASE_URL') or request.url_root.rstrip('/')
    reset_url = f"{base_url}{url_for('auth.password_reset_consume')}#{token}"
    message.set_content(
        'A password reset was requested for your account. Open this link to choose a new password. '
        'If you did not request this, you can ignore this message.\n\n'
        f'{reset_url}\n'
    )
    with smtplib.SMTP(config['SMTP_HOST'], config['SMTP_PORT'], timeout=10) as smtp:
        if config.get('SMTP_USE_TLS'):
            smtp.starttls()
        if config.get('SMTP_USERNAME'):
            smtp.login(config['SMTP_USERNAME'], config.get('SMTP_PASSWORD', ''))
        smtp.send_message(message)


@auth_bp.route('/password-reset/request', methods=['GET', 'POST'])
def password_reset_request():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        user = User.query.filter_by(username=username).first() if username else None
        if user and user.is_enabled:
            now = datetime.utcnow()
            token = secrets.token_urlsafe(32)
            invalidate_password_reset_tokens(user.id, now=now)
            reset_row = PasswordResetToken(
                user_id=user.id,
                token_hash=keyed_digest('password-reset', token),
                created_at=now,
                expires_at=now + timedelta(minutes=current_app.config['PASSWORD_RESET_LIFETIME_MINUTES']),
            )
            db.session.add(reset_row)
            db.session.commit()
            recipient = _reset_recipient(user)
            if current_app.config.get('SMTP_HOST') and current_app.config.get('SMTP_FROM') and recipient:
                try:
                    _send_password_reset_email(recipient, token)
                except Exception:
                    current_app.logger.warning('Password reset email delivery failed.')
        flash(
            'If an eligible account exists and email delivery is configured, password-reset instructions will be sent.',
            'info',
        )
    return render_template('password_reset_request.html')


@auth_bp.route('/password-reset/consume', methods=['GET', 'POST'])
def password_reset_consume():
    if request.method == 'GET':
        return render_template('password_reset_consume.html')

    token = request.form.get('token', '')
    password = request.form.get('password', '')
    confirmation = request.form.get('password_confirmation', '')
    strength_error = password_strength_error(password)
    if strength_error:
        flash(strength_error, 'danger')
        return render_template('password_reset_consume.html'), 400
    if password != confirmation:
        flash('The password confirmation does not match.', 'danger')
        return render_template('password_reset_consume.html'), 400
    if not token:
        flash('This password reset link is invalid or expired.', 'danger')
        return render_template('password_reset_consume.html'), 400

    now = datetime.utcnow()
    token_hash = keyed_digest('password-reset', token)
    reset_row = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if not reset_row or reset_row.used_at is not None or reset_row.expires_at <= now:
        flash('This password reset link is invalid or expired.', 'danger')
        return render_template('password_reset_consume.html'), 400
    user = db.session.get(User, reset_row.user_id)
    if not user or not user.is_enabled:
        flash('This password reset link is invalid or expired.', 'danger')
        return render_template('password_reset_consume.html'), 400

    claimed = PasswordResetToken.query.filter(
        PasswordResetToken.id == reset_row.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)
    if claimed != 1:
        db.session.rollback()
        flash('This password reset link is invalid or expired.', 'danger')
        return render_template('password_reset_consume.html'), 400
    user.password_hash = hash_password(password)
    revoke_user_sessions(user.id, now=now)
    invalidate_password_reset_tokens(user.id, now=now)
    db.session.commit()
    record_security_event(
        action='auth.password_reset', status='success', reason='password_reset_completed',
        actor=user, resource_type='user', resource_id=user.id, response_status=200,
    )
    return render_template('password_reset_complete.html')
