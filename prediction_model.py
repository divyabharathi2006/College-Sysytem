from __future__ import annotations

from attendance_engine import (
    DEFAULT_TARGET_PERCENTAGE, calculate_percentage, required_continuous_classes, risk_level,
)

try:
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor
except Exception:  # pragma: no cover
    LinearRegression = None  # type: ignore[assignment]
    RandomForestRegressor = None  # type: ignore[assignment]


def train_model(history):
    if not history or LinearRegression is None:
        return None
    x = [[row['attendance'], row['classes_conducted'], row['classes_attended'], row['recent_trend']] for row in history]
    y = [row['future_attendance'] for row in history]
    model = LinearRegression()
    model.fit(x, y)
    return model


def predict_attendance(current_attendance, total_classes, present_classes, recent_trend=0, subject_name='General'):
    if total_classes <= 0:
        return {
            'current_percentage': 0.0,
            'predicted_percentage': 0.0,
            'target_percentage': DEFAULT_TARGET_PERCENTAGE,
            'required_continuous_classes': 0,
            'trend': 0.0,
            'risk': 'CRITICAL',
            'recommended_action': 'Need more attendance records to predict accurately.',
        }

    current_percentage = calculate_percentage(present_classes, total_classes)
    trend = recent_trend
    predicted = current_percentage + trend
    predicted = max(0, min(100, predicted))
    risk = risk_level(predicted)
    if predicted >= 85:
        action = 'Maintain current attendance and aim for consistency in upcoming classes.'
    elif predicted >= 75:
        action = 'Stay regular in attendance and avoid missing classes in the next few weeks.'
    elif predicted >= 65:
        action = 'Focus on attending the next few classes to avoid falling below the required threshold.'
    else:
        action = 'Immediate action is required. Attend all upcoming classes to recover the mandatory attendance.'

    return {
        'current_percentage': current_percentage,
        'predicted_percentage': round(predicted, 2),
        'target_percentage': DEFAULT_TARGET_PERCENTAGE,
        'required_continuous_classes': required_continuous_classes(
            present_classes, total_classes, DEFAULT_TARGET_PERCENTAGE,
        ),
        'trend': round(trend, 2),
        'risk': risk,
        'recommended_action': action,
        'subject_name': subject_name,
    }


def calculate_risk(attendance_percentage):
    return risk_level(attendance_percentage)
