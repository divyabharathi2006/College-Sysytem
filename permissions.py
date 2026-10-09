"""Central role and scope policy used by server-side route authorization.

Department-scoped roles must supply a department target for scoped permissions.
Legacy profile department text is accepted until profiles are linked to department IDs.
"""

from enum import Enum


class Role(str, Enum):
    SUPER_ADMIN = 'SUPER_ADMIN'
    MANAGEMENT = 'MANAGEMENT'
    HOD = 'HOD'
    DEPARTMENT_ADMIN = 'DEPARTMENT_ADMIN'
    FACULTY = 'FACULTY'
    STUDENT = 'STUDENT'
    AFFILIATED_COLLEGE = 'AFFILIATED_COLLEGE'
    AFF_COLLEGE_ADMIN = 'AFF_COLLEGE_ADMIN'
    AFF_REGISTRAR = 'AFF_REGISTRAR'
    AFF_ATTENDANCE_OFFICER = 'AFF_ATTEND_OFFICER'


_ROLE_ALIASES = {
    'ADMIN': Role.SUPER_ADMIN,
    'SUPERADMIN': Role.SUPER_ADMIN,
    'SUPER_ADMIN': Role.SUPER_ADMIN,
    'MANAGEMENT': Role.MANAGEMENT,
    'HOD': Role.HOD,
    'DEPARTMENT_ADMIN': Role.DEPARTMENT_ADMIN,
    'DEPARTMENTADMIN': Role.DEPARTMENT_ADMIN,
    'FACULTY': Role.FACULTY,
    'STUDENT': Role.STUDENT,
    'AFFILIATED_COLLEGE': Role.AFFILIATED_COLLEGE,
    'AFF_COLLEGE_ADMIN': Role.AFF_COLLEGE_ADMIN,
    'AFF_REGISTRAR': Role.AFF_REGISTRAR,
    'AFF_ATTENDANCE_OFFICER': Role.AFF_ATTENDANCE_OFFICER,
    'AFF_ATTEND_OFFICER': Role.AFF_ATTENDANCE_OFFICER,
}

_ROLE_PERMISSIONS = {
    Role.SUPER_ADMIN: frozenset({'*'}),
    Role.MANAGEMENT: frozenset({'institution:read', 'department:read', 'analytics:read', 'report:read'}),
    Role.HOD: frozenset({
        'department:read', 'student:read', 'faculty:read', 'course:manage',
        'batch:manage', 'section:manage', 'classroom:manage', 'faculty:assign',
        'attendance:read', 'attendance:manage', 'attendance:review_corrections', 'leave:review',
        'timetable:manage', 'analytics:read', 'report:read',
    }),
    Role.DEPARTMENT_ADMIN: frozenset({
        'department:read', 'student:read', 'student:manage', 'faculty:read', 'faculty:manage',
        'course:manage', 'batch:manage', 'section:manage', 'classroom:manage',
        'faculty:assign', 'attendance:read', 'timetable:manage', 'report:read',
    }),
    Role.FACULTY: frozenset({
        'department:read', 'student:read', 'attendance:read', 'attendance:mark',
        'attendance:review_corrections', 'leave:review', 'timetable:read', 'report:read',
    }),
    Role.STUDENT: frozenset({
        'profile:read_own', 'attendance:read_own', 'timetable:read_own',
        'leave:create_own', 'leave:read_own',
    }),
    Role.AFFILIATED_COLLEGE: frozenset({
        'student:read_affiliated', 'student:register_affiliated',
    }),
    Role.AFF_COLLEGE_ADMIN: frozenset({
        'student:read_affiliated', 'student:register_affiliated',
        'student:edit_affiliated',
    }),
    Role.AFF_REGISTRAR: frozenset({
        'student:read_affiliated', 'student:register_affiliated',
    }),
    Role.AFF_ATTENDANCE_OFFICER: frozenset({
        'student:read_affiliated', 'attendance:read_affiliated',
    }),
}

_DEPARTMENT_SCOPED_ROLES = frozenset({Role.HOD, Role.DEPARTMENT_ADMIN, Role.FACULTY})
_SELF_PERMISSIONS = frozenset({
    'profile:read_own', 'attendance:read_own', 'timetable:read_own',
    'leave:create_own', 'leave:read_own',
})
AFFILIATED_COLLEGE_ROLES = frozenset({
    Role.AFFILIATED_COLLEGE,
    Role.AFF_COLLEGE_ADMIN,
    Role.AFF_REGISTRAR,
    Role.AFF_ATTENDANCE_OFFICER,
})
ASSIGNABLE_AFFILIATED_COLLEGE_ROLES = frozenset({
    Role.AFF_COLLEGE_ADMIN,
    Role.AFF_REGISTRAR,
    Role.AFF_ATTENDANCE_OFFICER,
})


def normalize_role(value):
    """Return the canonical role, including legacy lowercase `admin` accounts."""
    if isinstance(value, Role):
        return value
    normalized = str(value or '').strip().upper().replace('-', '_').replace(' ', '_')
    return _ROLE_ALIASES.get(normalized)


def role_for(user):
    return normalize_role(getattr(user, 'role', None))


def has_role(user, *roles):
    """Check canonical roles without requiring an account to be recreated."""
    actual = role_for(user)
    expected = {normalize_role(role) for role in roles}
    return actual is not None and actual in expected


def has_permission(user, permission):
    role = role_for(user)
    if role is None:
        return False
    permissions = _ROLE_PERMISSIONS[role]
    return '*' in permissions or permission in permissions


def _department_identity(department_id):
    if department_id is None:
        return set()
    values = {str(department_id).strip().casefold()}
    if isinstance(department_id, int):
        try:
            from extensions import db
            from models.academic import Department

            department = db.session.get(Department, department_id)
            if department:
                values.update({department.code.casefold(), department.name.casefold()})
        except Exception:
            # Authorization must fail closed when a requested department cannot be resolved.
            return set()
    return values


def _user_department_identity(user):
    user_department_id = getattr(user, 'department_id', None)
    profile = None
    if role_for(user) == Role.FACULTY:
        from models.faculty import Faculty
        profile = Faculty.query.filter_by(user_id=user.id).first()
    elif role_for(user) == Role.STUDENT:
        from models.student import Student

        profile = Student.query.filter_by(user_id=user.id).first()
    else:
        profile = getattr(user, 'faculty_profile', None) or getattr(user, 'student_profile', None)
        if isinstance(profile, (list, tuple)):
            profile = profile[0] if profile else None
    if profile is None:
        return _department_identity(user_department_id)
    profile_department_id = getattr(profile, 'department_id', None)
    if user_department_id is not None and profile_department_id is not None and user_department_id != profile_department_id:
        return set()
    authoritative_department_id = profile_department_id or user_department_id
    if authoritative_department_id is not None:
        return _department_identity(authoritative_department_id)
    legacy_department = getattr(profile, 'department', None)
    if legacy_department:
        normalized_department = str(legacy_department).strip().casefold()
        try:
            from extensions import db
            from models.academic import Department

            departments = Department.query.filter(db.or_(
                db.func.lower(db.func.trim(Department.code)) == normalized_department,
                db.func.lower(db.func.trim(Department.name)) == normalized_department,
            )).all()
            if len(departments) > 1:
                return set()
            if departments:
                return _department_identity(departments[0].id)
        except Exception:
            return {normalized_department}
        return {normalized_department}
    return set()


def department_id_for(user):
    """Resolve the user's department from additive IDs or legacy profile text."""
    department_id = getattr(user, 'department_id', None)
    if department_id is not None:
        return department_id
    role = role_for(user)
    profile = None
    if role == Role.FACULTY:
        from models.faculty import Faculty
        profile = Faculty.query.filter_by(user_id=user.id).first()
    elif role == Role.STUDENT:
        from models.student import Student
        profile = Student.query.filter_by(user_id=user.id).first()
    else:
        profile = getattr(user, 'faculty_profile', None) or getattr(user, 'student_profile', None)
        if isinstance(profile, (list, tuple)):
            profile = profile[0] if profile else None
    if not profile:
        return None
    department_id = getattr(profile, 'department_id', None)
    if department_id is not None:
        return department_id
    legacy_department = str(getattr(profile, 'department', '') or '').strip()
    if not legacy_department:
        return None
    try:
        from extensions import db
        from models.academic import Department

        matches = Department.query.filter(db.or_(
            db.func.lower(db.func.trim(Department.code)) == legacy_department.casefold(),
            db.func.lower(db.func.trim(Department.name)) == legacy_department.casefold(),
        )).all()
        return matches[0].id if len(matches) == 1 else None
    except Exception:
        return None


def can_access(user, permission, *, department_id=None, resource_owner_id=None):
    """Evaluate permission and optional department/owner scope; defaults are fail-closed."""
    role = role_for(user)
    if role is None or not has_permission(user, permission):
        return False
    if role == Role.SUPER_ADMIN:
        return True

    if role == Role.STUDENT:
        if permission not in _SELF_PERMISSIONS or resource_owner_id is None:
            return False
        if getattr(user, 'id', None) != resource_owner_id:
            return False
        if department_id is None:
            return True

    if role in _DEPARTMENT_SCOPED_ROLES or (role == Role.STUDENT and department_id is not None):
        requested_department = _department_identity(department_id)
        if not requested_department:
            return False
        if not requested_department.intersection(_user_department_identity(user)):
            return False

    return True
