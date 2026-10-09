from flask import Blueprint, flash, render_template, request
from flask_login import current_user, login_required

from attendance_engine import (
    DEFAULT_TARGET_PERCENTAGE, calculate_percentage, required_classes_to_attend,
    risk_level, safe_absence_count,
)
from models.attendance import Attendance, LeaveRequest
from models.notifications import Notification
from models.prediction import PredictionHistory
from models.student import Student
from models.subject import Subject
from permissions import Role, can_access, has_role
from prediction_history import record_student_prediction

student_bp = Blueprint('student', __name__)


def _get_student_profile():
    return Student.query.filter_by(user_id=current_user.id).first()


@student_bp.route('/dashboard')
@login_required
def dashboard():
    if not has_role(current_user, Role.STUDENT):
        flash('You are not authorized to access that page.', 'danger')
        return render_template('login.html'), 403
    student = _get_student_profile()
    if student and not can_access(current_user, 'attendance:read_own', resource_owner_id=student.user_id):
        return render_template('login.html'), 403
    records = Attendance.query.filter_by(student_id=student.id).all() if student else []
    total = len(records)
    present = sum(1 for item in records if item.status == 'PRESENT')
    absent = sum(1 for item in records if item.status == 'ABSENT')
    percentage = calculate_percentage(present, total)
    safe = safe_absence_count(present, total, 75)
    required = required_classes_to_attend(percentage, DEFAULT_TARGET_PERCENTAGE, total)
    subject_rows = []
    for subject in (Subject.query.order_by(Subject.subject_name).all() if student else []):
        subject_records = Attendance.query.filter_by(student_id=student.id, subject_id=subject.id).all()
        if not subject_records:
            continue
        present_count = sum(1 for item in subject_records if item.status == 'PRESENT')
        total_count = len(subject_records)
        subject_pct = calculate_percentage(present_count, total_count)
        subject_rows.append({
            'name': subject.subject_name,
            'present': present_count,
            'total': total_count,
            'percentage': subject_pct,
            'risk': risk_level(subject_pct),
            'required_classes': required_classes_to_attend(
                subject_pct, DEFAULT_TARGET_PERCENTAGE, total_count,
            ),
        })
    recent_leave_requests = LeaveRequest.query.filter_by(student_id=student.id).order_by(
        LeaveRequest.requested_at.desc(), LeaveRequest.id.desc(),
    ).limit(5).all() if student else []
    saved_predictions = PredictionHistory.query.filter_by(student_id=student.id).order_by(
        PredictionHistory.created_at.desc(), PredictionHistory.id.desc(),
    ).limit(5).all() if student else []
    prediction_history = [{
        'created_at': item.created_at,
        'current_percentage': item.current_percentage,
        'predicted_percentage': item.predicted_percentage,
        'target_percentage': DEFAULT_TARGET_PERCENTAGE,
        'required_continuous_classes': required_classes_to_attend(
            item.current_percentage, DEFAULT_TARGET_PERCENTAGE,
            (item.feature_snapshot or {}).get('attendance_records', 0),
        ),
        'risk': risk_level(item.predicted_percentage),
    } for item in saved_predictions]
    unread_notifications = Notification.query.filter_by(
        user_id=current_user.id, read_at=None, dismissed_at=None,
    ).count()
    latest_prediction = PredictionHistory.query.filter_by(student_id=student.id).order_by(
        PredictionHistory.created_at.desc(), PredictionHistory.id.desc(),
    ).first() if student else None
    return render_template(
        'student_dashboard.html',
        student=student,
        overall_percentage=percentage,
        present=present,
        absent=absent,
        safe_absence=safe,
        required_classes=required,
        overall_risk=risk_level(percentage),
        target_percentage=DEFAULT_TARGET_PERCENTAGE,
        subject_rows=subject_rows,
        latest_prediction=latest_prediction,
        latest_prediction_risk=risk_level(latest_prediction.predicted_percentage) if latest_prediction else None,
        recent_leave_requests=recent_leave_requests,
        prediction_history=prediction_history,
        unread_notifications=unread_notifications,
    )


@student_bp.route('/prediction')
@login_required
def prediction():
    if not has_role(current_user, Role.STUDENT):
        flash('You are not authorized to access that page.', 'danger')
        return render_template('login.html'), 403
    student = _get_student_profile()
    if student and not can_access(current_user, 'attendance:read_own', resource_owner_id=student.user_id):
        return render_template('login.html'), 403
    records = Attendance.query.filter_by(student_id=student.id).all() if student else []
    total = len(records)
    present = sum(1 for item in records if item.status == 'PRESENT')
    result, _history = record_student_prediction(student, records) if student else ({
        'current_percentage': 0.0, 'predicted_percentage': 0.0, 'risk': 'CRITICAL',
        'target_percentage': DEFAULT_TARGET_PERCENTAGE, 'required_continuous_classes': 0,
        'recommended_action': 'Create a student profile to view attendance predictions.',
    }, None)
    history = PredictionHistory.query.filter_by(student_id=student.id).order_by(
        PredictionHistory.created_at.desc(), PredictionHistory.id.desc(),
    ).limit(25).all() if student else []
    return render_template(
        'prediction.html', student=student, current_percentage=result['current_percentage'],
        present=present, total=total, predicted_percentage=result['predicted_percentage'],
        target_percentage=result['target_percentage'],
        required_continuous_classes=result['required_continuous_classes'],
        risk=result['risk'], recommended_action=result['recommended_action'], prediction_history=[{
            'created_at': item.created_at,
            'current_percentage': item.current_percentage,
            'predicted_percentage': item.predicted_percentage,
            'target_percentage': DEFAULT_TARGET_PERCENTAGE,
            'required_continuous_classes': required_classes_to_attend(
                item.current_percentage, DEFAULT_TARGET_PERCENTAGE,
                (item.feature_snapshot or {}).get('attendance_records', 0),
            ),
            'risk': risk_level(item.predicted_percentage),
        } for item in history],
    )


@student_bp.route('/history')
@login_required
def history():
    if not has_role(current_user, Role.STUDENT):
        flash('You are not authorized to access that page.', 'danger')
        return render_template('login.html'), 403
    student = _get_student_profile()
    if student and not can_access(current_user, 'attendance:read_own', resource_owner_id=student.user_id):
        return render_template('login.html'), 403
    records = Attendance.query.filter_by(student_id=student.id).all() if student else []
    return render_template('reports.html', student=student, records=records)
