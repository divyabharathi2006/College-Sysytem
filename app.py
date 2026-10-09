import os
import shutil
from datetime import datetime, timedelta

import click
from flask import Flask, Response, current_app, redirect, render_template, request, session, url_for
from flask.typing import RouteCallable
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from typing import cast

from config import Config
from extensions import csrf, db, limiter, login_manager, migrate
from security import hash_password, keyed_digest, login_rate_limit_key, rate_limit_ip_key


@login_manager.user_loader
def load_user(user_id):
    from models.user_session import UserSession
    from models.user import User

    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        user = None
    session_id = session.get('auth_session_id')
    if user is None or not user.is_enabled or not isinstance(session_id, str):
        session.clear()
        return None
    now = datetime.utcnow()
    auth_session = UserSession.query.filter_by(
        session_id_hash=keyed_digest('auth-session', session_id),
        user_id=user.id,
    ).first()
    if (
        auth_session is None or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        session.clear()
        return None
    auth_session.expires_at = now + timedelta(
        minutes=current_app.config['SESSION_IDLE_TIMEOUT_MINUTES'],
    )
    db.session.commit()
    return user


def create_app(testing=False):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or app.config['SQLALCHEMY_DATABASE_URI']
    app.config['SQLALCHEMY_BINDS'] = {
        bind_key: os.environ.get(environment_key) or configured_url
        for bind_key, environment_key, configured_url in (
            ('university_students', 'UNIVERSITY_STUDENTS_DATABASE_URL', app.config['SQLALCHEMY_BINDS']['university_students']),
            ('affiliated_students', 'AFFILIATED_STUDENTS_DATABASE_URL', app.config['SQLALCHEMY_BINDS']['affiliated_students']),
            ('affiliated_colleges', 'AFFILIATED_COLLEGES_DATABASE_URL', app.config['SQLALCHEMY_BINDS']['affiliated_colleges']),
        )
    }
    if testing:
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-only-secret-key'
        app.config['WTF_CSRF_ENABLED'] = False
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_BINDS'] = {
            'university_students': 'sqlite://',
            'affiliated_students': 'sqlite://',
            'affiliated_colleges': 'sqlite://',
        }
        app.config['STUDENT_EMAIL_VERIFICATION_REQUIRED'] = False
    elif not app.config.get('SECRET_KEY'):
        raise RuntimeError('Set SECRET_KEY in the environment or local .env file before starting the application.')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'database'), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    login_manager.init_app(app)
    setattr(login_manager, 'login_view', 'auth.login')
    login_manager.session_protection = 'strong'
    csrf.init_app(app)
    limiter.init_app(app)

    trusted_proxy_hops = app.config.get('TRUSTED_PROXY_HOPS', 0)
    if trusted_proxy_hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=trusted_proxy_hops, x_proto=trusted_proxy_hops,
            x_host=0, x_port=0, x_prefix=0,
        )

    from models import AcademicYear, Announcement, Attendance, AttendanceCorrectionRequest, AttendanceImportStage, Batch, ClassSession, Classroom, Course, Department, Faculty, LeaveRequest, Notification, NotificationPreference, PasswordResetToken, PredictionHistory, Section, Semester, Student, Subject, SystemPolicy, User, UserSession

    if testing:
        with app.app_context():
            db.create_all()

    from routes.admin import admin_bp
    from routes.academic_admin import academic_admin_bp
    from routes.api import api_bp
    from routes.auth import auth_bp
    from routes.faculty import faculty_bp
    from routes.student import student_bp
    from routes.attendance_workflows import attendance_workflows_bp
    from routes.notifications import notifications_bp
    from routes.announcements import announcements_bp
    from routes.registries import registries_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(academic_admin_bp, url_prefix='/admin')
    app.register_blueprint(faculty_bp, url_prefix='/faculty')
    app.register_blueprint(student_bp, url_prefix='/student')
    app.register_blueprint(attendance_workflows_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(announcements_bp)
    app.register_blueprint(registries_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(api_bp, url_prefix='/api/v1', name='api_v1')

    from audit import install_audit_hooks
    install_audit_hooks(app)
    from operational_controls import install_operational_controls
    install_operational_controls(app)

    @app.before_request
    def enforce_student_password_setup():
        from flask_login import current_user

        if (
            current_user.is_authenticated
            and current_user.must_change_password
            and request.endpoint not in {
                'auth.login', 'auth.logout', 'auth.student_password_setup', 'static',
            }
        ):
            return redirect(url_for('auth.student_password_setup'))
        return None

    endpoint_methods = {}
    for rule in app.url_map.iter_rules():
        endpoint_methods.setdefault(rule.endpoint, set()).update(rule.methods or ())
    for endpoint, methods in endpoint_methods.items():
        view = app.view_functions.get(endpoint)
        if view is None:
            continue
        mutating_methods = methods.intersection({'POST', 'PUT', 'PATCH', 'DELETE'})
        if endpoint == 'auth.login':
            view = limiter.limit('5 per minute', key_func=login_rate_limit_key, methods=['POST'])(view)
        elif endpoint == 'auth.password_reset_request':
            view = limiter.limit('3 per hour', key_func=rate_limit_ip_key, methods=['POST'])(view)
        elif endpoint == 'auth.password_reset_consume':
            view = limiter.limit('10 per hour', key_func=rate_limit_ip_key, methods=['POST'])(view)
        elif endpoint == 'auth.student_password_setup':
            view = limiter.limit('10 per hour', key_func=rate_limit_ip_key, methods=['POST'])(view)
        elif endpoint == 'registries.verify_student_email':
            view = limiter.limit('10 per hour', key_func=rate_limit_ip_key, methods=['POST'])(view)
        elif endpoint in {
            'registries.create_student_registration',
            'registries.edit_student_registration_details',
            'registries.resend_student_verification',
            'registries.create_affiliated_college_account',
            'registries.edit_affiliated_college_account',
            'registries.disable_affiliated_college_account',
            'registries.initialize_registries',
            'registries.edit_college', 'registries.delete_college',
            'registries.colleges',
        }:
            view = limiter.limit('20 per hour', key_func=rate_limit_ip_key, methods=['POST'])(view)
        elif endpoint in {'admin.import_csv', 'api.import_attendance'}:
            view = limiter.limit('5 per hour', key_func=rate_limit_ip_key)(view)
        elif endpoint in {'api.export_attendance', 'api.export_attendance_file'}:
            view = limiter.limit('10 per hour', key_func=rate_limit_ip_key)(view)
        elif endpoint.startswith(('api.', 'api_v1.')):
            view = limiter.limit('120 per minute', key_func=rate_limit_ip_key)(view)
        elif mutating_methods and endpoint.startswith((
            'admin.', 'academic_admin.', 'attendance_workflows.', 'faculty.',
            'announcements.', 'auth.account_settings',
        )):
            view = limiter.limit('40 per hour', key_func=rate_limit_ip_key, methods=sorted(mutating_methods))(view)
        app.view_functions[endpoint] = cast(RouteCallable, view)

    safe_error_messages = {
        400: 'The request could not be understood.',
        401: 'Authentication is required to access this resource.',
        403: 'You are not authorized to access this resource.',
        404: 'The requested resource was not found.',
        413: 'The uploaded request is too large.',
        429: 'Too many requests. Please try again later.',
        500: 'An unexpected server error occurred.',
    }

    def safe_http_error(error):
        status = int(error.code or 500) if isinstance(error, HTTPException) else 500
        if status >= 500:
            original_error = getattr(error, 'original_exception', None) or error
            app.logger.error(
                'Request failed with HTTP %s.',
                status,
                exc_info=(
                    type(original_error),
                    original_error,
                    original_error.__traceback__,
                ),
            )
        message = safe_error_messages.get(status, safe_error_messages[500])
        return Response(message, status=status, mimetype='text/plain')

    for status_code in safe_error_messages:
        app.register_error_handler(status_code, safe_http_error)

    @app.after_request
    def add_security_headers(response):
        if app.config.get('SECURITY_HEADERS_ENABLED'):
            response.headers.setdefault('Content-Security-Policy', app.config['CONTENT_SECURITY_POLICY'])
        if request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        hsts_max_age = app.config.get('SECURITY_HSTS_MAX_AGE', 0)
        if request.is_secure and hsts_max_age:
            response.headers.setdefault('Strict-Transport-Security', f'max-age={hsts_max_age}')
        return response

    @app.route('/')
    def index():
        return render_template('home.html')

    @app.cli.command('import-students')
    @click.argument('csv_path', required=False, type=click.Path(dir_okay=False, path_type=str))
    @click.option('--commit', is_flag=True, help='Persist the validated roster. Without this flag, the command is dry-run only.')
    def import_students(csv_path, commit):
        """Validate the university student roster; persist only with --commit."""
        from student_import import import_student_csv

        source_path = csv_path or os.path.join(app.root_path, 'tamil_nadu_student_database_6000.csv')
        try:
            with open(source_path, 'rb') as source:
                result, committed = import_student_csv(source, commit=commit)
        except OSError:
            raise click.ClickException('Student CSV could not be opened.')
        if result.errors:
            click.echo(f'Validation failed: {result.total_rows} row(s) checked; {len(result.errors)} safe error(s). No records were imported.')
            for error in result.errors[:20]:
                click.echo(f"Row {error['row']}: {error['code']}")
            if len(result.errors) > 20:
                click.echo(f'{len(result.errors) - 20} additional error(s) omitted.')
            raise click.ClickException('Resolve the reported row issues and run the import again.')
        if committed:
            click.echo(f'Imported {len(result.valid_rows)} student profile(s) atomically.')
        else:
            click.echo(f'Dry run valid: {len(result.valid_rows)} student profile(s); no database changes were made. Pass --commit to import.')

    @app.cli.command('bootstrap-admin')
    def bootstrap_admin():
        """Create the first administrator from private environment variables."""
        from models.user import User
        from permissions import Role, has_role

        username = os.environ.get('BOOTSTRAP_ADMIN_USERNAME', '').strip()
        password = os.environ.get('BOOTSTRAP_ADMIN_PASSWORD', '')
        if not username or not password:
            raise click.ClickException('Set BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD before running this command.')
        if any(has_role(user, Role.SUPER_ADMIN) for user in User.query.all()):
            raise click.ClickException('A Super Admin already exists; bootstrap is only available once.')
        if User.query.filter_by(username=username).first():
            raise click.ClickException('That username is already in use.')
        administrator = User()
        administrator.username = username
        administrator.password_hash = hash_password(password)
        administrator.role = Role.SUPER_ADMIN.value
        db.session.add(administrator)
        db.session.commit()
        click.echo('Administrator account created.')

    @app.cli.command('init-registry-databases')
    def init_registry_databases():
        """Create missing tables in the three student and college registry files."""
        from registry_store import initialize_registry_databases

        try:
            initialize_registry_databases()
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        click.echo('Registry tables are ready. Existing attendance data and registry rows were not reset.')

    @app.cli.command('reset-database')
    @click.option('--confirm-path', required=True, help='Type the exact absolute SQLite database path to authorize reset.')
    def reset_database(confirm_path):
        """Back up and recreate the configured local SQLite database."""
        from urllib.parse import unquote

        url = db.engine.url
        if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
            raise click.ClickException('Reset is restricted to a file-backed SQLite database.')
        database_path = os.path.realpath(unquote(url.database))
        if os.path.normcase(os.path.abspath(confirm_path)) != os.path.normcase(database_path):
            raise click.ClickException(f'Confirmation path does not match the configured database: {database_path}')
        if os.path.exists(database_path):
            backup_path = f"{database_path}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
            shutil.copy2(database_path, backup_path)
            click.echo(f'Existing database backed up to {backup_path}')
        db.drop_all(bind_key=[None])
        db.create_all(bind_key=[None])
        click.echo('Database schema recreated; no demo records were added. Run flask db upgrade to initialize migration tracking.')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(
        host=os.environ.get('HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', '5000')),
        debug=app.config['DEBUG'],
    )
