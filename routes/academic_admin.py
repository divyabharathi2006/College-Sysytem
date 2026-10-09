from datetime import datetime
from sqlalchemy.exc import IntegrityError
from flask import Blueprint, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from extensions import db
from models.academic import AcademicYear, Batch, ClassSession, Classroom, Course, Department, Section, Semester
from models.faculty import Faculty
from models.subject import Subject
from models.student import Student
from models.user import User
from permissions import Role, can_access, department_id_for, has_role, normalize_role

academic_admin_bp = Blueprint('academic_admin', __name__)

_RESOURCES = {'departments', 'courses', 'years', 'semesters', 'batches', 'sections', 'classrooms', 'sessions'}
_PERMISSIONS = {
    'departments': 'department:manage', 'courses': 'course:manage',
    'years': 'academic_year:manage', 'semesters': 'semester:manage',
    'batches': 'batch:manage', 'sections': 'section:manage',
    'classrooms': 'classroom:manage', 'sessions': 'timetable:manage',
}
_GLOBAL = {'years', 'semesters'}


def _is_super_admin():
    return has_role(current_user, Role.SUPER_ADMIN)


def _scoped_department_id():
    return department_id_for(current_user)


def _can_manage(resource, department_id=None):
    if resource not in _RESOURCES:
        return False
    if _is_super_admin():
        return True
    if resource == 'departments' or resource in _GLOBAL or department_id is None:
        return False
    return can_access(current_user, _PERMISSIONS[resource], department_id=department_id)


def _entity_department_id(resource, entity):
    if resource == 'departments':
        return entity.id
    if resource == 'courses':
        return entity.department_id
    if resource == 'batches':
        return entity.course.department_id if entity.course else None
    if resource == 'sections':
        return entity.batch.course.department_id if entity.batch and entity.batch.course else None
    if resource == 'sessions':
        section = entity.section
        return section.batch.course.department_id if section and section.batch and section.batch.course else None
    if resource == 'classrooms':
        return entity.department_id
    return None


def _department(value):
    if value in (None, ''):
        return None
    try:
        return db.session.get(Department, int(value))
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    return datetime.strptime(str(value), '%Y-%m-%d').date()


def _parse_time(value):
    return datetime.strptime(str(value), '%H:%M').time()


def _resource_query(resource):
    department_id = _scoped_department_id()
    if _is_super_admin():
        return {
            'departments': Department.query.order_by(Department.code),
            'courses': Course.query.order_by(Course.department_id, Course.code),
            'years': AcademicYear.query.order_by(AcademicYear.label),
            'semesters': Semester.query.order_by(Semester.academic_year_id, Semester.number),
            'batches': Batch.query.order_by(Batch.code),
            'sections': Section.query.order_by(Section.code),
            'classrooms': Classroom.query.order_by(Classroom.code),
            'sessions': ClassSession.query.order_by(ClassSession.weekday, ClassSession.starts_at),
        }[resource]
    if resource == 'departments':
        return Department.query.filter_by(id=department_id)
    if resource == 'courses':
        return Course.query.filter_by(department_id=department_id).order_by(Course.code)
    if resource == 'years':
        return AcademicYear.query.order_by(AcademicYear.label)
    if resource == 'semesters':
        return Semester.query.order_by(Semester.academic_year_id, Semester.number)
    if resource == 'batches':
        return Batch.query.join(Course).filter(Course.department_id == department_id).order_by(Batch.code)
    if resource == 'sections':
        return Section.query.join(Batch).join(Course).filter(Course.department_id == department_id).order_by(Section.code)
    if resource == 'classrooms':
        return Classroom.query.filter_by(department_id=department_id).order_by(Classroom.code)
    return ClassSession.query.join(Section).join(Batch).join(Course).filter(
        Course.department_id == department_id
    ).order_by(ClassSession.weekday, ClassSession.starts_at)


def _page_data():
    scoped = not _is_super_admin()
    department_id = _scoped_department_id()
    query = lambda key: _resource_query(key).all()
    departments = query('departments')
    if scoped and not departments:
        return None
    if scoped:
        department = db.session.get(Department, department_id)
        faculty = Faculty.query.filter(
            db.or_(
                Faculty.department_id == department_id,
                db.func.lower(db.func.trim(Faculty.department)).in_({
                    department.code.casefold(), department.name.casefold(),
                }),
            )
        ).order_by(Faculty.name).all()
        subjects = Subject.query.filter(
            db.func.lower(db.func.trim(Subject.department)).in_({
                department.code.casefold(), department.name.casefold(),
            })
        ).order_by(Subject.subject_code).all()
    else:
        faculty = Faculty.query.order_by(Faculty.name).all()
        subjects = Subject.query.order_by(Subject.subject_code).all()
    users = User.query.order_by(User.username).all() if _is_super_admin() else []
    data = {resource: query(resource) for resource in _RESOURCES}
    if scoped and department_id:
        department = db.session.get(Department, department_id)
        legacy_department_values = {department.code.casefold(), department.name.casefold()}
        students = Student.query.filter(db.or_(
            Student.department_id == department_id,
            db.and_(
                Student.department_id.is_(None),
                db.func.lower(db.func.trim(Student.department)).in_(legacy_department_values),
            ),
        )).order_by(Student.name).all()
    elif _is_super_admin():
        students = Student.query.order_by(Student.name).all()
    else:
        students = []
    data.update({
        'departments': departments,
        'faculty': faculty,
        'subjects': subjects,
        'users': users,
        'can_assign_roles': _is_super_admin(),
        'can_manage_global': _is_super_admin(),
        'current_department_id': department_id,
        'academic_years': data['years'],
        'students': students,
        'can_assign_student_sections': _is_super_admin() or has_role(current_user, Role.DEPARTMENT_ADMIN),
    })
    return data


def _form_response(message, status=200):
    if request.is_json:
        return jsonify({'error': message}), status
    flash(message, 'danger')
    return redirect(url_for('academic_admin.academic_dashboard'))


def _success_response(message, item=None, status=200):
    if request.is_json:
        result = {'message': message}
        if item is not None:
            result['id'] = item.id
        return jsonify(result), status
    flash(message, 'success')
    return redirect(url_for('academic_admin.academic_dashboard'))


def _payload():
    return request.get_json(silent=True) or request.form


def _validate_department_scope(resource, requested_department_id):
    if _is_super_admin():
        if resource in _GLOBAL:
            return None
        if requested_department_id in (None, ''):
            return None
        department = _department(requested_department_id)
        return department.id if department else False
    own_id = _scoped_department_id()
    if own_id is None:
        return False
    if requested_department_id not in (None, ''):
        department = _department(requested_department_id)
        if not department or department.id != own_id:
            return False
    return own_id


def _resolve_department_for_resource(resource, data, item=None):
    if resource == 'departments':
        return None
    if resource == 'courses':
        return _validate_department_scope(resource, data.get('department_id', getattr(item, 'department_id', None)))
    if resource == 'batches':
        course_id = data.get('course_id', getattr(item, 'course_id', None))
        course = db.session.get(Course, course_id) if course_id not in (None, '') else None
        return _validate_department_scope(resource, course.department_id if course else None) if course else False
    if resource == 'sections':
        batch_id = data.get('batch_id', getattr(item, 'batch_id', None))
        batch = db.session.get(Batch, batch_id) if batch_id not in (None, '') else None
        return _validate_department_scope(resource, batch.course.department_id if batch and batch.course else None) if batch else False
    if resource == 'sessions':
        section_id = data.get('section_id', getattr(item, 'section_id', None))
        section = db.session.get(Section, section_id) if section_id not in (None, '') else None
        dept_id = section.batch.course.department_id if section and section.batch and section.batch.course else None
        return _validate_department_scope(resource, dept_id) if dept_id else False
    if resource == 'classrooms':
        return _validate_department_scope(resource, data.get('department_id', getattr(item, 'department_id', None)))
    return None


def _as_int(data, key, default=None):
    value = data.get(key, default)
    if value in (None, ''):
        return None
    return int(value)


def _build_or_update(resource, data, item=None, department_id=None):
    entity = item or {
        'departments': Department, 'courses': Course, 'years': AcademicYear,
        'semesters': Semester, 'batches': Batch, 'sections': Section,
        'classrooms': Classroom, 'sessions': ClassSession,
    }[resource]()
    if resource == 'departments':
        entity.code = str(data.get('code', entity.code if item else '')).strip().upper()
        entity.name = str(data.get('name', entity.name if item else '')).strip()
        entity.is_active = str(data.get('is_active', 'true')).lower() not in {'0', 'false', 'off', 'no'}
    elif resource == 'courses':
        entity.department_id = department_id
        entity.code = str(data.get('code', entity.code if item else '')).strip().upper()
        entity.name = str(data.get('name', entity.name if item else '')).strip()
    elif resource == 'years':
        entity.label = str(data.get('label', entity.label if item else '')).strip()
        entity.starts_on = _parse_date(data.get('starts_on', entity.starts_on.isoformat() if item else ''))
        entity.ends_on = _parse_date(data.get('ends_on', entity.ends_on.isoformat() if item else ''))
        if entity.starts_on >= entity.ends_on:
            raise ValueError('Academic year end date must be after its start date.')
    elif resource == 'semesters':
        entity.academic_year_id = _as_int(data, 'academic_year_id', entity.academic_year_id if item else None)
        entity.number = _as_int(data, 'number', entity.number if item else None)
        entity.starts_on = _parse_date(data.get('starts_on', entity.starts_on.isoformat() if item else ''))
        entity.ends_on = _parse_date(data.get('ends_on', entity.ends_on.isoformat() if item else ''))
        if entity.starts_on >= entity.ends_on:
            raise ValueError('Semester end date must be after its start date.')
        if not db.session.get(AcademicYear, entity.academic_year_id):
            raise ValueError('Select an existing academic year.')
    elif resource == 'batches':
        entity.course_id = _as_int(data, 'course_id', entity.course_id if item else None)
        entity.academic_year_id = _as_int(data, 'academic_year_id', entity.academic_year_id if item else None)
        entity.code = str(data.get('code', entity.code if item else '')).strip().upper()
        if not db.session.get(Course, entity.course_id) or not db.session.get(AcademicYear, entity.academic_year_id):
            raise ValueError('Select an existing course and academic year.')
    elif resource == 'sections':
        entity.batch_id = _as_int(data, 'batch_id', entity.batch_id if item else None)
        entity.code = str(data.get('code', entity.code if item else '')).strip().upper()
        if not db.session.get(Batch, entity.batch_id):
            raise ValueError('Select an existing batch.')
    elif resource == 'classrooms':
        entity.department_id = department_id
        entity.code = str(data.get('code', entity.code if item else '')).strip().upper()
        entity.building = str(data.get('building', entity.building if item else '')).strip() or None
        entity.capacity = _as_int(data, 'capacity', entity.capacity if item else None)
        if entity.capacity is not None and entity.capacity <= 0:
            raise ValueError('Classroom capacity must be a positive number.')
    elif resource == 'sessions':
        entity.section_id = _as_int(data, 'section_id', entity.section_id if item else None)
        entity.semester_id = _as_int(data, 'semester_id', entity.semester_id if item else None)
        entity.subject_id = _as_int(data, 'subject_id', entity.subject_id if item else None)
        entity.faculty_id = _as_int(data, 'faculty_id', entity.faculty_id if item else None)
        entity.classroom_id = _as_int(data, 'classroom_id', entity.classroom_id if item else None)
        entity.weekday = _as_int(data, 'weekday', entity.weekday if item else None)
        entity.starts_at = _parse_time(data.get('starts_at', entity.starts_at.strftime('%H:%M') if item else ''))
        entity.ends_at = _parse_time(data.get('ends_at', entity.ends_at.strftime('%H:%M') if item else ''))
        if entity.starts_at >= entity.ends_at:
            raise ValueError('Class session end time must be after its start time.')
        section = db.session.get(Section, entity.section_id)
        semester = db.session.get(Semester, entity.semester_id)
        subject = db.session.get(Subject, entity.subject_id)
        faculty = db.session.get(Faculty, entity.faculty_id) if entity.faculty_id else None
        classroom = db.session.get(Classroom, entity.classroom_id) if entity.classroom_id else None
        if not section or not semester or not subject or (entity.faculty_id and not faculty) or (entity.classroom_id and not classroom):
            raise ValueError('Select existing section, semester, subject, faculty, and classroom records.')
        course_department = section.batch.course.department
        if subject.department.strip().casefold() not in {course_department.code.casefold(), course_department.name.casefold()}:
            raise ValueError('The subject must belong to the selected section department.')
        if faculty and not _faculty_in_department(faculty, course_department):
            raise ValueError('The selected faculty member is outside this department.')
        if classroom and classroom.department_id not in (None, course_department.id):
            raise ValueError('The selected classroom is outside this department.')
        overlap_scope = [ClassSession.section_id == entity.section_id]
        if entity.faculty_id is not None:
            overlap_scope.append(ClassSession.faculty_id == entity.faculty_id)
        if entity.classroom_id is not None:
            overlap_scope.append(ClassSession.classroom_id == entity.classroom_id)
        if entity.weekday not in range(7):
            raise ValueError('Choose a weekday from Monday through Sunday.')
        conflict = ClassSession.query.filter(
            ClassSession.weekday == entity.weekday,
            ClassSession.starts_at < entity.ends_at,
            ClassSession.ends_at > entity.starts_at,
            ClassSession.id != (entity.id if item else -1),
            db.or_(*overlap_scope),
        ).first()
        if conflict:
            raise ValueError('This time overlaps another session for the section, faculty member, or classroom.')
    required = {
        'departments': ('code', 'name'), 'courses': ('code', 'name'), 'years': ('label',),
        'semesters': ('number',), 'batches': ('code',), 'sections': ('code',),
        'classrooms': ('code',), 'sessions': (),
    }[resource]
    for field in required:
        if not getattr(entity, field, None):
            raise ValueError(f'{field.replace("_", " ").capitalize()} is required.')
    return entity


def _faculty_in_department(faculty, department):
    if faculty.department_id is not None:
        return faculty.department_id == department.id
    return faculty.department.strip().casefold() in {department.code.casefold(), department.name.casefold()}


def _student_department_id(student):
    if student.department_id is not None:
        return student.department_id
    value = str(student.department or '').strip()
    if not value:
        return None
    matches = Department.query.filter(db.or_(
        db.func.lower(db.func.trim(Department.code)) == value.casefold(),
        db.func.lower(db.func.trim(Department.name)) == value.casefold(),
    )).all()
    return matches[0].id if len(matches) == 1 else None


def _duplicate_record(resource, entity):
    query = None
    if resource == 'departments':
        identity_values = {entity.code.casefold(), entity.name.casefold()}
        query = Department.query.filter(db.or_(
            db.func.lower(db.func.trim(Department.code)).in_(identity_values),
            db.func.lower(db.func.trim(Department.name)).in_(identity_values),
        ))
    elif resource == 'courses':
        query = Course.query.filter(
            Course.department_id == entity.department_id,
            db.func.lower(db.func.trim(Course.code)) == entity.code.casefold(),
        )
    elif resource == 'years':
        query = AcademicYear.query.filter(db.func.lower(db.func.trim(AcademicYear.label)) == entity.label.casefold())
    elif resource == 'semesters':
        query = Semester.query.filter_by(academic_year_id=entity.academic_year_id, number=entity.number)
    elif resource == 'batches':
        query = Batch.query.filter(
            Batch.course_id == entity.course_id,
            Batch.academic_year_id == entity.academic_year_id,
            db.func.lower(db.func.trim(Batch.code)) == entity.code.casefold(),
        )
    elif resource == 'sections':
        query = Section.query.filter(
            Section.batch_id == entity.batch_id,
            db.func.lower(db.func.trim(Section.code)) == entity.code.casefold(),
        )
    elif resource == 'classrooms':
        query = Classroom.query.filter(db.func.lower(db.func.trim(Classroom.code)) == entity.code.casefold())
    if query is None:
        return False
    if entity.id is not None:
        query = query.filter(entity.__class__.id != entity.id)
    return query.first() is not None


def _list_resources(resource):
    if resource not in _RESOURCES:
        return None
    if _is_super_admin():
        return _resource_query(resource).all()
    department_id = department_id_for(current_user)
    if not department_id or not can_access(current_user, 'department:read', department_id=department_id):
        return None
    if resource in _GLOBAL:
        return _resource_query(resource).all() if has_role(current_user, Role.HOD, Role.DEPARTMENT_ADMIN) else None
    return _resource_query(resource).all()


@academic_admin_bp.route('/academic', methods=['GET'])
@login_required
def academic_dashboard():
    if not _is_super_admin() and not has_role(current_user, Role.HOD, Role.DEPARTMENT_ADMIN):
        return jsonify({'error': 'Forbidden'}), 403
    if not _is_super_admin():
        department_id = department_id_for(current_user)
        if not department_id or not can_access(current_user, 'department:read', department_id=department_id):
            return jsonify({'error': 'A valid department assignment is required.'}), 403
    context = _page_data()
    if context is None:
        return jsonify({'error': 'A department assignment is required.'}), 403
    return render_template('academic_dashboard.html', **context)


@academic_admin_bp.route('/academic/<resource>', methods=['GET'])
@login_required
def list_academic_resource(resource):
    if resource not in _RESOURCES:
        return jsonify({'error': 'Resource not found'}), 404
    items = _list_resources(resource)
    if items is None:
        return jsonify({'error': 'Forbidden'}), 403
    rows = []
    for item in items:
        rows.append({
            'id': item.id,
            'name': getattr(item, 'name', getattr(item, 'label', getattr(item, 'code', str(item.id)))),
            'department_id': _entity_department_id(resource, item),
        })
    return jsonify(rows)


@academic_admin_bp.route('/academic/<resource>', methods=['POST'])
@academic_admin_bp.route('/academic/<resource>/<int:resource_id>', methods=['POST'])
@login_required
def mutate_academic_resource(resource, resource_id=None):
    if resource not in _RESOURCES:
        return jsonify({'error': 'Resource not found'}), 404
    data = _payload()
    action = str(data.get('action', 'create' if resource_id is None else 'edit')).lower()
    if action not in {'create', 'edit', 'delete'} or (resource_id is None and action != 'create'):
        return _form_response('Choose a valid create, edit, or delete action.', 400)
    item = None
    if resource_id is not None:
        model = {
            'departments': Department, 'courses': Course, 'years': AcademicYear,
            'semesters': Semester, 'batches': Batch, 'sections': Section,
            'classrooms': Classroom, 'sessions': ClassSession,
        }[resource]
        item = db.session.get(model, resource_id)
        if item is None:
            return _form_response('Academic record not found.', 404)
        old_department_id = _entity_department_id(resource, item)
        if not _can_manage(resource, old_department_id):
            return _form_response('You are not authorized to change this department record.', 403)
    elif not _can_manage(resource, department_id_for(current_user)):
        return _form_response('You are not authorized to create this academic record.', 403)

    if action == 'delete':
        if item is None:
            return _form_response('Choose a record to delete.', 400)
        db.session.delete(item)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return _form_response('This record is still referenced by other academic records and cannot be deleted.', 409)
        return _success_response('Academic record deleted.')

    try:
        target_department_id = _resolve_department_for_resource(resource, data, item)
        if target_department_id is False:
            return _form_response('The selected department or parent record is outside your authorized department.', 403)
        if resource in {'courses', 'batches', 'sections', 'classrooms', 'sessions'}:
            if not _can_manage(resource, target_department_id):
                return _form_response('You are not authorized to manage this department record.', 403)
        if resource in _GLOBAL and not _is_super_admin():
            return _form_response('Only a Super Admin can manage institution-wide academic calendars.', 403)
        entity = _build_or_update(resource, data, item, target_department_id)
        if _duplicate_record(resource, entity):
            db.session.rollback()
            return _form_response('A record with these unique values already exists.', 409)
        if item is None:
            db.session.add(entity)
        db.session.commit()
    except (TypeError, ValueError) as error:
        db.session.rollback()
        return _form_response(str(error) or 'Invalid academic record values.', 400)
    except IntegrityError:
        db.session.rollback()
        return _form_response('A duplicate value or referenced record conflicts with this change.', 409)
    return _success_response('Academic record saved.', entity, 201 if item is None else 200)


@academic_admin_bp.route('/academic/assignments/<int:user_id>', methods=['POST'])
@login_required
def assign_user_department_role(user_id):
    if not _is_super_admin():
        return _form_response('Only a Super Admin can assign department leadership or account roles.', 403)
    user = db.session.get(User, user_id)
    if not user:
        return _form_response('User account not found.', 404)
    data = _payload()
    role = normalize_role(data.get('role'))
    department = _department(data.get('department_id'))
    if role not in {Role.HOD, Role.DEPARTMENT_ADMIN, Role.FACULTY, Role.STUDENT, Role.MANAGEMENT}:
        return _form_response('Choose an assignable non-administrator role.', 400)
    if role in {Role.HOD, Role.DEPARTMENT_ADMIN, Role.FACULTY, Role.STUDENT} and not department:
        return _form_response('Select a valid department for this role.', 400)
    if role == Role.STUDENT:
        from models.student import Student
        profile = Student.query.filter_by(user_id=user.id).first()
        if not profile:
            return _form_response('Create a student profile before assigning the student role.', 400)
        if profile.department_id not in (None, department.id) or profile.department.strip().casefold() not in {
            department.code.casefold(), department.name.casefold()
        }:
            return _form_response('The student profile department does not match the selected department.', 409)
        profile.department_id = department.id
    if role == Role.FACULTY:
        profile = Faculty.query.filter_by(user_id=user.id).first()
        if not profile:
            return _form_response('Create a faculty profile before assigning the faculty role.', 400)
        if profile.department_id not in (None, department.id):
            return _form_response('The faculty profile is already linked to another department.', 409)
        if profile.department.strip().casefold() not in {'', department.code.casefold(), department.name.casefold()}:
            return _form_response('The faculty profile department does not match the selected department.', 409)
        profile.department_id = department.id
        profile.department = profile.department or department.code
    if user.id == current_user.id and not has_role(user, Role.SUPER_ADMIN):
        return _form_response('You cannot change your own role.', 403)
    user.role = role.value
    user.department_id = department.id if department else None
    user.affiliated_college_code = None
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _form_response('The selected department link is invalid.', 400)
    return _success_response('Account role and department assignment saved.')


@academic_admin_bp.route('/academic/students/<int:student_id>/section', methods=['POST'])
@login_required
def assign_student_section(student_id):
    if not _is_super_admin() and not has_role(current_user, Role.DEPARTMENT_ADMIN):
        return _form_response('Only a Super Admin or Department Admin can assign student sections.', 403)
    student = db.session.get(Student, student_id)
    if not student:
        return _form_response('Student record not found.', 404)
    student_department_id = _student_department_id(student)
    if not student_department_id:
        return _form_response('Link the student to an unambiguous department before assigning a section.', 400)
    if not _is_super_admin() and (
        not can_access(current_user, 'student:manage', department_id=student_department_id)
        or not can_access(current_user, 'section:manage', department_id=student_department_id)
    ):
        return _form_response('The student is outside your authorized department.', 403)

    raw_section_id = _payload().get('section_id')
    section = None
    if raw_section_id not in (None, ''):
        try:
            section = db.session.get(Section, int(raw_section_id))
        except (TypeError, ValueError):
            section = None
        if not section:
            return _form_response('Select a valid section.', 400)
        section_department_id = _entity_department_id('sections', section)
        if section_department_id != student_department_id:
            return _form_response('The selected section must belong to the student department.', 400)
        if not _is_super_admin() and not can_access(
            current_user, 'section:manage', department_id=section_department_id,
        ):
            return _form_response('The selected section is outside your authorized department.', 403)

    student.section_id = section.id if section else None
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _form_response('The section assignment could not be saved.', 400)
    return _success_response('Student section assignment saved; legacy department and section text was preserved.', student)
