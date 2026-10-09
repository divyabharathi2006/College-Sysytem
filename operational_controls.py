"""Central server-side enforcement for operational emergency controls."""

from flask import Response, current_app, jsonify, request
from flask_login import current_user

from permissions import Role, has_role

POLICY_LABELS = {
    'maintenance_mode': 'Maintenance mode',
    'read_only_mode': 'Read-only mode',
    'attendance_modification_disabled': 'Attendance modification disabled',
    'attendance_import_disabled': 'Attendance import disabled',
    'attendance_exports_disabled': 'Attendance exports disabled',
}

_CONTROL_ENDPOINTS = {
    'admin.security_center', 'admin.accounts_security',
    'admin.update_operational_policy', 'admin.revoke_all_sessions',
    'admin.set_user_status', 'admin.revoke_user_sessions_route', 'admin.revoke_session_route',
}
_MAINTENANCE_PUBLIC_ENDPOINTS = {
    'auth.login', 'auth.logout',
}


def policy_states():
    from models.operational import SystemPolicy

    stored = {item.key: item.value for item in SystemPolicy.query.all()}
    return {key: stored.get(key, 'false').strip().lower() == 'true' for key in POLICY_LABELS}


def policy_enabled(key):
    if key not in POLICY_LABELS:
        return False
    from models.operational import SystemPolicy

    item = db_session_get_policy(SystemPolicy, key)
    return bool(item and item.value.strip().lower() == 'true')


def db_session_get_policy(model, key):
    from extensions import db

    return db.session.get(model, key)


def confirmation_phrase(policy_key, enabled):
    action = {
        'maintenance_mode': 'ENABLE MAINTENANCE' if enabled else 'DISABLE MAINTENANCE',
        'read_only_mode': 'ENABLE READ-ONLY' if enabled else 'DISABLE READ-ONLY',
        'attendance_modification_disabled': 'DISABLE ATTENDANCE MODIFICATION' if enabled else 'ENABLE ATTENDANCE MODIFICATION',
        'attendance_import_disabled': 'DISABLE ATTENDANCE IMPORT' if enabled else 'ENABLE ATTENDANCE IMPORT',
        'attendance_exports_disabled': 'DISABLE ATTENDANCE EXPORTS' if enabled else 'ENABLE ATTENDANCE EXPORTS',
    }
    return action[policy_key]


def _response(message, status):
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'error': message}), status
    return Response(message, status=status, mimetype='text/plain')


def _is_attendance_mutation():
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return False
    endpoint = request.endpoint or ''
    return _is_import() or any(token in endpoint for token in ('attendance', 'correction'))


def _is_import():
    endpoint = (request.endpoint or '').lower()
    return 'import' in endpoint and not endpoint.endswith('cancel_import')


def _is_export():
    return 'export' in (request.endpoint or '').lower()


def enforce_operational_policies():
    """Reject prohibited work before a route can mutate data or return an export."""
    endpoint = request.endpoint or ''
    states = policy_states()

    if states['maintenance_mode']:
        maintenance_allowed = (
            endpoint in _CONTROL_ENDPOINTS
            or endpoint in _MAINTENANCE_PUBLIC_ENDPOINTS
            or endpoint == 'static'
        )
        if not maintenance_allowed:
            return _response('Service is temporarily unavailable for maintenance.', 503)

    if states['read_only_mode'] and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        readonly_allowed = (
            endpoint in _CONTROL_ENDPOINTS
            or endpoint in {'auth.login', 'auth.logout'}
            or endpoint.endswith('cancel_import')
        )
        if not readonly_allowed:
            return _response('The system is in read-only mode.', 423)

    if _is_attendance_mutation() and states['attendance_modification_disabled']:
        return _response('Attendance modification is temporarily disabled.', 423)
    if _is_import() and states['attendance_import_disabled']:
        return _response('Attendance imports are temporarily disabled.', 423)
    if _is_export() and states['attendance_exports_disabled']:
        return _response('Attendance exports are temporarily disabled.', 423)
    return None


def install_operational_controls(app):
    app.before_request(enforce_operational_policies)
