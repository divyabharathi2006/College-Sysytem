import math


DEFAULT_TARGET_PERCENTAGE = 75.0


def calculate_percentage(present, total):
    if total == 0:
        return 0.0
    return round((present / total) * 100, 2)


def safe_absence_count(present, total, required_percentage):
    if required_percentage <= 0:
        return 0
    if total == 0:
        return 0
    x = (100 * present / required_percentage) - total
    return max(0, int(x))


def required_classes_to_attend(current_percentage, target_percentage, total_classes):
    if target_percentage <= 0:
        return 0
    if current_percentage >= target_percentage:
        return 0
    if total_classes <= 0:
        return 0
    if target_percentage >= 100:
        return None
    present = (current_percentage / 100) * total_classes
    required_present = (target_percentage / 100) * total_classes
    needed = required_present - present
    if needed <= 0:
        return 0
    return max(1, int(math.ceil(needed / (1 - target_percentage / 100))))


def required_continuous_classes(present, total, target_percentage=DEFAULT_TARGET_PERCENTAGE):
    """Return consecutive attended classes needed to reach the target percentage."""
    if target_percentage <= 0 or total < 0 or present < 0 or present > total:
        return 0
    if present >= total and total > 0 and calculate_percentage(present, total) >= target_percentage:
        return 0
    if target_percentage >= 100:
        return None
    needed = (target_percentage * total - 100 * present) / (100 - target_percentage)
    return max(0, int(math.ceil(needed)))


def projected_attendance(present, total, upcoming_classes, expected_attendance):
    upcoming_classes = max(0, int(upcoming_classes))
    attending = max(0, min(upcoming_classes, int(expected_attendance)))
    return calculate_percentage(present + attending, total + upcoming_classes)


def risk_level(percentage):
    if percentage >= 90:
        return 'SAFE'
    if percentage >= 75:
        return 'NORMAL'
    if percentage >= 65:
        return 'WARNING'
    if percentage >= 50:
        return 'HIGH RISK'
    return 'CRITICAL'


def aggregate_attendance(attendance_records):
    total = len(attendance_records)
    present = sum(1 for item in attendance_records if item.get('status') == 'PRESENT')
    return {
        'present': present,
        'total': total,
        'percentage': calculate_percentage(present, total),
        'risk': risk_level(calculate_percentage(present, total)),
    }
