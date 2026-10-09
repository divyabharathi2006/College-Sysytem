"""Create safe, persisted predictions from a student's observed attendance."""

from attendance_engine import calculate_percentage
from extensions import db
from models.prediction import PredictionHistory
from prediction_model import predict_attendance

MODEL_VERSION = 'deterministic-v1'
RECENT_WINDOW_SIZE = 10


def record_student_prediction(student, records):
    total = len(records)
    present = sum(1 for item in records if item.status == 'PRESENT')
    current = calculate_percentage(present, total)
    recent = sorted(records, key=lambda item: (item.date, item.id), reverse=True)[:RECENT_WINDOW_SIZE]
    recent_present = sum(1 for item in recent if item.status == 'PRESENT')
    recent_percentage = calculate_percentage(recent_present, len(recent))
    result = predict_attendance(
        current, total, present, recent_trend=recent_percentage - current, subject_name='Overall',
    )
    snapshot = {
        'attendance_records': total,
        'present_records': present,
        'recent_window': len(recent),
        'recent_present_records': recent_present,
        'recent_percentage': recent_percentage,
    }
    history = PredictionHistory(
        student_id=student.id,
        model_version=MODEL_VERSION,
        feature_snapshot=snapshot,
        current_percentage=current,
        predicted_percentage=result['predicted_percentage'],
        risk=result['risk'],
    )
    db.session.add(history)
    db.session.commit()
    return result, history