"""Append-only security event capture with conservative field allowlists."""

import re
from datetime import date, datetime

from flask import current_app, g, request
from flask_login import current_user
from sqlalchemy import event

from extensions import db
from models.audit import SecurityEvent
from security import keyed_digest

_MUTATING_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_ALLOWED_REQUEST_FIELDS = {
    'action', 'attendance_id', 'code', 'correction_id', 'date', 'decision',
    'department_id', 'ends_on', 'is_active', 'leave_id', 'new_status',
    'previous_status', 'resource_id', 'role', 'session_id', 'starts_on',
    'status', 'student_id', 'subject_id', 'user_id', 'is_enabled',
    'policy_key', 'enabled', 'stage_id', 'imported', 'accepted_count', 'error_count',
    'target_type', 'expires_at', 'created_at', 'section_id',
}
_ENTITY_FIELDS = {
    'user': ('role', 'department_id', 'is_enabled', 'last_login_at'),
    'student': ('department_id',),
    'faculty': ('department_id',),
    'attendance': ('status', 'student_id', 'subject_id', 'date', 'session_id'),
    'correction_request': ('status', 'attendance_id', 'previous_status', 'new_status'),
    'leave_request': ('status', 'student_id', 'starts_on', 'ends_on'),
    'department': ('code', 'is_active'),
    'course': ('department_id', 'code'),
    'academic_year': ('label',),
    'semester': ('academic_year_id', 'number'),
    'batch': ('course_id', 'academic_year_id', 'code'),
    'section': ('batch_id', 'code'),
    'classroom': ('department_id', 'code', 'capacity'),
    'class_session': ('section_id', 'semester_id', 'subject_id', 'faculty_id', 'classroom_id', 'weekday'),
    'subject': ('subject_code', 'department', 'faculty_id'),
    'announcement': ('target_type', 'department_id', 'section_id', 'creator_id', 'created_at', 'expires_at', 'is_active'),
}
_SECRET_KEY_PATTERN = re.compile(r'password|secret|token|credential|authorization|csrf|api.?key|hash', re.I)


def _model_for_resource(resource_type):
    from models.academic import AcademicYear, Batch, ClassSession, Classroom, Course, Department, Section, Semester
    from models.attendance import Attendance, AttendanceCorrectionRequest, LeaveRequest
    from models.announcements import Announcement
    from models.faculty import Faculty
    from models.student import Student
    from models.subject import Subject
    from models.user import User

    return {
        'user': User, 'student': Student, 'faculty': Faculty, 'attendance': Attendance,
        'subject': Subject,
        'correction_request': AttendanceCorrectionRequest, 'leave_request': LeaveRequest,
        'department': Department, 'course': Course, 'academic_year': AcademicYear,
        'semester': Semester, 'batch': Batch, 'section': Section, 'classroom': Classroom,
        'class_session': ClassSession,
        'announcement': Announcement,
    }.get(resource_type)


def _resource_for_request():
    endpoint = request.endpoint or 'unknown'
    args = request.view_args or {}
    if endpoint.startswith('academic_admin.'):
        resource = args.get('resource')
        resource_types = {
            'departments': 'department', 'courses': 'course', 'years': 'academic_year',
            'semesters': 'semester', 'batches': 'batch', 'sections': 'section',
            'classrooms': 'classroom', 'sessions': 'class_session',
        }
        if endpoint.endswith('assign_user_department_role'):
            return 'user', args.get('user_id')
        return resource_types.get(resource, 'academic_record'), args.get('resource_id')
    if 'import' in endpoint:
        return 'import', None
    if endpoint.startswith('announcements.'):
        return 'announcement', args.get('announcement_id')
    if 'export' in endpoint:
        return 'export', args.get('file_format')
    if 'subject' in endpoint:
        return 'subject', args.get('subject_id')
    for key, resource_type in (
        ('user_id', 'user'), ('student_id', 'student'), ('faculty_id', 'faculty'),
        ('subject_id', 'subject'), ('attendance_id', 'attendance'),
        ('correction_id', 'correction_request'), ('leave_id', 'leave_request'),
    ):
        if key in args:
            return resource_type, args[key]
    if endpoint.endswith('review_correction'):
        return 'correction_request', args.get('correction_id')
    if endpoint.endswith('review_leave_request'):
        return 'leave_request', args.get('leave_id')
    if 'correction' in endpoint:
        return 'correction_request', None
    if 'leave' in endpoint:
        return 'leave_request', None
    if 'attendance' in endpoint or 'mark_attendance' in endpoint:
        return 'attendance', None
    if 'department' in endpoint:
        return 'department', None
    if 'user' in endpoint or endpoint.endswith('students') or endpoint.endswith('faculty'):
        return 'user', None
    return 'mutation', None


def _safe_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)[:120] if isinstance(value, str) else value
    return None


def _safe_mapping(mapping, allowed_fields):
    result = {}
    for key in allowed_fields:
        if key in mapping and not _SECRET_KEY_PATTERN.search(key):
            safe = _safe_value(mapping.get(key))
            if safe is not None:
                result[key] = safe
    return result


def _snapshot_entity(resource_type, resource_id):
    model = _model_for_resource(resource_type)
    if not model or resource_id is None:
        return None
    try:
        entity = db.session.get(model, int(resource_id))
    except (TypeError, ValueError):
        return None
    if entity is None:
        return None
    return _safe_mapping(
        {field: getattr(entity, field, None) for field in _ENTITY_FIELDS.get(resource_type, ())},
        _ENTITY_FIELDS.get(resource_type, ()),
    )


def _request_business_fields():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form
    if not hasattr(payload, 'get'):
        return {}
    return _safe_mapping(payload, _ALLOWED_REQUEST_FIELDS)


def record_security_event(*, action, status, reason, actor=None, actor_role=None,
                          resource_type='authentication', resource_id=None,
                          before_data=None, after_data=None, response_status=None):
    """Persist one security event; callers must pass only safe, allowlisted details."""
    if actor is None and current_user.is_authenticated:
        actor = current_user
    role = actor_role or (getattr(actor, 'role', None) if actor else None) or 'anonymous'
    forwarded_agent = request.user_agent.string if request else ''
    safe_agent = ''.join(char for char in (forwarded_agent or '') if char.isprintable())[:255]
    address = request.remote_addr if request else None
    allowed_fields = set(_ENTITY_FIELDS.get(resource_type, ())) | _ALLOWED_REQUEST_FIELDS
    before_data = _safe_mapping(before_data, allowed_fields) if isinstance(before_data, dict) else None
    after_data = _safe_mapping(after_data, allowed_fields) if isinstance(after_data, dict) else None
    event_row = SecurityEvent(
        actor_id=getattr(actor, 'id', None), actor_role=str(role)[:30],
        action=str(action)[:100], resource_type=str(resource_type)[:60],
        resource_id=str(resource_id)[:80] if resource_id is not None else None,
        status=str(status)[:30], response_status=response_status,
        reason=str(reason)[:120], before_data=before_data,
        after_data=after_data, ip_hash=keyed_digest('audit-ip', address or 'unknown'),
        user_agent=safe_agent,
    )
    db.session.add(event_row)
    db.session.commit()
    return event_row


def install_audit_hooks(app):
    @app.before_request
    def capture_audit_before_state():
        endpoint = request.endpoint or ''
        if endpoint == 'auth.login':
            return None
        should_log = request.method in _MUTATING_METHODS or 'export' in endpoint
        if not should_log:
            return None
        resource_type, resource_id = _resource_for_request()
        g.security_audit = {
            'action': f'{endpoint}:{request.method.lower()}',
            'resource_type': resource_type,
            'resource_id': resource_id,
            'before_data': _snapshot_entity(resource_type, resource_id),
        }
        return None

    @app.after_request
    def persist_audit_event(response):
        context = getattr(g, 'security_audit', None)
        if context is None:
            return response
        status_code = response.status_code
        result = 'success' if status_code < 400 else ('denied' if status_code < 500 else 'error')
        reason = 'request_completed' if status_code < 400 else (
            'request_rejected' if status_code < 500 else 'request_failed'
        )
        resource_type = context['resource_type']
        resource_id = context['resource_id']
        if resource_id is None and response.is_json:
            payload = response.get_json(silent=True) or {}
            if isinstance(payload, dict):
                resource_id = payload.get('id') or payload.get('request_id')
        after_data = _snapshot_entity(resource_type, resource_id) or _request_business_fields()
        actor = current_user if current_user.is_authenticated else None
        try:
            record_security_event(
                action=context['action'], status=result, reason=reason,
                actor=actor, resource_type=resource_type, resource_id=resource_id,
                before_data=context['before_data'], after_data=after_data,
                response_status=status_code,
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Security event persistence failed.')
        return response


@event.listens_for(SecurityEvent, 'before_update')
def prevent_security_event_updates(mapper, connection, target):
    raise ValueError('Security events are append-only.')


@event.listens_for(SecurityEvent, 'before_delete')
def prevent_security_event_deletes(mapper, connection, target):
    raise ValueError('Security events are append-only.')
