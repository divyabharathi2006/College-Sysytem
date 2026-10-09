from datetime import date, datetime
from email.message import EmailMessage
import re
import smtplib

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.attendance import Attendance
from models.registry import (
    AffiliatedCollegeRecord, AffiliatedStudentRecord, StudentRegistrationContact,
    UniversityStudentRecord,
)
from models.student import Student
from models.user import User
from permissions import (
    AFFILIATED_COLLEGE_ROLES, ASSIGNABLE_AFFILIATED_COLLEGE_ROLES,
    Role, has_permission, has_role,
)
from registry_store import initialize_registry_databases, registry_table_ready
from registration_numbers import next_registration_number
from security import (
    hash_password, invalidate_password_reset_tokens, password_strength_error,
    revoke_user_sessions,
)

registries_bp = Blueprint('registries', __name__)
_COLLEGE_FIELDS = {
    'name': 160, 'address': 4000, 'contact_number': 30, 'email': 120,
    'affiliation_details': 4000, 'staff_details': 4000,
    'attendance_summary': 4000, 'infrastructure_details': 4000,
    'courses_offered': 4000, 'counseling_services': 2000,
    'scholarship_details': 2000, 'extracurricular_activities': 2000,
}
_STUDENT_FIELDS = {
    'name': 120, 'email': 120, 'phone': 30, 'department': 80, 'year': 20,
    'section': 20, 'semester': 20, 'gender': 30, 'father_name': 120,
    'mother_name': 120, 'address': 2000, 'community': 80,
    'identification_type': 80, 'disability_details': 300,
}
_STUDENT_GENDERS = {'Female', 'Male', 'Non-binary', 'Other', 'Prefer not to say'}
_AFFILIATED_ROLE_LABELS = {
    Role.AFF_COLLEGE_ADMIN.value: 'College Admin',
    Role.AFF_REGISTRAR.value: 'Registrar',
    Role.AFF_ATTENDANCE_OFFICER.value: 'Attendance Officer',
    Role.AFFILIATED_COLLEGE.value: 'Legacy college account',
}


def _registry_ready(bind_key, table_name):
    return registry_table_ready(bind_key, table_name)


def _admin_only():
    return has_role(current_user, Role.SUPER_ADMIN)


def _is_affiliated_college_user(user):
    return has_role(user, *AFFILIATED_COLLEGE_ROLES)


def _affiliated_college_for_user(user):
    if not _is_affiliated_college_user(user):
        return None
    college_code = str(getattr(user, 'affiliated_college_code', '') or '').strip().upper()
    if not college_code or not _registry_ready(
        'affiliated_colleges', 'affiliated_college_records',
    ):
        return None
    college = AffiliatedCollegeRecord.query.filter_by(
        college_code=college_code, is_active=True,
    ).first()
    return college


def _student_registration_return():
    if _is_affiliated_college_user(current_user):
        return redirect(url_for('registries.affiliated_portal'))
    return redirect(url_for('admin.students'))


def _registry_return_target():
    if request.form.get('return_to') == 'students':
        return redirect(url_for('admin.students'))
    return redirect(url_for('registries.colleges'))


def _email_is_valid(value):
    return bool(re.fullmatch(r'[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+', value))


def _normalize_phone(value):
    if not re.fullmatch(r'[+()\-\s0-9]+', value):
        return None
    digits = re.sub(r'\D', '', value)
    return digits if 7 <= len(digits) <= 15 else None


def _valid_contact(email, phone, *, exclude_student_id=None):
    if not _email_is_valid(email) or not _normalize_phone(phone):
        return 'Enter a valid email address and phone number.'
    students = Student.query
    if exclude_student_id is not None:
        students = students.filter(Student.id != exclude_student_id)
    profiles = students.all()
    if any(item.email.strip().casefold() == email.casefold() for item in profiles):
        return 'That email address is already registered to a student.'
    normalized_phone = _normalize_phone(phone)
    if any(_normalize_phone(item.phone) == normalized_phone for item in profiles):
        return 'That phone number is already registered to a student.'
    return None


def _verification_serializer():
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'], salt='student-email-verification-v1',
    )


def _send_verification_email(user, student):
    config = current_app.config
    if not config.get('SMTP_HOST') or not config.get('SMTP_FROM'):
        raise RuntimeError('Student email verification requires SMTP_HOST and SMTP_FROM.')
    token = _verification_serializer().dumps({
        'user_id': user.id, 'email': student.email.casefold(),
    })
    base_url = config.get('PUBLIC_BASE_URL') or request.url_root.rstrip('/')
    verify_url = f"{base_url}{url_for('registries.verify_student_email')}#{token}"
    message = EmailMessage()
    message['Subject'] = 'Verify your student registration email'
    message['From'] = config['SMTP_FROM']
    message['To'] = student.email
    message.set_content(
        'An authorized university or affiliated-college staff member created a student account using this email address. '
        'Open the link below to verify the address and activate the account. '
        'If you did not expect this message, contact the university registrar.\n\n'
        f'{verify_url}\n'
    )
    with smtplib.SMTP(config['SMTP_HOST'], config['SMTP_PORT'], timeout=10) as smtp:
        if config.get('SMTP_USE_TLS'):
            smtp.starttls()
        if config.get('SMTP_USERNAME'):
            smtp.login(config['SMTP_USERNAME'], config.get('SMTP_PASSWORD', ''))
        smtp.send_message(message)


def _college_form_values(payload):
    values = {}
    for field, maximum in _COLLEGE_FIELDS.items():
        value = str(payload.get(field, '')).strip()
        if field in {
            'name', 'address', 'contact_number', 'email', 'affiliation_details',
        } and not value:
            return None, f'Complete the required college field: {field.replace("_", " ")}.'
        if len(value) > maximum:
            return None, f'{field.replace("_", " ").capitalize()} must be no more than {maximum} characters.'
        values[field] = value
    if not _email_is_valid(values['email']):
        return None, 'Enter a valid college email address.'
    if not _normalize_phone(values['contact_number']):
        return None, 'Enter a valid college contact number.'
    try:
        strength = int(payload.get('student_strength', '0'))
    except (TypeError, ValueError):
        return None, 'Student strength must be a non-negative whole number.'
    if not 0 <= strength <= 10_000_000:
        return None, 'Student strength must be between 0 and 10000000.'
    values['student_strength'] = strength
    values['has_antiragging_committee'] = payload.get('has_antiragging_committee') == 'on'
    return values, None


@registries_bp.route('/admin/registries/colleges', methods=['GET', 'POST'])
@login_required
def colleges():
    if not _admin_only():
        return 'Forbidden', 403
    if not _registry_ready('affiliated_colleges', 'affiliated_college_records'):
        return render_template('college_management.html', colleges=[], registry_ready=False), 503
    if request.method == 'POST':
        code = str(request.form.get('college_code', '')).strip().upper()
        values, error = _college_form_values(request.form)
        if not re.fullmatch(r'[A-Z0-9-]{2,30}', code):
            flash('College code must contain 2 to 30 letters, numbers, or hyphens.', 'danger')
            return redirect(url_for('registries.colleges'))
        if error:
            flash(error, 'danger')
            return redirect(url_for('registries.colleges'))
        existing = AffiliatedCollegeRecord.query.filter(db.or_(
            db.func.lower(AffiliatedCollegeRecord.email) == values['email'].casefold(),
            AffiliatedCollegeRecord.college_code == code,
        )).first()
        if existing:
            flash('College code or email address is already registered.', 'danger')
            return redirect(url_for('registries.colleges'))
        used_prefixes = [
            int(item.registration_prefix[3:])
            for item in AffiliatedCollegeRecord.query.with_entities(
                AffiliatedCollegeRecord.registration_prefix,
            ).all()
            if re.fullmatch(r'DBU3[0-9]{2}', item.registration_prefix)
        ]
        next_prefix_number = max([300, *used_prefixes]) + 1
        if next_prefix_number > 399:
            flash('No affiliated registration prefixes remain; contact the registrar.', 'danger')
            return redirect(url_for('registries.colleges'))
        college = AffiliatedCollegeRecord(
            college_code=code,
            registration_prefix=f'DBU{next_prefix_number:03d}',
            **values,
        )
        db.session.add(college)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('College name, email, or contact number is already in use.', 'danger')
            return redirect(url_for('registries.colleges'))
        flash(f'College created with registration prefix {college.registration_prefix}.', 'success')
        return redirect(url_for('registries.colleges'))
    colleges = AffiliatedCollegeRecord.query.order_by(AffiliatedCollegeRecord.name).all()
    college_accounts = {
        college.college_code: User.query.filter(
            User.role.in_([role.value for role in AFFILIATED_COLLEGE_ROLES]),
            affiliated_college_code=college.college_code,
        ).order_by(User.username).all()
        for college in colleges
    }
    return render_template(
        'college_management.html',
        colleges=colleges,
        college_accounts=college_accounts,
        registry_ready=True,
    )


@registries_bp.route('/admin/registries/initialize', methods=['POST'])
@login_required
def initialize_registries():
    if not _admin_only():
        return 'Forbidden', 403
    try:
        initialize_registry_databases()
    except ValueError as error:
        flash(str(error), 'danger')
        return _registry_return_target()
    flash(
        'Registry databases are ready. Existing registry and attendance data were preserved.',
        'success',
    )
    return _registry_return_target()


@registries_bp.route('/admin/registries/colleges/<int:college_id>/edit', methods=['POST'])
@login_required
def edit_college(college_id):
    if not _admin_only():
        return 'Forbidden', 403
    if not _registry_ready('affiliated_colleges', 'affiliated_college_records'):
        return 'Registry database is not initialized.', 503
    college = db.session.get(AffiliatedCollegeRecord, college_id)
    if college is None:
        return 'College not found.', 404
    values, error = _college_form_values(request.form)
    if error:
        flash(error, 'danger')
        return redirect(url_for('registries.colleges'))
    for field, value in values.items():
        setattr(college, field, value)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('College name, email, or contact number is already in use.', 'danger')
        return redirect(url_for('registries.colleges'))
    flash('College details updated.', 'success')
    return redirect(url_for('registries.colleges'))


@registries_bp.route('/admin/registries/colleges/<int:college_id>/delete', methods=['POST'])
@login_required
def delete_college(college_id):
    if not _admin_only():
        return 'Forbidden', 403
    if not _registry_ready('affiliated_colleges', 'affiliated_college_records'):
        return 'Registry database is not initialized.', 503
    college = db.session.get(AffiliatedCollegeRecord, college_id)
    if college is None:
        return 'College not found.', 404
    college.is_active = False
    college_users = User.query.filter_by(
        affiliated_college_code=college.college_code,
    ).filter(User.role.in_([role.value for role in AFFILIATED_COLLEGE_ROLES])).all()
    for college_user in college_users:
        college_user.is_enabled = False
        revoke_user_sessions(college_user.id)
        invalidate_password_reset_tokens(college_user.id)
    db.session.commit()
    flash(
        'College deactivated. Its staff accounts were disabled; historical student records and its registration prefix have been retained.',
        'success',
    )
    return redirect(url_for('registries.colleges'))


@registries_bp.route('/admin/registries/colleges/<int:college_id>/accounts', methods=['POST'])
@login_required
def create_affiliated_college_account(college_id):
    if not _admin_only():
        return 'Forbidden', 403
    if not _registry_ready('affiliated_colleges', 'affiliated_college_records'):
        return 'Affiliated-college registry is not initialized.', 503
    college = db.session.get(AffiliatedCollegeRecord, college_id)
    if not college or not college.is_active:
        return 'Active affiliated college not found.', 404

    display_name = str(request.form.get('display_name', '')).strip()
    username = str(request.form.get('username', '')).strip()
    password = str(request.form.get('password', ''))
    assigned_role = str(request.form.get('role', '')).strip()
    if len(display_name) < 2 or len(display_name) > 120:
        flash("Enter the staff member's full name (2 to 120 characters).", 'danger')
        return redirect(url_for('registries.colleges'))
    if not re.fullmatch(r'[A-Za-z0-9_.@+-]{3,80}', username):
        flash('Enter a valid username between 3 and 80 characters.', 'danger')
        return redirect(url_for('registries.colleges'))
    if assigned_role not in {role.value for role in ASSIGNABLE_AFFILIATED_COLLEGE_ROLES}:
        flash('Choose a valid affiliated-college role.', 'danger')
        return redirect(url_for('registries.colleges'))
    if User.query.filter_by(username=username).first():
        flash('That username is already in use.', 'danger')
        return redirect(url_for('registries.colleges'))
    password_error = password_strength_error(password)
    if password_error:
        flash(password_error, 'danger')
        return redirect(url_for('registries.colleges'))

    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        role=assigned_role,
        affiliated_college_code=college.college_code,
        is_enabled=True,
        must_change_password=True,
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('Unable to create the college account. Check the username and try again.', 'danger')
        return redirect(url_for('registries.colleges'))
    flash(
        f'{display_name} ({username}) was added as {_AFFILIATED_ROLE_LABELS[assigned_role]} for {college.name}. '
        'The staff member must change the temporary password at first sign-in.',
        'success',
    )
    return redirect(url_for('registries.colleges'))


@registries_bp.route(
    '/admin/registries/colleges/<int:college_id>/accounts/<int:user_id>/edit',
    methods=['POST'],
)
@login_required
def edit_affiliated_college_account(college_id, user_id):
    if not _admin_only():
        return 'Forbidden', 403
    if not _registry_ready('affiliated_colleges', 'affiliated_college_records'):
        return 'Affiliated-college registry is not initialized.', 503
    college = db.session.get(AffiliatedCollegeRecord, college_id)
    user = db.session.get(User, user_id)
    if (
        not college or not user or not _is_affiliated_college_user(user)
        or user.affiliated_college_code != college.college_code
    ):
        return 'College staff account not found.', 404
    display_name = str(request.form.get('display_name', '')).strip()
    assigned_role = str(request.form.get('role', '')).strip()
    if len(display_name) < 2 or len(display_name) > 120:
        flash("Enter the staff member's full name (2 to 120 characters).", 'danger')
        return redirect(url_for('registries.colleges'))
    if assigned_role not in {role.value for role in ASSIGNABLE_AFFILIATED_COLLEGE_ROLES}:
        flash('Choose a valid affiliated-college role.', 'danger')
        return redirect(url_for('registries.colleges'))
    user.display_name = display_name
    user.role = assigned_role
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('Unable to update the affiliated-college account.', 'danger')
        return redirect(url_for('registries.colleges'))
    flash(f"Updated {display_name}'s college role and account details.", 'success')
    return redirect(url_for('registries.colleges'))


@registries_bp.route(
    '/admin/registries/colleges/<int:college_id>/accounts/<int:user_id>/disable',
    methods=['POST'],
)
@login_required
def disable_affiliated_college_account(college_id, user_id):
    if not _admin_only():
        return 'Forbidden', 403
    if not _registry_ready('affiliated_colleges', 'affiliated_college_records'):
        return 'Affiliated-college registry is not initialized.', 503
    college = db.session.get(AffiliatedCollegeRecord, college_id)
    user = db.session.get(User, user_id)
    if (
        not college or not user
        or not _is_affiliated_college_user(user)
        or user.affiliated_college_code != college.college_code
    ):
        return 'College staff account not found.', 404
    if user.is_enabled:
        user.is_enabled = False
        revoke_user_sessions(user.id)
        invalidate_password_reset_tokens(user.id)
        db.session.commit()
    flash(f'College staff account {user.username} has been disabled.', 'success')
    return redirect(url_for('registries.colleges'))


@registries_bp.route('/affiliated/portal')
@login_required
def affiliated_portal():
    if not _is_affiliated_college_user(current_user):
        return 'Forbidden', 403
    if current_user.must_change_password:
        return redirect(url_for('auth.student_password_setup'))
    college = _affiliated_college_for_user(current_user)
    if not college:
        return 'This college account is not linked to an active college.', 403
    registry_ready = _registry_ready(
        'affiliated_students', 'affiliated_student_records',
    )
    registration_ready = (
        registry_ready and _registry_ready(None, 'student_registration_contacts')
    )
    registration_preview = (
        next_registration_number(college.registration_prefix)
        if registration_ready else None
    )
    if not registry_ready:
        return render_template(
            'affiliated_college_portal.html',
            college=college,
            registry_ready=False,
            registration_ready=False,
            registration_preview=None,
            can_register=has_permission(current_user, 'student:register_affiliated'),
            can_edit=has_permission(current_user, 'student:edit_affiliated'),
            can_view_attendance=has_permission(current_user, 'attendance:read_affiliated'),
            roster=[],
            total_attendance=0,
            present_attendance=0,
        ), 503
    affiliated_records = AffiliatedStudentRecord.query.filter_by(
        college_code=college.college_code,
    ).order_by(AffiliatedStudentRecord.name, AffiliatedStudentRecord.register_number).all()
    user_ids = [record.user_id for record in affiliated_records]
    profiles = Student.query.filter(Student.user_id.in_(user_ids)).all() if user_ids else []
    profile_by_user = {profile.user_id: profile for profile in profiles}
    attendance_by_student = {}
    total_attendance = 0
    present_attendance = 0
    can_view_attendance = has_permission(current_user, 'attendance:read_affiliated')
    if can_view_attendance and profiles:
        attendance_rows = db.session.query(
            Attendance.student_id,
            db.func.count(Attendance.id),
            db.func.coalesce(
                db.func.sum(db.case(
                    (Attendance.status == 'PRESENT', 1),
                    else_=0,
                )),
                0,
            ),
        ).filter(
            Attendance.student_id.in_([profile.id for profile in profiles]),
        ).group_by(Attendance.student_id).all()
        attendance_by_student = {
            student_id: (int(total), int(present))
            for student_id, total, present in attendance_rows
        }
        total_attendance = sum(total for total, _ in attendance_by_student.values())
        present_attendance = sum(present for _, present in attendance_by_student.values())
    roster = []
    for record in affiliated_records:
        profile = profile_by_user.get(record.user_id)
        total, present = attendance_by_student.get(profile.id, (0, 0)) if profile else (0, 0)
        roster.append({
            'record': record,
            'profile': profile,
            'attendance_total': total,
            'attendance_present': present,
            'attendance_percentage': round(present * 100 / total, 2) if total else 0.0,
        })
    return render_template(
        'affiliated_college_portal.html',
        college=college,
        registry_ready=True,
        registration_ready=registration_ready,
        can_register=has_permission(current_user, 'student:register_affiliated'),
        can_edit=has_permission(current_user, 'student:edit_affiliated'),
        can_view_attendance=can_view_attendance,
        roster=roster,
        total_attendance=total_attendance,
        present_attendance=present_attendance,
        registration_preview=registration_preview,
    )


@registries_bp.route('/admin/registries/students', methods=['POST'])
@login_required
def create_student_registration():
    is_college_account = _is_affiliated_college_user(current_user)
    if not _admin_only() and not has_permission(
        current_user, 'student:register_affiliated',
    ):
        return 'Forbidden', 403
    if is_college_account:
        if current_user.must_change_password:
            return redirect(url_for('auth.student_password_setup'))
        assigned_college = _affiliated_college_for_user(current_user)
        if not assigned_college:
            return 'This college account is not linked to an active college.', 403
    else:
        assigned_college = None
        if (
            not _registry_ready('university_students', 'university_student_records')
            or not _registry_ready('affiliated_students', 'affiliated_student_records')
        ):
            flash('Initialize the student registry databases before adding registrations.', 'danger')
            return _student_registration_return()
    if not _registry_ready(None, 'student_registration_contacts'):
        flash('Apply the attendance database migrations before adding student registrations.', 'danger')
        return _student_registration_return()

    data = request.form
    student_type = 'affiliated' if is_college_account else str(
        data.get('student_type', ''),
    ).strip().lower()
    if student_type not in {'university', 'affiliated'}:
        flash('Choose a valid student category.', 'danger')
        return _student_registration_return()
    student_registry_key = (
        'affiliated_students' if student_type == 'affiliated' else 'university_students'
    )
    student_table = (
        'affiliated_student_records'
        if student_type == 'affiliated' else 'university_student_records'
    )
    if not _registry_ready(student_registry_key, student_table):
        flash('The selected student registry database is not initialized.', 'danger')
        return _student_registration_return()
    if student_type == 'affiliated' and not _registry_ready(
        'affiliated_colleges', 'affiliated_college_records',
    ):
        flash('Initialize the affiliated-college registry before adding affiliated students.', 'danger')
        return _student_registration_return()
    roll_number = str(data.get('roll_number', '')).strip().upper()
    if not re.fullmatch(r'[A-Z0-9-]{1,40}', roll_number):
        flash('Roll number must contain 1 to 40 letters, numbers, or hyphens.', 'danger')
        return _student_registration_return()
    college = None
    if student_type == 'affiliated':
        college_code = (
            assigned_college.college_code
            if is_college_account
            else str(data.get('college_code', '')).strip().upper()
        )
        college = assigned_college or AffiliatedCollegeRecord.query.filter_by(
            college_code=college_code, is_active=True,
        ).first()
        if not college:
            flash('Choose a registered affiliated college.', 'danger')
            return _student_registration_return()
        register_number = next_registration_number(college.registration_prefix)
        detail_model = AffiliatedStudentRecord
    else:
        college_code = None
        register_number = next_registration_number('DBU300')
        detail_model = UniversityStudentRecord
    if Student.query.filter_by(register_number=register_number).first():
        flash('The generated registration number is already in use.', 'danger')
        return _student_registration_return()

    values = {}
    for field, maximum in _STUDENT_FIELDS.items():
        value = str(data.get(field, '')).strip()
        if field == 'disability_details' and not value:
            values[field] = None
            continue
        if field != 'disability_details' and not value:
            flash(f'Complete the required student field: {field.replace("_", " ")}.', 'danger')
            return _student_registration_return()
        if len(value) > maximum:
            flash(f'{field.replace("_", " ").capitalize()} must be no more than {maximum} characters.', 'danger')
            return _student_registration_return()
        values[field] = value
    contact_error = _valid_contact(values['email'], values['phone'])
    if contact_error:
        flash(contact_error, 'danger')
        return _student_registration_return()
    if values['gender'] not in _STUDENT_GENDERS:
        flash('Choose a valid gender option.', 'danger')
        return _student_registration_return()
    try:
        birth_date = date.fromisoformat(str(data.get('date_of_birth', '')))
    except ValueError:
        flash('Enter a valid date of birth.', 'danger')
        return _student_registration_return()
    if birth_date >= date.today() or birth_date.year < 1900:
        flash('Date of birth must be a valid date in the past.', 'danger')
        return _student_registration_return()
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))
    if not re.fullmatch(r'[A-Za-z0-9_.@+-]{3,80}', username) or User.query.filter_by(username=username).first():
        flash('Enter a valid, unused login username.', 'danger')
        return _student_registration_return()
    password_error = password_strength_error(password)
    if password_error:
        flash(password_error, 'danger')
        return _student_registration_return()
    if current_app.config.get('STUDENT_EMAIL_VERIFICATION_REQUIRED') and (
        not current_app.config.get('SMTP_HOST') or not current_app.config.get('SMTP_FROM')
    ):
        flash('Student registration is disabled until SMTP_HOST and SMTP_FROM are configured for email verification.', 'danger')
        return _student_registration_return()
    department = values['department']
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=Role.STUDENT.value,
        department_id=None,
        is_enabled=not current_app.config.get('STUDENT_EMAIL_VERIFICATION_REQUIRED'),
        must_change_password=True,
    )
    db.session.add(user)
    db.session.flush()
    verification_time = None if current_app.config.get('STUDENT_EMAIL_VERIFICATION_REQUIRED') else datetime.utcnow()
    detail_values = {
        'user_id': user.id,
        'register_number': register_number,
        'roll_number': roll_number,
        'name': values['name'],
        'email': values['email'],
        'phone': values['phone'],
        'gender': values['gender'],
        'date_of_birth': birth_date,
        'father_name': values['father_name'],
        'mother_name': values['mother_name'],
        'address': values['address'],
        'community': values['community'],
        'is_differently_abled': data.get('is_differently_abled') == 'on',
        'disability_details': values['disability_details'],
        'identification_type': values['identification_type'],
        'identification_verified': data.get('identification_verified') == 'on',
        'email_verified_at': verification_time,
    }
    if student_type == 'affiliated':
        detail_values.update(
            college_code=college.college_code,
            registration_prefix=college.registration_prefix,
        )
    student = Student(
        register_number=register_number,
        roll_number=roll_number,
        name=values['name'],
        email=values['email'],
        phone=values['phone'],
        department=department,
        year=values['year'],
        section=values['section'],
        semester=values['semester'],
        user_id=user.id,
    )
    db.session.add_all([student, detail_model(**detail_values)])
    db.session.add(StudentRegistrationContact(
        user_id=user.id,
        normalized_email=values['email'].casefold(),
        normalized_phone=_normalize_phone(values['phone']),
    ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            'A registration number, email, phone, or login is already in use. '
            'If another registration was submitted at the same time, reload the form and try again.',
            'danger',
        )
        return _student_registration_return()
    if current_app.config.get('STUDENT_EMAIL_VERIFICATION_REQUIRED'):
        try:
            _send_verification_email(user, student)
        except (OSError, smtplib.SMTPException, RuntimeError):
            current_app.logger.warning('Student verification email delivery failed.')
            flash(
                f'Student {register_number} was saved with a disabled account, but email delivery failed. '
                'The university administrator must correct SMTP settings and resend verification.',
                'danger',
            )
            return _student_registration_return()
    flash(
        f'Student registered as {register_number}. '
        + ('A verification email was sent; the account remains disabled until verified.' if current_app.config.get('STUDENT_EMAIL_VERIFICATION_REQUIRED') else 'Email verification was bypassed in test mode.'),
        'success',
    )
    return _student_registration_return()


@registries_bp.route('/admin/registries/students/<int:student_id>/details', methods=['POST'])
@login_required
def edit_student_registration_details(student_id):
    if not _admin_only() and not has_permission(
        current_user, 'student:edit_affiliated',
    ):
        return 'Forbidden', 403
    student = db.session.get(Student, student_id)
    if student is None:
        return 'Student not found.', 404
    detail = None
    if _registry_ready('affiliated_students', 'affiliated_student_records'):
        detail = AffiliatedStudentRecord.query.filter_by(user_id=student.user_id).first()
    if detail is None and _registry_ready('university_students', 'university_student_records'):
        detail = UniversityStudentRecord.query.filter_by(user_id=student.user_id).first()
    if detail is None:
        return 'Student registry details not found.', 404
    if not _admin_only():
        college = _affiliated_college_for_user(current_user)
        if not isinstance(detail, AffiliatedStudentRecord) or not college or (
            detail.college_code != college.college_code
        ):
            return 'Student registry details not found.', 404
    data = request.form
    fields = ('gender', 'father_name', 'mother_name', 'address', 'community', 'identification_type')
    values = {}
    for field in fields:
        value = str(data.get(field, '')).strip()
        if not value or len(value) > _STUDENT_FIELDS[field]:
            flash(f'Enter a valid value for {field.replace("_", " ")}.', 'danger')
            return _student_registration_return()
        values[field] = value
    try:
        birth_date = date.fromisoformat(str(data.get('date_of_birth', '')))
    except ValueError:
        flash('Enter a valid date of birth.', 'danger')
        return _student_registration_return()
    if birth_date >= date.today() or birth_date.year < 1900:
        flash('Date of birth must be a valid date in the past.', 'danger')
        return _student_registration_return()
    disability_details = str(data.get('disability_details', '')).strip()
    if len(disability_details) > _STUDENT_FIELDS['disability_details']:
        flash('Accessibility information must be no more than 300 characters.', 'danger')
        return _student_registration_return()
    for field, value in values.items():
        setattr(detail, field, value)
    detail.date_of_birth = birth_date
    detail.is_differently_abled = data.get('is_differently_abled') == 'on'
    detail.disability_details = disability_details or None
    detail.identification_verified = data.get('identification_verified') == 'on'
    db.session.commit()
    flash('Student registration details updated.', 'success')
    return _student_registration_return()


@registries_bp.route('/admin/registries/students/<int:student_id>/resend-verification', methods=['POST'])
@login_required
def resend_student_verification(student_id):
    if not _admin_only():
        return 'Forbidden', 403
    student = db.session.get(Student, student_id)
    if not student or not student.user or student.user.is_enabled:
        return 'Pending student registration not found.', 404
    detail_model = (
        AffiliatedStudentRecord
        if student.register_number.startswith('DBU3')
        and _registry_ready('affiliated_students', 'affiliated_student_records')
        and AffiliatedStudentRecord.query.filter_by(user_id=student.user_id).first()
        else UniversityStudentRecord
    )
    details = detail_model.query.filter_by(user_id=student.user_id).first()
    if not details or details.email_verified_at is not None:
        return 'Pending student registration not found.', 404
    try:
        _send_verification_email(student.user, student)
    except (OSError, smtplib.SMTPException, RuntimeError):
        current_app.logger.warning('Student verification email delivery failed.')
        flash('Verification email delivery failed. Check SMTP settings before retrying.', 'danger')
        return redirect(url_for('admin.students'))
    flash('Verification email sent again.', 'success')
    return redirect(url_for('admin.students'))


@registries_bp.route('/student/verify-email', methods=['GET', 'POST'])
def verify_student_email():
    if request.method == 'GET':
        return render_template('student_email_verification.html')
    token = str(request.form.get('token', ''))
    try:
        claims = _verification_serializer().loads(
            token,
            max_age=3600 * int(current_app.config['STUDENT_EMAIL_VERIFICATION_LIFETIME_HOURS']),
        )
        user_id = int(claims['user_id'])
        email = str(claims['email'])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        flash('The verification link is invalid or expired.', 'danger')
        return render_template('student_email_verification.html'), 400
    user = db.session.get(User, user_id)
    student = Student.query.filter_by(user_id=user_id).first()
    if not user or not student or student.email.casefold() != email or user.is_enabled:
        flash('The verification link is invalid, expired, or already used.', 'danger')
        return render_template('student_email_verification.html'), 400
    affiliated = (
        AffiliatedStudentRecord.query.filter_by(user_id=user_id).first()
        if _registry_ready('affiliated_students', 'affiliated_student_records')
        else None
    )
    details = affiliated
    if details is None and _registry_ready('university_students', 'university_student_records'):
        details = UniversityStudentRecord.query.filter_by(user_id=user_id).first()
    if not details or details.email.casefold() != email or details.email_verified_at is not None:
        flash('The verification link is invalid, expired, or already used.', 'danger')
        return render_template('student_email_verification.html'), 400
    details.email_verified_at = datetime.utcnow()
    user.is_enabled = True
    db.session.commit()
    flash('Email verified. Your account is active; sign in and set your new password.', 'success')
    return redirect(url_for('auth.login'))


@registries_bp.route('/admin/registries/init-status')
@login_required
def registry_status():
    if not _admin_only():
        return 'Forbidden', 403
    return {
        'university_students': _registry_ready('university_students', 'university_student_records'),
        'affiliated_students': _registry_ready('affiliated_students', 'affiliated_student_records'),
        'affiliated_colleges': _registry_ready('affiliated_colleges', 'affiliated_college_records'),
    }
