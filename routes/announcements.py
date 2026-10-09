from datetime import datetime, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.academic import Batch, ClassSession, Course, Department, Section
from models.announcements import Announcement
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from permissions import Role, department_id_for, has_role, role_for

announcements_bp = Blueprint('announcements', __name__)


def _profile_department_id(profile, user=current_user):
	profile_id = getattr(profile, 'department_id', None) if profile else None
	user_id = getattr(user, 'department_id', None)
	if profile_id is not None and user_id is not None and profile_id != user_id:
		return None
	department_id = profile_id if profile_id is not None else user_id
	if department_id is not None:
		return department_id if db.session.get(Department, department_id) else None
	legacy_value = str(getattr(profile, 'department', '') or '').strip()
	if not legacy_value:
		return None
	matches = Department.query.filter(db.or_(
		db.func.lower(db.func.trim(Department.code)) == legacy_value.casefold(),
		db.func.lower(db.func.trim(Department.name)) == legacy_value.casefold(),
	)).all()
	return matches[0].id if len(matches) == 1 else None


def _section_department_id(section):
	if not section or not section.batch or not section.batch.course:
		return None
	department_id = section.batch.course.department_id
	return department_id if db.session.get(Department, department_id) else None


def _faculty_profile():
	profiles = Faculty.query.filter_by(user_id=current_user.id).all()
	return profiles[0] if len(profiles) == 1 else None


def _student_profile():
	profiles = Student.query.filter_by(user_id=current_user.id).all()
	return profiles[0] if len(profiles) == 1 else None


def _assigned_faculty_sections(faculty=None):
	faculty = faculty or _faculty_profile()
	if not faculty:
		return set()
	department_id = _profile_department_id(faculty)
	if not department_id:
		return set()
	sessions = ClassSession.query.join(Subject, ClassSession.subject_id == Subject.id).filter(
		ClassSession.faculty_id == faculty.id,
		Subject.faculty_id == faculty.id,
	).all()
	section_ids = set()
	department = db.session.get(Department, department_id)
	if not department:
		return section_ids
	valid_subject_departments = {department.code.strip().casefold(), department.name.strip().casefold()}
	for class_session in sessions:
		section = class_session.section
		subject = class_session.subject
		section_department_id = _section_department_id(section)
		if (
			section_department_id == department_id and subject
			and str(subject.department or '').strip().casefold() in valid_subject_departments
		):
			section_ids.add(section.id)
	return section_ids


def _read_scope():
	role = role_for(current_user)
	if role in {Role.SUPER_ADMIN, Role.MANAGEMENT}:
		return None, None, True
	if role in {Role.HOD, Role.DEPARTMENT_ADMIN}:
		department_id = department_id_for(current_user)
		if department_id and db.session.get(Department, department_id):
			return department_id, set(), False
		return None, set(), False
	if role == Role.FACULTY:
		faculty = _faculty_profile()
		department_id = _profile_department_id(faculty) if faculty else None
		return department_id, _assigned_faculty_sections(faculty), False
	if role == Role.STUDENT:
		student = _student_profile()
		department_id = _profile_department_id(student) if student else None
		section_ids = set()
		section = db.session.get(Section, student.section_id) if student and student.section_id else None
		if section and department_id and _section_department_id(section) == department_id:
			section_ids.add(section.id)
		return department_id, section_ids, False
	return None, set(), False


def _valid_announcement_scope(item):
	if item.target_type == 'institution':
		return item.department_id is None and item.section_id is None
	if item.target_type == 'department':
		return bool(item.department_id and item.section_id is None and db.session.get(Department, item.department_id))
	if item.target_type == 'section':
		section = db.session.get(Section, item.section_id) if item.section_id else None
		return bool(
			item.department_id and section and db.session.get(Department, item.department_id)
			and _section_department_id(section) == item.department_id
		)
	return False


def _visible_announcements():
	now = datetime.utcnow()
	department_id, section_ids, unrestricted = _read_scope()
	query = Announcement.query.filter(
		Announcement.is_active.is_(True),
		db.or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
	)
	if not unrestricted:
		scopes = [Announcement.target_type == 'institution']
		if department_id:
			scopes.append(db.and_(
				Announcement.target_type == 'department',
				Announcement.department_id == department_id,
			))
		if section_ids:
			scopes.append(db.and_(
				Announcement.target_type == 'section',
				Announcement.section_id.in_(section_ids),
			))
		query = query.filter(db.or_(*scopes))
	rows = query.order_by(Announcement.created_at.desc(), Announcement.id.desc()).all()
	return [item for item in rows if _valid_announcement_scope(item)]


def _allowed_sections():
	role = role_for(current_user)
	if role == Role.SUPER_ADMIN:
		return Section.query.join(Batch).join(Course).order_by(Section.id).all()
	if role in {Role.HOD, Role.DEPARTMENT_ADMIN}:
		department_id = department_id_for(current_user)
		if not department_id:
			return []
		return Section.query.join(Batch).join(Course).filter(
			Course.department_id == department_id,
		).order_by(Section.id).all()
	if role == Role.FACULTY:
		section_ids = _assigned_faculty_sections()
		return Section.query.filter(Section.id.in_(section_ids)).order_by(Section.id).all() if section_ids else []
	return []


def _section_department(section):
	department_id = _section_department_id(section)
	return db.session.get(Department, department_id) if department_id else None


def _payload():
	return request.get_json(silent=True) or request.form


def _positive_id(value):
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value if value > 0 else None
	if isinstance(value, str) and value.strip().isdigit():
		parsed = int(value.strip())
		return parsed if parsed > 0 else None
	return None


def _error(message, status):
	if request.is_json:
		return jsonify({'error': message}), status
	flash(message, 'danger')
	return redirect(url_for('announcements.announcements_page'))


def _parse_expiry(value):
	raw = str(value or '').strip()
	if not raw:
		return None
	if len(raw) > 64:
		raise ValueError('Enter a valid announcement expiry date and time.')
	try:
		parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
		if parsed.tzinfo is not None:
			parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
	except (ValueError, OverflowError) as error:
		raise ValueError('Enter a valid announcement expiry date and time.') from error
	if parsed <= datetime.utcnow():
		raise ValueError('Announcement expiry must be in the future.')
	return parsed


def _announcement_data(item):
	return {
		'id': item.id,
		'title': item.title,
		'body': item.body,
		'target_type': item.target_type,
		'department_id': item.department_id,
		'section_id': item.section_id,
		'created_at': item.created_at.isoformat() if item.created_at else None,
		'expires_at': item.expires_at.isoformat() if item.expires_at else None,
		'is_active': item.is_active,
	}


@announcements_bp.get('/announcements')
@login_required
def announcements_page():
	role = role_for(current_user)
	if role is None:
		abort(403)
	department_id, _section_ids, _unrestricted = _read_scope()
	can_create = role in {
		Role.SUPER_ADMIN, Role.HOD, Role.DEPARTMENT_ADMIN, Role.FACULTY,
	}
	if role in {Role.HOD, Role.DEPARTMENT_ADMIN, Role.FACULTY} and not department_id:
		can_create = False
	departments = []
	if role == Role.SUPER_ADMIN:
		departments = Department.query.order_by(Department.code).all()
	elif role in {Role.HOD, Role.DEPARTMENT_ADMIN} and department_id:
		department = db.session.get(Department, department_id)
		departments = [department] if department else []
	sections = _allowed_sections()
	section_departments = {section.id: _section_department(section) for section in sections}
	return render_template(
		'announcements.html', announcements=_visible_announcements(), can_create=can_create,
		role=role, departments=departments, sections=sections,
		section_departments=section_departments,
	)


@announcements_bp.get('/api/announcements')
@login_required
def list_announcements():
	if role_for(current_user) is None:
		abort(403)
	return jsonify([_announcement_data(item) for item in _visible_announcements()])


@announcements_bp.post('/announcements')
@announcements_bp.post('/api/announcements')
@login_required
def create_announcement():
	role = role_for(current_user)
	if role not in {Role.SUPER_ADMIN, Role.HOD, Role.DEPARTMENT_ADMIN, Role.FACULTY}:
		return _error('You are not authorized to create announcements.', 403)

	data = _payload()
	raw_title = data.get('title', '')
	raw_body = data.get('body', '')
	if not isinstance(raw_title, str) or not isinstance(raw_body, str):
		return _error('Title and message must be plain text.', 400)
	title = raw_title.strip()
	body = raw_body.strip()
	if not title or len(title) > 160:
		return _error('Title is required and must be no more than 160 characters.', 400)
	if not body or len(body) > 5000:
		return _error('Message is required and must be no more than 5000 characters.', 400)
	try:
		expires_at = _parse_expiry(data.get('expires_at'))
	except ValueError as error:
		return _error(str(error), 400)

	target_type = str(data.get('target_type', '')).strip().lower()
	raw_department = data.get('department_id')
	raw_section = data.get('section_id')
	if role == Role.SUPER_ADMIN:
		if target_type == 'institution':
			if raw_department not in (None, '') or raw_section not in (None, ''):
				return _error('Institution announcements cannot include department or section IDs.', 400)
			department_id = section_id = None
		elif target_type == 'department':
			if raw_section not in (None, ''):
				return _error('Department announcements cannot include a section ID.', 400)
			department_id = _positive_id(raw_department)
			if department_id is None:
				return _error('Select an existing department.', 400)
			if not db.session.get(Department, department_id):
				return _error('Select an existing department.', 400)
			section_id = None
		elif target_type == 'section':
			section_id = _positive_id(raw_section)
			if section_id is None:
				return _error('Select an existing section.', 400)
			section = db.session.get(Section, section_id)
			department_id = _section_department_id(section)
			if not section or not department_id:
				return _error('Select an existing section with a valid department.', 400)
			if raw_department not in (None, ''):
				requested_department_id = _positive_id(raw_department)
				if requested_department_id is None:
					return _error('The department and section do not match.', 400)
				if requested_department_id != department_id:
					return _error('The department and section do not match.', 400)
		else:
			return _error('Choose institution, department, or section scope.', 400)
	elif role in {Role.HOD, Role.DEPARTMENT_ADMIN}:
		own_department_id = department_id_for(current_user)
		if not own_department_id or not db.session.get(Department, own_department_id):
			return _error('A valid department assignment is required.', 403)
		if target_type == 'department':
			department_id = _positive_id(raw_department)
			if department_id is None:
				return _error('Select your assigned department.', 400)
			if department_id != own_department_id or not db.session.get(Department, department_id):
				return _error('Announcements can only target your assigned department.', 403)
			if raw_section not in (None, ''):
				return _error('Department announcements cannot include a section ID.', 400)
			section_id = None
		elif target_type == 'section':
			section_id = _positive_id(raw_section)
			if section_id is None:
				return _error('Select a section in your assigned department.', 400)
			section = db.session.get(Section, section_id)
			department_id = _section_department_id(section)
			if not section or department_id != own_department_id:
				return _error('Announcements can only target sections in your assigned department.', 403)
			if raw_department not in (None, ''):
				requested_department_id = _positive_id(raw_department)
				if requested_department_id is None:
					return _error('The department and section do not match.', 400)
				if requested_department_id != own_department_id:
					return _error('The department and section do not match.', 403)
		else:
			return _error('Department roles can target only their own department or its sections.', 403)
	else:
		if target_type != 'section':
			return _error('Faculty can create announcements only for assigned sections.', 403)
		section_id = _positive_id(raw_section)
		if section_id is None:
			return _error('Select one of your assigned sections.', 400)
		if section_id not in _assigned_faculty_sections():
			return _error('Faculty can create announcements only for assigned sections.', 403)
		section = db.session.get(Section, section_id)
		department_id = _section_department_id(section)
		if raw_department not in (None, ''):
			requested_department_id = _positive_id(raw_department)
			if requested_department_id is None:
				return _error('The department and section do not match.', 400)
			if requested_department_id != department_id:
				return _error('The department and section do not match.', 403)

	raw_active = data.get('is_active', True)
	if isinstance(raw_active, bool):
		is_active = raw_active
	elif str(raw_active).strip().lower() in {'true', '1', 'on'}:
		is_active = True
	elif str(raw_active).strip().lower() in {'false', '0', 'off'}:
		is_active = False
	else:
		return _error('Choose whether the announcement is active.', 400)

	item = Announcement(
		title=title, body=body, target_type=target_type,
		department_id=department_id, section_id=section_id,
		creator_id=current_user.id, created_at=datetime.utcnow(),
		expires_at=expires_at, is_active=is_active,
	)
	db.session.add(item)
	try:
		db.session.commit()
	except IntegrityError:
		db.session.rollback()
		return _error('The announcement conflicts with current data and could not be saved.', 409)

	from audit import record_security_event
	record_security_event(
		action='announcement.created', status='success', reason='announcement_created',
		actor=current_user._get_current_object(), resource_type='announcement',
		resource_id=item.id, after_data={
			'target_type': item.target_type, 'department_id': item.department_id,
			'section_id': item.section_id, 'is_active': item.is_active,
			'expires_at': item.expires_at,
		}, response_status=201,
	)
	if request.is_json:
		return jsonify(_announcement_data(item)), 201
	flash('Announcement published.', 'success')
	return redirect(url_for('announcements.announcements_page'))


