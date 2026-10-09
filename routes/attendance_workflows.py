from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models.academic import ClassSession, Department
from models.attendance import Attendance, AttendanceCorrectionRequest, LeaveRequest
from models.faculty import Faculty
from models.student import Student
from models.subject import Subject
from notifications_service import create_notification
from permissions import Role, can_access, department_id_for, has_role, role_for

attendance_workflows_bp = Blueprint('attendance_workflows', __name__)

NEW_ATTENDANCE_STATUSES = {'PRESENT', 'ABSENT', 'LATE', 'EXCUSED'}
COMPATIBLE_ATTENDANCE_STATUSES = NEW_ATTENDANCE_STATUSES | {'OD', 'LEAVE'}


def _department_scope(text, linked_id=None):
    if linked_id is not None:
        return linked_id
    value = str(text or '').strip()
    if not value:
        return None
    matches = Department.query.filter(db.or_(
        db.func.lower(db.func.trim(Department.code)) == value.casefold(),
        db.func.lower(db.func.trim(Department.name)) == value.casefold(),
    )).all()
    if len(matches) > 1:
        return None
    return matches[0].id if matches else value


def _student_scope(student):
    return _department_scope(student.department, student.department_id)


def _subject_scope(subject):
    return _department_scope(subject.department)


def _faculty_profile():
    return Faculty.query.filter_by(user_id=current_user.id).first()


def _can_submit_or_review_correction(record, permission):
    student_scope = _student_scope(record.student)
    subject_scope = _subject_scope(record.subject)
    if student_scope is None or subject_scope is None:
        return False
    if not can_access(current_user, permission, department_id=student_scope):
        return False
    if role_for(current_user) == Role.FACULTY:
        faculty = _faculty_profile()
        class_session = record.session
        section = class_session.section if class_session else None
        section_department_id = (
            section.batch.course.department_id if section and section.batch and section.batch.course else None
        )
        return bool(
            faculty and class_session and record.student.section_id == class_session.section_id
            and class_session.faculty_id == faculty.id
            and class_session.subject_id == record.subject_id
            and class_session.weekday == record.date.weekday()
            and record.subject.faculty_id == faculty.id
            and section_department_id
            and can_access(current_user, permission, department_id=section_department_id)
            and _same_department(student_scope, subject_scope)
            and _same_department(student_scope, section_department_id)
            and _same_department(subject_scope, section_department_id)
        )
    return has_role(current_user, Role.HOD) and _same_department(student_scope, subject_scope)


def _same_department(left, right):
    def identities(value):
        try:
            department = db.session.get(Department, int(value))
        except (TypeError, ValueError):
            department = None
        return {department.code.casefold(), department.name.casefold()} if department else {str(value).strip().casefold()}
    return bool(identities(left).intersection(identities(right)))


def _can_review_leave(student):
    if has_role(current_user, Role.FACULTY):
        faculty = _faculty_profile()
        if not faculty or not student.section_id:
            return False
        assigned_sessions = ClassSession.query.join(Subject, ClassSession.subject_id == Subject.id).filter(
            ClassSession.section_id == student.section_id,
            ClassSession.faculty_id == faculty.id,
            Subject.faculty_id == faculty.id,
        ).all()
        return any(
            session.section and session.section.batch and session.section.batch.course
            and can_access(
                current_user, 'leave:review',
                department_id=session.section.batch.course.department_id,
            )
            for session in assigned_sessions
        )
    if not has_role(current_user, Role.HOD):
        return False
    scope = _student_scope(student)
    return scope is not None and can_access(current_user, 'leave:review', department_id=scope)


def _parse_date(value):
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _workflow_response(endpoint):
    if request.is_json:
        return {'message': 'Request saved'}, 201
    return redirect(url_for(endpoint))


@attendance_workflows_bp.route('/student/leave-requests', methods=['GET', 'POST'])
@login_required
def student_leave_requests():
    if not has_role(current_user, Role.STUDENT):
        abort(403)
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student or not can_access(
        current_user, 'leave:read_own', resource_owner_id=student.user_id,
    ):
        abort(403)

    if request.method == 'POST':
        if not can_access(current_user, 'leave:create_own', resource_owner_id=student.user_id):
            abort(403)
        starts_on = _parse_date(request.form.get('starts_on'))
        ends_on = _parse_date(request.form.get('ends_on'))
        reason = str(request.form.get('reason', '')).strip()
        if not starts_on or not ends_on or starts_on > ends_on or not 10 <= len(reason) <= 1000:
            abort(400, 'Provide a valid date range and a reason between 10 and 1000 characters.')
        leave_request = LeaveRequest(
            student_id=student.id, starts_on=starts_on, ends_on=ends_on,
            reason=reason, requested_by=current_user.id,
        )
        db.session.add(leave_request)
        db.session.commit()
        flash('Leave request submitted. Approval does not create attendance records.', 'success')
        return _workflow_response('attendance_workflows.student_leave_requests')

    leave_requests = LeaveRequest.query.filter_by(student_id=student.id).order_by(
        LeaveRequest.requested_at.desc(), LeaveRequest.id.desc(),
    ).all()
    return render_template('leave_requests.html', student=student, leave_requests=leave_requests)


@attendance_workflows_bp.route('/attendance/corrections', methods=['GET', 'POST'])
@login_required
def correction_requests():
    if not has_role(current_user, Role.FACULTY, Role.HOD):
        abort(403)

    if request.method == 'POST':
        try:
            attendance_id = int(request.form.get('attendance_id', ''))
        except (TypeError, ValueError):
            abort(400, 'Select a valid attendance record.')
        record = db.session.get(Attendance, attendance_id)
        new_status = str(request.form.get('new_status', '')).strip().upper()
        reason = str(request.form.get('reason', '')).strip()
        if not record:
            abort(404)
        if not _can_submit_or_review_correction(record, 'attendance:mark' if has_role(current_user, Role.FACULTY) else 'attendance:manage'):
            abort(403)
        if new_status not in COMPATIBLE_ATTENDANCE_STATUSES or new_status == record.status:
            abort(400, 'Choose a different supported attendance status.')
        if not 10 <= len(reason) <= 1000:
            abort(400, 'Provide a reason between 10 and 1000 characters.')
        if AttendanceCorrectionRequest.query.filter_by(attendance_id=record.id, status='PENDING').first():
            abort(409, 'A correction request for this attendance record is already pending.')
        correction = AttendanceCorrectionRequest(
            attendance_id=record.id,
            previous_status=record.status,
            new_status=new_status,
            reason=reason,
            requested_by=current_user.id,
            requester_role=role_for(current_user).value,
        )
        db.session.add(correction)
        db.session.commit()
        flash('Correction request submitted for review.', 'success')
        return _workflow_response('attendance_workflows.correction_requests')

    requests_for_review = []
    for correction in AttendanceCorrectionRequest.query.order_by(
        AttendanceCorrectionRequest.requested_at.desc(), AttendanceCorrectionRequest.id.desc(),
    ).all():
        if _can_submit_or_review_correction(correction.attendance, 'attendance:review_corrections'):
            requests_for_review.append(correction)
    assigned_subjects = []
    if has_role(current_user, Role.FACULTY):
        faculty = _faculty_profile()
        if not faculty:
            abort(403)
        assigned_subjects = Subject.query.filter_by(faculty_id=faculty.id).all()
        visible_records = []
        for subject in assigned_subjects:
            for record in Attendance.query.filter_by(subject_id=subject.id).order_by(Attendance.date.desc()).limit(250):
                if _can_submit_or_review_correction(record, 'attendance:mark'):
                    visible_records.append(record)
    else:
        department_id = department_id_for(current_user)
        if department_id is None or not can_access(current_user, 'attendance:manage', department_id=department_id):
            abort(403)
        visible_records = [
            record for record in Attendance.query.order_by(Attendance.date.desc()).limit(250)
            if _same_department(_student_scope(record.student), department_id)
            and _same_department(_subject_scope(record.subject), department_id)
        ]
    return render_template(
        'correction_requests.html', correction_requests=requests_for_review,
        attendance_records=visible_records,
        back_url=url_for('faculty.dashboard') if has_role(current_user, Role.FACULTY)
        else url_for('academic_admin.academic_dashboard'),
    )


@attendance_workflows_bp.route('/attendance/corrections/<int:correction_id>/review', methods=['POST'])
@login_required
def review_correction(correction_id):
    if not has_role(current_user, Role.FACULTY, Role.HOD):
        abort(403)
    correction = db.session.get(AttendanceCorrectionRequest, correction_id)
    if not correction:
        abort(404)
    permission = 'attendance:review_corrections'
    if not _can_submit_or_review_correction(correction.attendance, permission):
        abort(403)
    if correction.status != 'PENDING':
        abort(409, 'This correction request has already been reviewed.')
    if correction.requested_by == current_user.id:
        abort(403, 'Requesters cannot review their own corrections.')
    decision = str(request.form.get('decision', '')).upper()
    if decision not in {'APPROVE', 'REJECT'}:
        abort(400, 'Choose approve or reject.')

    reviewed_at = datetime.utcnow()
    try:
        if decision == 'APPROVE':
            attendance_updated = Attendance.query.filter_by(
                id=correction.attendance_id, status=correction.previous_status,
            ).update(
                {'status': correction.new_status, 'marked_by': current_user.id},
                synchronize_session=False,
            )
            if attendance_updated != 1:
                db.session.rollback()
                abort(409, 'Attendance changed after this request was submitted; no correction was applied.')
        request_updated = AttendanceCorrectionRequest.query.filter_by(
            id=correction.id, status='PENDING',
        ).update({
            'status': 'APPROVED' if decision == 'APPROVE' else 'REJECTED',
            'reviewed_by': current_user.id,
            'reviewer_role': role_for(current_user).value,
            'reviewed_at': reviewed_at,
        }, synchronize_session=False)
        if request_updated != 1:
            db.session.rollback()
            abort(409, 'This correction request has already been reviewed.')
        create_notification(
            correction.requested_by, 'correction', 'Attendance correction reviewed',
            f'Your attendance correction request was {"approved" if decision == "APPROVE" else "rejected"}.',
            '/student/history',
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    flash(f'Correction request {correction.status.lower()}.', 'success')
    return redirect(url_for('attendance_workflows.correction_requests'))


@attendance_workflows_bp.route('/attendance/leave-requests')
@login_required
def department_leave_requests():
    if not has_role(current_user, Role.FACULTY, Role.HOD):
        abort(403)
    visible_requests = [
        leave_request for leave_request in LeaveRequest.query.order_by(
            LeaveRequest.requested_at.desc(), LeaveRequest.id.desc(),
        ).all()
        if _can_review_leave(leave_request.student)
    ]
    return render_template(
        'leave_review.html', leave_requests=visible_requests,
        back_url=url_for('faculty.dashboard') if has_role(current_user, Role.FACULTY)
        else url_for('academic_admin.academic_dashboard'),
    )


@attendance_workflows_bp.route('/attendance/leave-requests/<int:leave_id>/review', methods=['POST'])
@login_required
def review_leave_request(leave_id):
    if not has_role(current_user, Role.FACULTY, Role.HOD):
        abort(403)
    leave_request = db.session.get(LeaveRequest, leave_id)
    if not leave_request:
        abort(404)
    if not _can_review_leave(leave_request.student):
        abort(403)
    if leave_request.requested_by == current_user.id:
        abort(403, 'Requesters cannot review their own leave requests.')
    if leave_request.status != 'PENDING':
        abort(409, 'This leave request has already been reviewed.')
    decision = str(request.form.get('decision', '')).upper()
    if decision not in {'APPROVE', 'REJECT'}:
        abort(400, 'Choose approve or reject.')
    leave_updated = LeaveRequest.query.filter_by(id=leave_request.id, status='PENDING').update({
        'status': 'APPROVED' if decision == 'APPROVE' else 'REJECTED',
        'reviewed_by': current_user.id,
        'reviewer_role': role_for(current_user).value,
        'reviewed_at': datetime.utcnow(),
    }, synchronize_session=False)
    if leave_updated != 1:
        db.session.rollback()
        abort(409, 'This leave request has already been reviewed.')
    create_notification(
        leave_request.requested_by, 'leave', 'Leave request reviewed',
        f'Your leave request was {"approved" if decision == "APPROVE" else "rejected"}.',
        '/student/leave-requests',
    )
    db.session.commit()
    flash(f'Leave request {leave_request.status.lower()}. No attendance rows were created.', 'success')
    return redirect(url_for('attendance_workflows.department_leave_requests'))
