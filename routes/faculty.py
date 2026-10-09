from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from attendance_engine import calculate_percentage, risk_level
from models.attendance import Attendance, AttendanceCorrectionRequest
from models.academic import ClassSession, Department
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from permissions import Role, can_access, has_role, role_for

faculty_bp = Blueprint('faculty', __name__)


def _faculty_department_scope(faculty):
    if faculty.department_id and current_user.department_id and faculty.department_id != current_user.department_id:
        return None
    return faculty.department_id or current_user.department_id or faculty.department


def _session_assigned_to_faculty(faculty, class_session, permission='student:read', date_value=None):
    if not class_session or class_session.faculty_id != faculty.id:
        return False
    subject = class_session.subject
    section = class_session.section
    department = section.batch.course.department if section and section.batch and section.batch.course else None
    department_id = department.id if department else None
    return bool(
        subject and subject.faculty_id == faculty.id and department_id
        and subject.department.strip().casefold() in {department.code.casefold(), department.name.casefold()}
        and can_access(current_user, permission, department_id=department_id)
        and (date_value is None or class_session.weekday == date_value.weekday())
    )


def _assigned_sessions(faculty, permission='student:read'):
    sessions = ClassSession.query.join(Subject, ClassSession.subject_id == Subject.id).filter(
        ClassSession.faculty_id == faculty.id, Subject.faculty_id == faculty.id,
    ).order_by(ClassSession.weekday, ClassSession.starts_at).all()
    return [item for item in sessions if _session_assigned_to_faculty(faculty, item, permission)]


def _students_for_class_session(faculty, class_session, date_value=None, permission='student:read'):
    if not _session_assigned_to_faculty(faculty, class_session, permission, date_value):
        return []
    department_id = class_session.section.batch.course.department_id
    students = Student.query.filter_by(section_id=class_session.section_id).order_by(Student.name).all()
    visible = []
    for student in students:
        if student.department_id is not None:
            matches_department = student.department_id == department_id
        else:
            value = str(student.department or '').strip().casefold()
            matches = Department.query.filter(db.or_(
                db.func.lower(db.func.trim(Department.code)) == value,
                db.func.lower(db.func.trim(Department.name)) == value,
            )).all()
            matches_department = len(matches) == 1 and matches[0].id == department_id
        if matches_department:
            visible.append(student)
    return visible


def _students_for_sessions(faculty, sessions, permission='student:read'):
    students = {}
    for class_session in sessions:
        for student in _students_for_class_session(faculty, class_session, permission=permission):
            students[student.id] = student
    return sorted(students.values(), key=lambda student: student.name)


@faculty_bp.route('/dashboard')
@login_required
def dashboard():
    if not has_role(current_user, Role.FACULTY):
        return redirect(url_for('auth.login'))
    faculty_profile = Faculty.query.filter_by(user_id=current_user.id).first()
    if not faculty_profile or not _faculty_department_scope(faculty_profile):
        return redirect(url_for('auth.login'))
    sessions = _assigned_sessions(faculty_profile)
    subject_by_id = {item.subject_id: item.subject for item in sessions if item.subject}
    subject_list = sorted(subject_by_id.values(), key=lambda item: item.subject_code)
    students = _students_for_sessions(faculty_profile, sessions)
    today = date.today()
    today_timetable = [item for item in sessions if item.weekday == today.weekday()]
    students_by_section = {}
    for class_session in sessions:
        students_by_section.setdefault(class_session.section_id, set()).update(
            student.id for student in _students_for_class_session(
                faculty_profile, class_session, permission='attendance:read',
            )
        )
    assigned_session_ids = {item.id for item in sessions}
    assigned_student_ids = set().union(*students_by_section.values()) if students_by_section else set()
    session_records = []
    if assigned_session_ids and assigned_student_ids:
        for record in Attendance.query.filter(
            Attendance.session_id.in_(assigned_session_ids),
            Attendance.student_id.in_(assigned_student_ids),
        ).all():
            class_session = record.session
            if (
                class_session and record.subject_id == class_session.subject_id
                and record.student.section_id == class_session.section_id
                and class_session.weekday == record.date.weekday()
                and _session_assigned_to_faculty(
                    faculty_profile, class_session, 'attendance:read', record.date,
                )
            ):
                session_records.append(record)
    metric_groups = {}
    for class_session in sessions:
        key = (class_session.subject_id, class_session.section_id)
        metric_groups.setdefault(key, {'session': class_session, 'records': []})
    for record in session_records:
        key = (record.subject_id, record.session.section_id)
        if key in metric_groups:
            metric_groups[key]['records'].append(record)
    class_metrics = []
    for group in metric_groups.values():
        class_session = group['session']
        section = class_session.section
        batch = section.batch
        course = batch.course
        records_for_class = group['records']
        present_count = sum(item.status == 'PRESENT' for item in records_for_class)
        class_metrics.append({
            'subject': class_session.subject.subject_name,
            'subject_code': class_session.subject.subject_code,
            'class_label': f'{course.code} · {batch.code}',
            'section': section.code,
            'present': present_count,
            'total': len(records_for_class),
            'percentage': calculate_percentage(present_count, len(records_for_class)),
        })
    stats = []
    for student in students:
        records = [
            record for record in Attendance.query.filter_by(student_id=student.id).all()
            if record.session and record.session.section_id == student.section_id
            and _session_assigned_to_faculty(faculty_profile, record.session, 'attendance:read', record.date)
        ]
        total = len(records)
        present = sum(1 for item in records if item.status == 'PRESENT')
        percentage = calculate_percentage(present, total)
        stats.append({'student': student, 'percentage': percentage, 'risk': risk_level(percentage)})
    return render_template(
        'faculty_dashboard.html', faculty=faculty_profile, subjects=subject_list,
        students=students, stats=stats, today=today, today_timetable=today_timetable,
        class_metrics=class_metrics,
    )


@faculty_bp.route('/mark-attendance', methods=['GET', 'POST'])
@login_required
def mark_attendance():
    if not has_role(current_user, Role.FACULTY):
        return redirect(url_for('auth.login'))
    faculty_profile = Faculty.query.filter_by(user_id=current_user.id).first()
    if not faculty_profile or not _faculty_department_scope(faculty_profile):
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        try:
            subject_id = int(request.form.get('subject_id', ''))
            session_id = int(request.form.get('session_id', ''))
        except (TypeError, ValueError):
            subject_id = session_id = -1
        subject = Subject.query.filter_by(id=subject_id, faculty_id=faculty_profile.id).first()
        date_value = request.form.get('date', '')
        try:
            parsed_date = datetime.strptime(date_value, '%Y-%m-%d').date()
        except ValueError:
            parsed_date = None
        class_session = db.session.get(ClassSession, session_id)
        session_valid = bool(
            subject and parsed_date and class_session
            and class_session.subject_id == subject.id
            and _session_assigned_to_faculty(faculty_profile, class_session, 'attendance:mark', parsed_date)
        )
        if not session_valid:
            flash('Select an assigned subject, valid date, and matching scheduled session.', 'danger')
            return redirect(url_for('faculty.mark_attendance'))
        students = _students_for_class_session(
            faculty_profile, class_session, parsed_date, 'attendance:mark',
        )
        allowed_student_ids = {student.id for student in students}
        submitted_ids = set()
        try:
            submitted_ids = {int(key[len('status_'):]) for key in request.form if key.startswith('status_')}
        except ValueError:
            flash('Invalid student selection for this class session.', 'danger')
            return redirect(url_for('faculty.mark_attendance'))
        if not submitted_ids.issubset(allowed_student_ids):
            flash('One or more selected students are not enrolled in this assigned class session.', 'danger')
            return redirect(url_for('faculty.mark_attendance'))
        if not students:
            flash('No students are explicitly assigned to this class section.', 'warning')
            return redirect(url_for('faculty.mark_attendance'))
        else:
            updates = []
            correction_reason = str(request.form.get('correction_reason', '')).strip()
            window_hours = max(0, current_app.config.get('ATTENDANCE_CORRECTION_WINDOW_HOURS', 24))
            for student in students:
                status = request.form.get(f'status_{student.id}', '').upper()
                if not status:
                    continue
                if status not in {'PRESENT', 'ABSENT', 'LATE', 'EXCUSED', 'OD', 'LEAVE'}:
                    updates = []
                    flash('Select a supported attendance status for each submitted student.', 'danger')
                    break
                record = Attendance.query.filter_by(
                    student_id=student.id, subject_id=subject.id, date=parsed_date, session_id=session_id,
                ).first()
                if record and record.status == status:
                    continue
                if record and (
                    record.created_at is None
                    or datetime.utcnow() - record.created_at > timedelta(hours=window_hours)
                ):
                    if not 10 <= len(correction_reason) <= 1000:
                        updates = []
                        flash('A reason between 10 and 1000 characters is required for an overdue correction.', 'danger')
                        break
                    if AttendanceCorrectionRequest.query.filter_by(
                        attendance_id=record.id, status='PENDING',
                    ).first():
                        updates = []
                        flash(f'A correction for {student.name} is already pending.', 'danger')
                        break
                    updates.append(('correction', record, student, status))
                else:
                    updates.append(('attendance', record, student, status))
            else:
                for kind, record, student, status in updates:
                    if kind == 'correction':
                        db.session.add(AttendanceCorrectionRequest(
                            attendance_id=record.id,
                            previous_status=record.status,
                            new_status=status,
                            reason=correction_reason,
                            requested_by=current_user.id,
                            requester_role=role_for(current_user).value,
                        ))
                    elif record:
                        record.status = status
                        record.marked_by = current_user.id
                    else:
                        db.session.add(Attendance(
                            student_id=student.id,
                            subject_id=subject.id,
                            date=parsed_date,
                            session_id=session_id,
                            status=status,
                            marked_by=current_user.id,
                        ))
                db.session.commit()
                flash('Attendance saved; overdue edits were submitted for review.', 'success')
                return redirect(url_for('faculty.mark_attendance'))
    subject_list = Subject.query.filter_by(faculty_id=faculty_profile.id).all()
    sessions = _assigned_sessions(faculty_profile, 'attendance:mark')
    students = []
    selected_subject_id = request.args.get('subject_id', '')
    selected_session_id = request.args.get('session_id', '')
    selected_date = request.args.get('date', '')
    if selected_subject_id and selected_session_id and selected_date:
        try:
            selected_date_value = datetime.strptime(selected_date, '%Y-%m-%d').date()
            selected_subject_id_int = int(selected_subject_id)
            selected_session_id_int = int(selected_session_id)
        except (TypeError, ValueError):
            selected_date_value = None
            selected_subject_id_int = selected_session_id_int = -1
        selected_session = db.session.get(ClassSession, selected_session_id_int)
        selected_subject = Subject.query.filter_by(
            id=selected_subject_id_int, faculty_id=faculty_profile.id,
        ).first()
        if (
            selected_date_value and selected_subject and selected_session
            and selected_session.subject_id == selected_subject.id
            and _session_assigned_to_faculty(
                faculty_profile, selected_session, 'attendance:mark', selected_date_value,
            )
        ):
            students = _students_for_class_session(
                faculty_profile, selected_session, selected_date_value, 'attendance:mark',
            )
    return render_template(
        'attendance.html', faculty=faculty_profile, subjects=subject_list, sessions=sessions,
        students=students, selected_subject_id=selected_subject_id,
        selected_session_id=selected_session_id, selected_date=selected_date,
    )
