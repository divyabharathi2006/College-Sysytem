from datetime import datetime
import re

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from extensions import db
from attendance_engine import calculate_percentage, risk_level
from models.attendance import Attendance
from models.academic import Batch, ClassSession, Course, Department, Section
from models.attendance import AttendanceCorrectionRequest, LeaveRequest
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from models.user import User
from models.audit import SecurityEvent
from models.user_session import UserSession
from models.registry import (
    AffiliatedCollegeRecord, AffiliatedStudentRecord, StudentRegistrationContact,
    UniversityStudentRecord,
)
from registry_store import registry_table_ready
from registration_numbers import next_registration_number
from permissions import (
    AFFILIATED_COLLEGE_ROLES, Role, can_access, department_id_for, has_role,
    normalize_role,
)
from security import hash_password, invalidate_password_reset_tokens, password_strength_error, revoke_user_sessions
from attendance_imports import (
    ImportValidationError, commit_import_stage, create_import_stage,
    expire_stage, import_preview_data, validate_csv_upload,
)
from models.operational import AttendanceImportStage, SystemPolicy
from operational_controls import POLICY_LABELS, confirmation_phrase, policy_states

admin_bp = Blueprint('admin', __name__)


def _calculate_overall_student_stats(student):
    records = Attendance.query.filter_by(student_id=student.id).all()
    total = len(records)
    present = sum(1 for item in records if item.status == 'PRESENT')
    percentage = calculate_percentage(present, total)
    return {'present': present, 'total': total, 'percentage': percentage, 'risk': risk_level(percentage)}


def _admin_payload():
    return request.get_json(silent=True) or request.form


def _user_deletion_error(user):
    if not user:
        return None
    if user.id == current_user.id:
        return 'You cannot delete your own administrator account.'
    if has_role(user, Role.SUPER_ADMIN) and user.is_enabled and sum(
        has_role(item, Role.SUPER_ADMIN) and item.is_enabled for item in User.query.all()
    ) <= 1:
        return 'The last administrator cannot be deleted.'
    return None


def _is_super_admin():
    return has_role(current_user, Role.SUPER_ADMIN)


def _student_search_query(search_text=''):
    query = Student.query
    needle = str(search_text or '').strip()[:100]
    if needle:
        pattern = f'%{needle}%'
        query = query.filter(db.or_(
            Student.name.ilike(pattern),
            Student.roll_number.ilike(pattern),
            Student.phone.ilike(pattern),
        ))
    return query.order_by(Student.name, Student.id)


def _student_pagination(search_text='', *, page=1, per_page=25):
    return _student_search_query(search_text).paginate(
        page=max(1, page or 1), per_page=per_page, error_out=False,
    )


def _valid_student_email(value):
    local, separator, domain = value.partition('@')
    return bool(separator and local and domain and '.' in domain and not any(char.isspace() for char in value))


def _normalized_student_phone(value):
    digits = ''.join(character for character in str(value) if character.isdigit())
    return digits or str(value).strip().casefold()


def _student_management_redirect():
    search_text = str(request.form.get('return_q', '') or '')[:100]
    return redirect(url_for('admin.students', q=search_text) if search_text else url_for('admin.students'))


def _security_checks():
    config = current_app.config
    storage = str(config.get('RATELIMIT_STORAGE_URI', 'memory://'))
    return [
        {'label': 'Password hashing', 'status': 'Configured', 'detail': 'Argon2id for new and upgraded passwords.'},
        {'label': 'CSRF protection', 'status': 'Enabled' if config.get('WTF_CSRF_ENABLED') else 'Disabled', 'detail': 'Flask-WTF request protection.'},
        {'label': 'Session cookie Secure', 'status': 'Enabled' if config.get('SESSION_COOKIE_SECURE') else 'Disabled', 'detail': 'Enable behind HTTPS for production.'},
        {'label': 'Session cookie HttpOnly / SameSite', 'status': 'Configured' if config.get('SESSION_COOKIE_HTTPONLY') and config.get('SESSION_COOKIE_SAMESITE') else 'Review', 'detail': str(config.get('SESSION_COOKIE_SAMESITE'))},
        {'label': 'Session lifetime', 'status': 'Configured', 'detail': f"{int(config['PERMANENT_SESSION_LIFETIME'].total_seconds())} seconds."},
        {'label': 'Idle session timeout', 'status': 'Configured', 'detail': f"{config['SESSION_IDLE_TIMEOUT_MINUTES']} minutes without activity."},
        {'label': 'Request limiting', 'status': 'Enabled', 'detail': 'HMAC-obscured account/IP keys.'},
        {'label': 'Shared limiter storage', 'status': 'Configured' if storage and not storage.startswith('memory://') else 'Local memory only', 'detail': 'Shared storage is required when running multiple workers.'},
        {'label': 'Security event history', 'status': 'Available', 'detail': 'Database-backed, append-only event records.'},
        {'label': 'Content Security Policy', 'status': 'Enforced' if config.get('SECURITY_HEADERS_ENABLED') and config.get('CONTENT_SECURITY_POLICY') else 'Disabled', 'detail': 'unsafe-inline remains enabled for current templates; use nonces/external scripts to remove it.'},
        {'label': 'HSTS', 'status': 'Active for HTTPS responses' if request.is_secure and config.get('SECURITY_HSTS_MAX_AGE') else 'HTTPS only / not active on this request', 'detail': 'Never emitted on HTTP responses.'},
        {'label': 'Trusted proxy headers', 'status': 'Configured' if config.get('TRUSTED_PROXY_HOPS') else 'Not trusted', 'detail': f"Configured hops: {config.get('TRUSTED_PROXY_HOPS', 0)}."},
    ]


def _profile_department_id(value, department_text):
    if value not in (None, ''):
        try:
            department = db.session.get(Department, int(value))
        except (TypeError, ValueError):
            return False
        return department.id if department else False
    text = str(department_text or '').strip()
    if not text:
        return None
    matches = Department.query.filter(db.or_(
        db.func.lower(db.func.trim(Department.code)) == text.casefold(),
        db.func.lower(db.func.trim(Department.name)) == text.casefold(),
    )).all()
    return matches[0].id if len(matches) == 1 else None


def _attendance_values(data, default_session_id=None):
    try:
        student_id = int(data.get('student_id', ''))
        subject_id = int(data.get('subject_id', ''))
        record_date = datetime.strptime(data.get('date', ''), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None
    status = str(data.get('status', '')).upper()
    if status not in {'PRESENT', 'ABSENT', 'LATE', 'EXCUSED', 'OD', 'LEAVE'}:
        return None
    student = db.session.get(Student, student_id)
    if not student or not db.session.get(Subject, subject_id):
        return None
    session_value = data.get('session_id', default_session_id)
    try:
        session_id = int(session_value) if session_value not in (None, '') else None
    except (TypeError, ValueError):
        return None
    if session_id is not None:
        class_session = db.session.get(ClassSession, session_id)
        if (
            not class_session or class_session.subject_id != subject_id
            or class_session.weekday != record_date.weekday()
            or student.section_id != class_session.section_id
        ):
            return None
    return student_id, subject_id, record_date, status, session_id


@admin_bp.route('/dashboard')
@login_required
def dashboard():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    per_student_attendance = db.session.query(
        Student.id.label('student_id'), Student.department.label('department'),
        db.func.count(Attendance.id).label('total'),
        db.func.coalesce(db.func.sum(db.case((Attendance.status == 'PRESENT', 1), else_=0)), 0).label('present'),
    ).outerjoin(Attendance, Attendance.student_id == Student.id).group_by(
        Student.id, Student.department,
    ).subquery()
    aggregate_rows = db.session.query(
        per_student_attendance.c.department,
        per_student_attendance.c.total,
        per_student_attendance.c.present,
        db.func.count(per_student_attendance.c.student_id),
    ).group_by(
        per_student_attendance.c.department,
        per_student_attendance.c.total,
        per_student_attendance.c.present,
    ).all()
    total_students = sum(int(row[3]) for row in aggregate_rows)
    percentages_by_department = {}
    weighted_percentages = []
    distribution = {'Excellent': 0, 'Good': 0, 'Warning': 0, 'Critical': 0}
    above_75 = below_75 = critical = 0
    for department, total, present, student_count in aggregate_rows:
        student_count = int(student_count)
        percentage = calculate_percentage(int(present), int(total))
        percentages_by_department.setdefault(department, [0, 0.0])
        percentages_by_department[department][0] += student_count
        percentages_by_department[department][1] += percentage * student_count
        weighted_percentages.append(percentage * student_count)
        above_75 += student_count if percentage >= 75 else 0
        below_75 += student_count if percentage < 75 else 0
        critical += student_count if percentage < 65 else 0
        if percentage >= 90:
            distribution['Excellent'] += student_count
        elif percentage >= 80:
            distribution['Good'] += student_count
        elif percentage >= 65:
            distribution['Warning'] += student_count
        else:
            distribution['Critical'] += student_count
    average = round(sum(weighted_percentages) / total_students, 2) if total_students else 0
    total_classes = Attendance.query.count()
    department_stats = [
        {
            'department': department,
            'average': round(weighted / student_count, 2) if student_count else 0,
        }
        for department, (student_count, weighted) in sorted(
            percentages_by_department.items(), key=lambda item: item[0] or '',
        )
    ]

    subject_stats = []
    for subject in Subject.query.all():
        records = Attendance.query.filter_by(subject_id=subject.id).all()
        if not records:
            continue
        present = sum(1 for item in records if item.status == 'PRESENT')
        subject_stats.append({'name': subject.subject_name, 'percentage': calculate_percentage(present, len(records))})

    attendance_pagination = Attendance.query.order_by(
        Attendance.date.desc(), Attendance.id.desc()
    ).paginate(page=request.args.get('attendance_page', 1, type=int), per_page=100, error_out=False)
    search_text = request.args.get('student_search', '')[:100]
    student_pagination = _student_pagination(
        search_text, page=request.args.get('student_page', 1, type=int), per_page=25,
    )
    users_pagination = User.query.order_by(User.username).paginate(
        page=max(1, request.args.get('user_page', 1, type=int) or 1),
        per_page=50, error_out=False,
    )

    return render_template(
        'admin_dashboard.html',
        total_students=total_students,
        average_attendance=average,
        students_above_75=above_75,
        students_below_75=below_75,
        critical_students=critical,
        classes_conducted=total_classes,
        distribution=distribution,
        department_stats=department_stats,
        subject_stats=subject_stats,
        students=student_pagination.items,
        student_pagination=student_pagination,
        student_search=search_text,
        faculty_members=Faculty.query.order_by(Faculty.name).all(),
        subjects=Subject.query.order_by(Subject.subject_code).all(),
        departments=Department.query.order_by(Department.code).all(),
        users=users_pagination.items,
        users_pagination=users_pagination,
        attendance_records=attendance_pagination.items,
        attendance_pagination=attendance_pagination,
        class_sessions=ClassSession.query.order_by(ClassSession.weekday, ClassSession.starts_at).all(),
    )


@admin_bp.route('/management/dashboard')
@login_required
def management_dashboard():
    if not can_access(current_user, 'analytics:read') or not has_role(current_user, Role.MANAGEMENT):
        return jsonify({'error': 'Forbidden'}), 403
    departments = Department.query.order_by(Department.code).all()
    student_attendance = db.session.query(
        Student.id.label('student_id'), Department.code.label('department_code'),
        Student.department.label('department_text'),
        db.func.count(Attendance.id).label('total'),
        db.func.coalesce(db.func.sum(db.case((Attendance.status == 'PRESENT', 1), else_=0)), 0).label('present'),
    ).outerjoin(Department, Student.department_id == Department.id).outerjoin(
        Attendance, Attendance.student_id == Student.id,
    ).group_by(Student.id, Department.code, Student.department).subquery()
    label = db.func.coalesce(student_attendance.c.department_code, student_attendance.c.department_text, 'Unassigned')
    attendance_buckets = db.session.query(
        label, student_attendance.c.total, student_attendance.c.present,
        db.func.count(student_attendance.c.student_id),
    ).group_by(label, student_attendance.c.total, student_attendance.c.present).all()
    grouped = {}
    weighted_percentages = []
    student_count = 0
    for department_label, total, present, count in attendance_buckets:
        count = int(count)
        percentage = calculate_percentage(int(present), int(total))
        grouped.setdefault(department_label, [0, 0.0])
        grouped[department_label][0] += count
        grouped[department_label][1] += percentage * count
        student_count += count
        weighted_percentages.append(percentage * count)
    department_stats = [
        {'department': department, 'students': count, 'average': round(weighted / count, 2) if count else 0}
        for department, (count, weighted) in sorted(grouped.items())
    ]
    return render_template(
        'management_dashboard.html',
        department_count=len(departments),
        course_count=Department.query.join(Department.courses).count(),
        student_count=student_count,
        attendance_count=Attendance.query.count(),
        average_attendance=round(sum(weighted_percentages) / student_count, 2) if student_count else 0,
        department_stats=department_stats,
    )


def _department_dashboard_data(department_id):
    department = db.session.get(Department, department_id)
    if not department:
        return None
    department_values = set()
    for value in (department.code.strip().casefold(), department.name.strip().casefold()):
        matches = Department.query.filter(db.or_(
            db.func.lower(db.func.trim(Department.code)) == value,
            db.func.lower(db.func.trim(Department.name)) == value,
        )).all()
        if len(matches) == 1 and matches[0].id == department.id:
            department_values.add(value)
    student_scope = db.or_(
        Student.department_id == department.id,
        db.and_(
            Student.department_id.is_(None),
            db.func.lower(db.func.trim(Student.department)).in_(department_values),
        ),
    )
    faculty_scope = db.or_(
        Faculty.department_id == department.id,
        db.and_(
            Faculty.department_id.is_(None),
            db.func.lower(db.func.trim(Faculty.department)).in_(department_values),
        ),
    )
    students = Student.query.outerjoin(User, Student.user_id == User.id).filter(
        student_scope, db.or_(User.department_id.is_(None), User.department_id == department.id),
    ).order_by(Student.name).all()
    faculty = Faculty.query.outerjoin(User, Faculty.user_id == User.id).filter(
        faculty_scope, db.or_(User.department_id.is_(None), User.department_id == department.id),
    ).order_by(Faculty.name).all()
    subjects = Subject.query.filter(
        db.func.lower(db.func.trim(Subject.department)).in_(department_values),
    ).order_by(Subject.subject_name).all()
    student_ids = [item.id for item in students]
    subject_ids = [item.id for item in subjects]
    sessions = []
    if subject_ids:
        sessions = ClassSession.query.join(Section).join(Batch).join(Course).filter(
            Course.department_id == department.id,
            ClassSession.subject_id.in_(subject_ids),
        ).order_by(ClassSession.weekday, ClassSession.starts_at).all()
    session_by_id = {item.id: item for item in sessions}
    records = []
    if student_ids and subject_ids:
        candidates = Attendance.query.filter(
            Attendance.student_id.in_(student_ids), Attendance.subject_id.in_(subject_ids),
        ).order_by(Attendance.date.desc(), Attendance.id.desc()).all()
        for record in candidates:
            if record.session_id is None:
                records.append(record)
                continue
            class_session = session_by_id.get(record.session_id)
            if (
                class_session and class_session.subject_id == record.subject_id
                and class_session.section_id == record.student.section_id
                and class_session.weekday == record.date.weekday()
            ):
                records.append(record)

    records_by_student = {}
    for record in records:
        records_by_student.setdefault(record.student_id, []).append(record)
    student_stats = []
    percentages = []
    at_risk_students = []
    defaulter_students = []
    for student in students:
        student_records = records_by_student.get(student.id, [])
        present = sum(item.status == 'PRESENT' for item in student_records)
        percentage = calculate_percentage(present, len(student_records))
        stat = {
            'student': student, 'present': present, 'total': len(student_records),
            'percentage': percentage, 'risk': risk_level(percentage),
        }
        student_stats.append(stat)
        if student_records:
            percentages.append(percentage)
        if percentage < 75:
            at_risk_students.append(stat)
        if percentage < 65:
            defaulter_students.append(stat)

    records_by_subject = {}
    for record in records:
        records_by_subject.setdefault(record.subject_id, []).append(record)
    subject_stats = []
    for subject in subjects:
        subject_records = records_by_subject.get(subject.id, [])
        subject_stats.append({
            'name': subject.subject_name,
            'present': sum(item.status == 'PRESENT' for item in subject_records),
            'total': len(subject_records),
            'percentage': calculate_percentage(
                sum(item.status == 'PRESENT' for item in subject_records), len(subject_records),
            ),
        })

    section_groups = {}
    class_groups = {}
    faculty_groups = {item.id: [] for item in faculty}
    faculty_session_ids = {}
    for class_session in sessions:
        section = class_session.section
        batch = section.batch
        course = batch.course
        section_groups.setdefault(section.id, {
            'section': section, 'batch': batch, 'course': course, 'records': [],
        })
        class_key = (course.id, batch.id)
        class_groups.setdefault(class_key, {
            'course': course, 'batch': batch, 'records': [],
        })
        subject = class_session.subject
        if class_session.faculty_id in faculty_groups and subject.faculty_id == class_session.faculty_id:
            faculty_session_ids.setdefault(class_session.faculty_id, set()).add(class_session.id)
    for record in records:
        class_session = session_by_id.get(record.session_id)
        if not class_session:
            continue
        section_groups[class_session.section_id]['records'].append(record)
        class_groups[(class_session.section.batch.course.id, class_session.section.batch.id)]['records'].append(record)
        if record.session_id in faculty_session_ids.get(class_session.faculty_id, set()):
            faculty_groups[class_session.faculty_id].append(record)

    def summarize(records_for_group):
        present = sum(item.status == 'PRESENT' for item in records_for_group)
        return {
            'present': present,
            'total': len(records_for_group),
            'percentage': calculate_percentage(present, len(records_for_group)),
        }

    section_stats = []
    for group in section_groups.values():
        section_stats.append({
            'label': f"{group['course'].code} · {group['batch'].code} · {group['section'].code}",
            **summarize(group['records']),
        })
    class_stats = []
    for group in class_groups.values():
        class_stats.append({
            'label': f"{group['course'].code} · {group['batch'].code}",
            **summarize(group['records']),
        })
    faculty_stats = []
    for member in faculty:
        member_records = faculty_groups[member.id]
        faculty_stats.append({
            'name': member.name,
            'sessions': len(faculty_session_ids.get(member.id, set())),
            **summarize(member_records),
        })

    attendance_ids = [item.id for item in records]
    pending_leave_count = LeaveRequest.query.filter(
        LeaveRequest.student_id.in_(student_ids), LeaveRequest.status == 'PENDING',
    ).count() if student_ids else 0
    pending_correction_count = AttendanceCorrectionRequest.query.filter(
        AttendanceCorrectionRequest.attendance_id.in_(attendance_ids),
        AttendanceCorrectionRequest.status == 'PENDING',
    ).count() if attendance_ids else 0
    return {
        'department': department,
        'student_count': len(students),
        'faculty_count': len(faculty),
        'subject_count': len(subjects),
        'course_count': Course.query.filter_by(department_id=department.id).count(),
        'section_count': Section.query.join(Batch).join(Course).filter(
            Course.department_id == department.id,
        ).count(),
        'attendance_count': len(records),
        'average_attendance': round(sum(percentages) / len(percentages), 2) if percentages else 0.0,
        'pending_leave_count': pending_leave_count,
        'pending_correction_count': pending_correction_count,
        'subject_stats': subject_stats,
        'class_stats': class_stats,
        'section_stats': section_stats,
        'faculty_stats': faculty_stats,
        'at_risk_students': at_risk_students,
        'defaulter_students': defaulter_students,
    }


@admin_bp.route('/hod/dashboard')
@login_required
def hod_dashboard():
    department_id = department_id_for(current_user)
    if (
        not has_role(current_user, Role.HOD)
        or not department_id
        or not can_access(current_user, 'analytics:read', department_id=department_id)
    ):
        return jsonify({'error': 'Forbidden'}), 403
    context = _department_dashboard_data(department_id)
    if context is None:
        return jsonify({'error': 'A valid department assignment is required.'}), 403
    return render_template('department_dashboard.html', is_hod=True, **context)


@admin_bp.route('/department/dashboard')
@login_required
def department_dashboard():
    department_id = department_id_for(current_user)
    if (
        not has_role(current_user, Role.DEPARTMENT_ADMIN)
        or not department_id
        or not can_access(current_user, 'department:read', department_id=department_id)
    ):
        return jsonify({'error': 'Forbidden'}), 403
    context = _department_dashboard_data(department_id)
    if context is None:
        return jsonify({'error': 'A valid department assignment is required.'}), 403
    for key in (
        'pending_leave_count', 'pending_correction_count', 'subject_stats', 'class_stats',
        'section_stats', 'faculty_stats', 'at_risk_students', 'defaulter_students',
    ):
        context.pop(key, None)
    return render_template('department_dashboard.html', is_hod=False, **context)


@admin_bp.route('/security')
@login_required
def security_center():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    pagination = SecurityEvent.query.order_by(
        SecurityEvent.created_at.desc(), SecurityEvent.id.desc(),
    ).paginate(page=request.args.get('page', 1, type=int), per_page=50, error_out=False)
    return render_template(
        'security_center.html', events=pagination.items,
        pagination=pagination, checks=_security_checks(), policies=policy_states(),
        policy_labels=POLICY_LABELS, confirmation_phrase=confirmation_phrase,
        users=User.query.order_by(User.username).all(),
    )


@admin_bp.route('/security/controls/<string:policy_key>', methods=['POST'])
@login_required
def update_operational_policy(policy_key):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    if policy_key not in POLICY_LABELS:
        return jsonify({'error': 'Unknown operational control.'}), 404
    data = _admin_payload()
    raw_enabled = data.get('enabled')
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    elif str(raw_enabled).strip().lower() in {'true', '1', 'on'}:
        enabled = True
    elif str(raw_enabled).strip().lower() in {'false', '0', 'off'}:
        enabled = False
    else:
        return jsonify({'error': 'Choose an enabled or disabled state.'}), 400
    expected = confirmation_phrase(policy_key, enabled)
    if str(data.get('confirmation_phrase', '')).strip() != expected:
        return jsonify({'error': f'Type the confirmation phrase: {expected}'}), 400
    policy = db.session.get(SystemPolicy, policy_key)
    if policy is None:
        policy = SystemPolicy(key=policy_key)
        db.session.add(policy)
    policy.value = 'true' if enabled else 'false'
    policy.updated_by = current_user.id
    policy.updated_at = datetime.utcnow()
    db.session.commit()
    if request.is_json:
        return jsonify({'key': policy_key, 'enabled': enabled}), 200
    flash(f'{POLICY_LABELS[policy_key]} is now {"enabled" if enabled else "disabled"}.', 'success')
    return redirect(url_for('admin.security_center'))


@admin_bp.route('/security/controls/revoke-all-sessions', methods=['POST'])
@login_required
def revoke_all_sessions():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    if str(_admin_payload().get('confirmation_phrase', '')).strip() != 'REVOKE ALL SESSIONS':
        return jsonify({'error': 'Type the confirmation phrase: REVOKE ALL SESSIONS'}), 400
    revoked_count = UserSession.query.filter(UserSession.revoked_at.is_(None)).update(
        {UserSession.revoked_at: datetime.utcnow()}, synchronize_session=False,
    )
    db.session.commit()
    if request.is_json:
        return jsonify({'revoked_sessions': revoked_count}), 200
    flash(f'Revoked {revoked_count} server-side session(s). Sign in again to continue.', 'success')
    return redirect(url_for('admin.security_center'))


@admin_bp.route('/accounts/security')
@login_required
def accounts_security():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    now = datetime.utcnow()
    active_sessions = UserSession.query.filter(
        UserSession.revoked_at.is_(None), UserSession.expires_at > now,
    ).order_by(UserSession.created_at.desc()).all()
    sessions_by_user = {}
    for auth_session in active_sessions:
        sessions_by_user.setdefault(auth_session.user_id, []).append(auth_session)
    return render_template(
        'accounts_security.html',
        users=User.query.order_by(User.username).all(),
        sessions_by_user=sessions_by_user,
    )


@admin_bp.route('/users/<int:user_id>/status', methods=['POST'])
@login_required
def set_user_status(user_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    raw_enabled = _admin_payload().get('is_enabled')
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    elif str(raw_enabled).strip().lower() in {'true', '1', 'enabled'}:
        enabled = True
    elif str(raw_enabled).strip().lower() in {'false', '0', 'disabled'}:
        enabled = False
    else:
        return jsonify({'error': 'Choose enabled or disabled status.'}), 400
    if not enabled and user.id == current_user.id:
        return jsonify({'error': 'You cannot disable your own account.'}), 400
    if not enabled and str(_admin_payload().get('confirmation_phrase', '')).strip() != f'DISABLE ACCOUNT {user.id}':
        return jsonify({'error': f'Type the confirmation phrase: DISABLE ACCOUNT {user.id}'}), 400
    if (
        not enabled and user.is_enabled and has_role(user, Role.SUPER_ADMIN)
        and sum(has_role(item, Role.SUPER_ADMIN) and item.is_enabled for item in User.query.all()) <= 1
    ):
        return jsonify({'error': 'The last enabled Super Admin cannot be disabled.'}), 400
    was_enabled = user.is_enabled
    user.is_enabled = enabled
    if not enabled or not was_enabled:
        revoke_user_sessions(user.id)
        invalidate_password_reset_tokens(user.id)
    db.session.commit()
    flash('Account enabled.' if enabled else 'Account disabled and sessions revoked.', 'success')
    return redirect(url_for('admin.accounts_security'))


@admin_bp.route('/users/<int:user_id>/sessions/revoke', methods=['POST'])
@login_required
def revoke_user_sessions_route(user_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    revoked_count = revoke_user_sessions(user.id)
    db.session.commit()
    flash(f'Revoked {revoked_count} session(s) for {user.username}.', 'success')
    return redirect(url_for('admin.accounts_security'))


@admin_bp.route('/sessions/<int:session_id>/revoke', methods=['POST'])
@login_required
def revoke_session_route(session_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    auth_session = db.session.get(UserSession, session_id)
    if not auth_session:
        return jsonify({'error': 'Session not found'}), 404
    if auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.utcnow()
        db.session.commit()
    flash('Session revoked.', 'success')
    return redirect(url_for('admin.accounts_security'))


@admin_bp.route('/students')
@admin_bp.route('/students', methods=['POST'])
@login_required
def students():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    if request.method == 'GET':
        search_text = request.args.get('q', '')[:100]
        pagination = _student_pagination(
            search_text, page=request.args.get('page', 1, type=int), per_page=50,
        )
        college_store_ready = registry_table_ready(
            'affiliated_colleges', 'affiliated_college_records',
        )
        university_store_ready = registry_table_ready(
            'university_students', 'university_student_records',
        )
        affiliated_store_ready = registry_table_ready(
            'affiliated_students', 'affiliated_student_records',
        )
        contact_store_ready = registry_table_ready(
            None, 'student_registration_contacts',
        )
        page_user_ids = [student.user_id for student in pagination.items]
        registration_details = {}
        if page_user_ids and university_store_ready:
            registration_details.update({
                item.user_id: item for item in UniversityStudentRecord.query.filter(
                    UniversityStudentRecord.user_id.in_(page_user_ids),
                ).all()
            })
        if page_user_ids and affiliated_store_ready:
            registration_details.update({
                item.user_id: item for item in AffiliatedStudentRecord.query.filter(
                    AffiliatedStudentRecord.user_id.in_(page_user_ids),
                ).all()
            })
        colleges = (
            AffiliatedCollegeRecord.query.filter_by(is_active=True)
            .order_by(AffiliatedCollegeRecord.name).all()
            if college_store_ready else []
        )
        registration_previews = {
            'university': next_registration_number('DBU300'),
            'colleges': {
                college.college_code: next_registration_number(college.registration_prefix)
                for college in colleges
            },
        }
        return render_template(
            'student_management.html', students=pagination.items,
            pagination=pagination, search_text=search_text,
            registration_details=registration_details,
            colleges=colleges,
            registration_previews=registration_previews,
            registry_ready=university_store_ready and affiliated_store_ready and college_store_ready,
            registration_ready=(
                university_store_ready and affiliated_store_ready and college_store_ready
                and contact_store_ready
            ),
            contact_store_ready=contact_store_ready,
        )
    flash('Use the verified student registration form so contact details and registry records are validated.', 'danger')
    return redirect(url_for('admin.students'))


@admin_bp.route('/students/<int:student_id>/edit', methods=['POST'])
@login_required
def edit_student(student_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    data = request.get_json(silent=True) or request.form
    if 'department_id' in data:
        department_id = _profile_department_id(data.get('department_id'), data.get('department', student.department))
    elif 'department' in data and str(data.get('department', '')).strip() != student.department:
        department_id = _profile_department_id(None, data.get('department'))
    else:
        department_id = student.department_id
    if department_id is False:
        flash('Select a valid department.', 'danger')
        return redirect(url_for('admin.dashboard'))
    user = db.session.get(User, student.user_id)
    if not user:
        flash('The student account is unavailable.', 'danger')
        return redirect(url_for('admin.dashboard'))
    registration_details = []
    if registry_table_ready('university_students', 'university_student_records'):
        registration_details.extend(UniversityStudentRecord.query.filter_by(user_id=user.id).all())
    if registry_table_ready('affiliated_students', 'affiliated_student_records'):
        registration_details.extend(AffiliatedStudentRecord.query.filter_by(user_id=user.id).all())
    requested_id = data.get('student_id', data.get('register_number'))
    normalized_id = None
    if requested_id is not None:
        requested_id = str(requested_id).strip()
        if not requested_id or len(requested_id) > 50:
            flash('Student ID must contain 1 to 50 characters.', 'danger')
            return _student_management_redirect()
        duplicate_profile = None if registration_details else Student.query.filter(
            Student.register_number == requested_id, Student.id != student.id,
        ).first()
        duplicate_user = User.query.filter(
            User.username == requested_id, User.id != user.id,
        ).first()
        if duplicate_profile or duplicate_user:
            flash('Student ID is already in use.', 'danger')
            return _student_management_redirect()
        normalized_id = requested_id

    values = {}
    limits = {
        'name': 120, 'email': 120, 'phone': 30, 'department': 80,
        'year': 20, 'section': 20, 'semester': 20,
    }
    for field, maximum in limits.items():
        if data.get(field) is not None:
            value = str(data[field]).strip()
            if not value or len(value) > maximum:
                flash(f'Enter a value for {field} within {maximum} characters.', 'danger')
                return _student_management_redirect()
            values[field] = value
    if 'email' in values and not _valid_student_email(values['email']):
        flash('Enter a valid email address.', 'danger')
        return _student_management_redirect()
    if 'email' in values or 'phone' in values:
        email = values.get('email', student.email)
        phone = values.get('phone', student.phone)
        phone_digits = ''.join(character for character in phone if character.isdigit())
        normalized_phone = phone_digits or phone.strip().casefold()
        claims_ready = registry_table_ready(None, 'student_registration_contacts')
        contact_claim = (
            StudentRegistrationContact.query.filter_by(user_id=student.user_id).first()
            if claims_ready else None
        )
        if contact_claim and not 7 <= len(phone_digits) <= 15:
            flash('Registered student phone numbers must contain 7 to 15 digits.', 'danger')
            return _student_management_redirect()
        if any(
            profile.email.strip().casefold() == email.casefold()
            or _normalized_student_phone(profile.phone) == normalized_phone
            for profile in Student.query.filter(Student.id != student.id).all()
        ):
            flash('That email address or phone number is already registered to another student.', 'danger')
            return _student_management_redirect()
        claim_conflict = (
            StudentRegistrationContact.query.filter(
                StudentRegistrationContact.user_id != student.user_id,
                db.or_(
                    StudentRegistrationContact.normalized_email == email.casefold(),
                    StudentRegistrationContact.normalized_phone == normalized_phone,
                ),
            ).first()
            if claims_ready else None
        )
        if claim_conflict:
            flash('That email address or phone number is already reserved by another student.', 'danger')
            return _student_management_redirect()
    roll_number = data.get('roll_number')
    if roll_number is not None:
        roll_number = str(roll_number).strip().upper() or None
        if roll_number is not None and len(roll_number) > 50:
            flash('Roll number must be no more than 50 characters.', 'danger')
            return _student_management_redirect()
        if registration_details and (
            not roll_number or not re.fullmatch(r'[A-Z0-9-]{1,40}', roll_number)
        ):
            flash('Registered student roll numbers must contain 1 to 40 letters, numbers, or hyphens.', 'danger')
            return _student_management_redirect()

    if normalized_id is not None:
        if not registration_details:
            student.register_number = normalized_id
        user.username = normalized_id
    if roll_number is not None or 'roll_number' in data:
        student.roll_number = roll_number
        if registration_details:
            for detail in registration_details:
                detail.roll_number = roll_number
    student.department_id = department_id
    user.department_id = department_id
    changed_email = 'email' in values and values['email'].casefold() != student.email.casefold()
    if changed_email:
        user.is_enabled = False
        user.must_change_password = True
        revoke_user_sessions(user.id)
        invalidate_password_reset_tokens(user.id)
    for detail in registration_details:
        if 'name' in values:
            detail.name = values['name']
        if 'email' in values:
            detail.email = values['email']
        if 'phone' in values:
            detail.phone = values['phone']
        if changed_email:
            detail.email_verified_at = None
    contact_claim = (
        StudentRegistrationContact.query.filter_by(user_id=user.id).first()
        if registry_table_ready(None, 'student_registration_contacts') else None
    )
    if contact_claim:
        contact_claim.normalized_email = values.get('email', student.email).casefold()
        contact_claim.normalized_phone = ''.join(
            character for character in values.get('phone', student.phone) if character.isdigit()
        )
    for field, value in values.items():
        setattr(student, field, value)
    db.session.commit()
    if request.is_json:
        return jsonify({'message': 'Student updated'})
    flash(
        'Student updated. The account was disabled because the email changed; resend verification to reactivate it.'
        if changed_email else 'Student updated successfully.',
        'success',
    )
    return _student_management_redirect()


@admin_bp.route('/students/<int:student_id>/delete', methods=['POST'])
@login_required
def delete_student(student_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    user = db.session.get(User, student.user_id)
    deletion_error = _user_deletion_error(user)
    if deletion_error:
        flash(deletion_error, 'danger')
        return redirect(url_for('admin.dashboard'))
    Attendance.query.filter_by(student_id=student.id).delete()
    if registry_table_ready('university_students', 'university_student_records'):
        UniversityStudentRecord.query.filter_by(user_id=student.user_id).delete()
    if registry_table_ready('affiliated_students', 'affiliated_student_records'):
        AffiliatedStudentRecord.query.filter_by(user_id=student.user_id).delete()
    if user:
        Attendance.query.filter_by(marked_by=user.id).update({Attendance.marked_by: None})
    db.session.delete(student)
    if user:
        if registry_table_ready(None, 'student_registration_contacts'):
            StudentRegistrationContact.query.filter_by(user_id=user.id).delete()
        revoke_user_sessions(user.id)
        invalidate_password_reset_tokens(user.id)
        db.session.delete(user)
    db.session.commit()
    flash('Student and associated attendance records deleted.', 'success')
    return _student_management_redirect()


@admin_bp.route('/faculty', methods=['POST'])
@login_required
def create_faculty():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = _admin_payload()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    employee_id = data.get('employee_id', '').strip().upper()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    department = data.get('department', '').strip()
    if not all((username, password, employee_id, name, email, department)):
        flash('Complete all faculty fields, including login credentials.', 'danger')
        return redirect(url_for('admin.dashboard'))
    if User.query.filter_by(username=username).first() or Faculty.query.filter_by(employee_id=employee_id).first():
        flash('Username or employee ID already exists.', 'danger')
        return redirect(url_for('admin.dashboard'))
    department_id = _profile_department_id(data.get('department_id'), department)
    if department_id is False:
        flash('Select a valid department.', 'danger')
        return redirect(url_for('admin.dashboard'))
    user = User(username=username, password_hash=hash_password(password), role='faculty', department_id=department_id)
    db.session.add(user)
    db.session.flush()
    db.session.add(Faculty(employee_id=employee_id, name=name, email=email, department=department, department_id=department_id, user_id=user.id))
    db.session.commit()
    flash('Faculty account and profile created.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/faculty/<int:faculty_id>/edit', methods=['POST'])
@login_required
def edit_faculty(faculty_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({'error': 'Faculty profile not found'}), 404
    data = _admin_payload()
    department_text = data.get('department', faculty.department)
    department_id = _profile_department_id(data.get('department_id'), department_text)
    if department_id is False:
        flash('Select a valid department.', 'danger')
        return redirect(url_for('admin.dashboard'))
    faculty.department_id = department_id
    faculty_user = db.session.get(User, faculty.user_id)
    if faculty_user:
        faculty_user.department_id = department_id
    employee_id = data.get('employee_id', faculty.employee_id).strip().upper()
    duplicate = Faculty.query.filter(Faculty.employee_id == employee_id, Faculty.id != faculty.id).first()
    if duplicate:
        flash('Employee ID already exists.', 'danger')
        return redirect(url_for('admin.dashboard'))
    faculty.employee_id = employee_id
    for field in ('name', 'email', 'department'):
        if data.get(field) is not None:
            setattr(faculty, field, data[field].strip())
    db.session.commit()
    flash('Faculty profile updated.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/faculty/<int:faculty_id>/delete', methods=['POST'])
@login_required
def delete_faculty(faculty_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({'error': 'Faculty profile not found'}), 404
    user = db.session.get(User, faculty.user_id)
    deletion_error = _user_deletion_error(user)
    if deletion_error:
        flash(deletion_error, 'danger')
        return redirect(url_for('admin.dashboard'))
    Subject.query.filter_by(faculty_id=faculty.id).update({Subject.faculty_id: None})
    if user:
        Attendance.query.filter_by(marked_by=user.id).update({Attendance.marked_by: None})
    db.session.delete(faculty)
    if user:
        revoke_user_sessions(user.id)
        invalidate_password_reset_tokens(user.id)
        db.session.delete(user)
    db.session.commit()
    flash('Faculty profile and account deleted; assigned subjects are now unassigned.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/subjects', methods=['POST'])
@login_required
def create_subject():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.form
    code = data.get('subject_code', '').strip().upper()
    name = data.get('subject_name', '').strip()
    department = data.get('department', '').strip()
    semester = data.get('semester', '').strip()
    if not all((code, name, department, semester)):
        flash('Complete all subject fields.', 'danger')
        return redirect(url_for('admin.dashboard'))
    if Subject.query.filter_by(subject_code=code).first():
        flash('Subject code already exists.', 'danger')
        return redirect(url_for('admin.dashboard'))
    try:
        faculty_id = int(data.get('faculty_id')) if data.get('faculty_id') else None
    except (TypeError, ValueError):
        faculty_id = -1
    if faculty_id is not None and not db.session.get(Faculty, faculty_id):
        flash('Selected faculty member does not exist.', 'danger')
        return redirect(url_for('admin.dashboard'))
    db.session.add(Subject(subject_code=code, subject_name=name, department=department, semester=semester, faculty_id=faculty_id))
    db.session.commit()
    flash('Subject added successfully.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/subjects/<int:subject_id>/edit', methods=['POST'])
@login_required
def edit_subject(subject_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return jsonify({'error': 'Subject not found'}), 404
    data = _admin_payload()
    code = data.get('subject_code', subject.subject_code).strip().upper()
    if Subject.query.filter(Subject.subject_code == code, Subject.id != subject.id).first():
        flash('Subject code already exists.', 'danger')
        return redirect(url_for('admin.dashboard'))
    faculty_id_value = data.get('faculty_id', '')
    try:
        faculty_id = int(faculty_id_value) if faculty_id_value else None
    except (TypeError, ValueError):
        faculty_id = -1
    if faculty_id is not None and not db.session.get(Faculty, faculty_id):
        flash('Selected faculty member does not exist.', 'danger')
        return redirect(url_for('admin.dashboard'))
    subject.subject_code = code
    for field in ('subject_name', 'department', 'semester'):
        if data.get(field) is not None:
            setattr(subject, field, data[field].strip())
    subject.faculty_id = faculty_id
    db.session.commit()
    flash('Subject updated successfully.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/subjects/<int:subject_id>/delete', methods=['POST'])
@login_required
def delete_subject(subject_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return jsonify({'error': 'Subject not found'}), 404
    Attendance.query.filter_by(subject_id=subject.id).delete()
    db.session.delete(subject)
    db.session.commit()
    flash('Subject and its attendance records deleted.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/users/<int:user_id>/edit', methods=['POST'])
@login_required
def edit_user(user_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = _admin_payload()
    username = data.get('username', user.username).strip()
    requested_role = data.get('role', user.role).strip()
    canonical_role = normalize_role(requested_role)
    if not username or canonical_role is None:
        flash('Enter a username and a valid account role.', 'danger')
        return redirect(url_for('admin.dashboard'))
    if (
        canonical_role in AFFILIATED_COLLEGE_ROLES
        or has_role(user, *AFFILIATED_COLLEGE_ROLES)
    ):
        flash('Create affiliated-college accounts from the affiliated college registry.', 'danger')
        return redirect(url_for('admin.dashboard'))
    if User.query.filter(User.username == username, User.id != user.id).first():
        flash('Username already exists.', 'danger')
        return redirect(url_for('admin.dashboard'))
    if user.id == current_user.id and canonical_role != Role.SUPER_ADMIN:
        flash('You cannot remove your own administrator role.', 'danger')
        return redirect(url_for('admin.dashboard'))
    if user.is_enabled and has_role(user, Role.SUPER_ADMIN) and canonical_role != Role.SUPER_ADMIN and sum(
        has_role(item, Role.SUPER_ADMIN) and item.is_enabled for item in User.query.all()
    ) <= 1:
        flash('The last administrator cannot be demoted.', 'danger')
        return redirect(url_for('admin.dashboard'))
    user.username = username
    legacy_roles = {'admin': 'admin', 'faculty': 'faculty', 'student': 'student'}
    user.role = legacy_roles.get(requested_role.lower(), canonical_role.value)
    user.affiliated_college_code = None
    db.session.commit()
    flash('Account details updated.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/users/<int:user_id>/password', methods=['POST'])
@login_required
def reset_user_password(user_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    password = _admin_payload().get('password', '')
    strength_error = password_strength_error(password)
    if strength_error:
        flash(strength_error, 'danger')
        return redirect(url_for('admin.dashboard'))
    user.password_hash = hash_password(password)
    revoke_user_sessions(user.id)
    invalidate_password_reset_tokens(user.id)
    db.session.commit()
    flash('Password reset successfully.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if has_role(user, *AFFILIATED_COLLEGE_ROLES):
        flash('Disable affiliated-college accounts from the affiliated college registry.', 'danger')
        return redirect(url_for('admin.dashboard'))
    deletion_error = _user_deletion_error(user)
    if deletion_error:
        flash(deletion_error, 'danger')
        return redirect(url_for('admin.dashboard'))
    student = Student.query.filter_by(user_id=user.id).first()
    if student:
        Attendance.query.filter_by(student_id=student.id).delete()
        db.session.delete(student)
    faculty = Faculty.query.filter_by(user_id=user.id).first()
    if faculty:
        Subject.query.filter_by(faculty_id=faculty.id).update({Subject.faculty_id: None})
        db.session.delete(faculty)
    Attendance.query.filter_by(marked_by=user.id).update({Attendance.marked_by: None})
    revoke_user_sessions(user.id)
    invalidate_password_reset_tokens(user.id)
    db.session.delete(user)
    db.session.commit()
    flash('Account and associated profile deleted.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/attendance', methods=['POST'])
@login_required
def create_attendance():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    values = _attendance_values(_admin_payload())
    if not values:
        flash('Select a valid student, subject, date, and attendance status.', 'danger')
        return redirect(url_for('admin.dashboard'))
    student_id, subject_id, record_date, status, session_id = values
    if Attendance.query.filter_by(student_id=student_id, subject_id=subject_id, date=record_date, session_id=session_id).first():
        flash('An attendance record already exists for this student, subject, and date.', 'danger')
        return redirect(url_for('admin.dashboard'))
    db.session.add(Attendance(
        student_id=student_id, subject_id=subject_id, date=record_date,
        session_id=session_id, status=status, marked_by=current_user.id,
    ))
    db.session.commit()
    flash('Attendance record created.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/attendance/<int:attendance_id>/edit', methods=['POST'])
@login_required
def edit_attendance(attendance_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    record = db.session.get(Attendance, attendance_id)
    if not record:
        return jsonify({'error': 'Attendance record not found'}), 404
    values = _attendance_values(_admin_payload(), default_session_id=record.session_id)
    if not values:
        flash('Select a valid student, subject, date, and attendance status.', 'danger')
        return redirect(url_for('admin.dashboard'))
    student_id, subject_id, record_date, status, session_id = values
    duplicate = Attendance.query.filter(
        Attendance.student_id == student_id,
        Attendance.subject_id == subject_id,
        Attendance.date == record_date,
        Attendance.session_id == session_id,
        Attendance.id != record.id,
    ).first()
    if duplicate:
        flash('An attendance record already exists for this student, subject, and date.', 'danger')
        return redirect(url_for('admin.dashboard'))
    record.student_id = student_id
    record.subject_id = subject_id
    record.date = record_date
    record.session_id = session_id
    record.status = status
    record.marked_by = current_user.id
    db.session.commit()
    flash('Attendance record updated.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/attendance/<int:attendance_id>/delete', methods=['POST'])
@login_required
def delete_attendance(attendance_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    record = db.session.get(Attendance, attendance_id)
    if not record:
        return jsonify({'error': 'Attendance record not found'}), 404
    db.session.delete(record)
    db.session.commit()
    flash('Attendance record deleted.', 'success')
    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/import-csv', methods=['POST'])
@login_required
def import_csv():
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        file = request.files.get('file')
        if file is None:
            raise ImportValidationError('Please upload a CSV file.')
        payload = validate_csv_upload(file.stream, file.filename or '')
    except ImportValidationError as error:
        db.session.rollback()
        if request.is_json:
            return jsonify({'error': str(error)}), 400
        flash(str(error), 'danger')
        return redirect(url_for('admin.dashboard'))
    stage = create_import_stage(current_user.id, payload)
    if request.is_json:
        summary = import_preview_data(stage)
        return jsonify({
            'stage_id': stage.id, 'status': stage.status, 'expires_at': stage.expires_at.isoformat(),
            'preview_url': url_for('api.import_attendance_preview', stage_id=stage.id),
            'commit_url': url_for('api.commit_import', stage_id=stage.id), **summary,
        }), 202
    return redirect(url_for('admin.import_preview', stage_id=stage.id))


@admin_bp.route('/import-csv/<int:stage_id>')
@login_required
def import_preview(stage_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    stage = AttendanceImportStage.query.filter_by(id=stage_id, owner_id=current_user.id).first()
    if not stage:
        abort(404)
    if stage.expires_at <= datetime.utcnow() and stage.status == 'preview':
        expire_stage(stage)
    if stage.status != 'preview' or not stage.payload:
        return render_template('attendance_import_preview.html', stage=stage, summary=None, unavailable=True), 410
    return render_template(
        'attendance_import_preview.html', stage=stage,
        summary=import_preview_data(stage), unavailable=False,
    )


@admin_bp.route('/import-csv/<int:stage_id>/commit', methods=['POST'])
@login_required
def commit_import(stage_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    stage = AttendanceImportStage.query.filter_by(id=stage_id, owner_id=current_user.id).first()
    if not stage:
        abort(404)
    expected_phrase = f'COMMIT ATTENDANCE IMPORT {stage.id}'
    if str(_admin_payload().get('confirmation_phrase', '')).strip() != expected_phrase:
        return jsonify({'error': f'Type the confirmation phrase: {expected_phrase}'}), 400
    result, imported = commit_import_stage(stage, current_user.id)
    if result == 'committed':
        if request.is_json:
            return jsonify({'imported': imported, 'status': result}), 201
        flash(f'Committed {imported} attendance row(s) atomically.', 'success')
        return redirect(url_for('admin.dashboard'))
    status = 410 if result == 'expired' else 409
    message = 'Import preview expired.' if result == 'expired' else 'Import preview is no longer available or a row now conflicts with saved attendance.'
    if request.is_json:
        return jsonify({'error': message, 'status': result}), status
    flash(message, 'danger')
    return redirect(url_for('admin.import_preview', stage_id=stage.id))


@admin_bp.route('/import-csv/<int:stage_id>/cancel', methods=['POST'])
@login_required
def cancel_import(stage_id):
    if not _is_super_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    stage = AttendanceImportStage.query.filter_by(id=stage_id, owner_id=current_user.id).first()
    if not stage:
        abort(404)
    if stage.status == 'preview':
        stage.status = 'cancelled'
        stage.payload = None
        db.session.commit()
    if request.is_json:
        return jsonify({'status': stage.status}), 200
    flash('Attendance import preview cancelled; no attendance rows were written.', 'info')
    return redirect(url_for('admin.dashboard'))
