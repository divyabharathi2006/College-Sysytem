"""Helpers for preference-aware in-app notification delivery."""

from extensions import db
from models.notifications import Notification, NotificationPreference
from models.user import User
from permissions import Role, has_role

_PREFERENCE_FIELDS = {
    'leave': 'leave_updates',
    'correction': 'correction_updates',
    'security': 'security_alerts',
}


def preference_enabled(user_id, category):
    field = _PREFERENCE_FIELDS.get(category)
    if not field:
        return False
    preferences = db.session.get(NotificationPreference, user_id)
    return preferences is None or bool(getattr(preferences, field))


def create_notification(user_id, category, title, message, link=None):
    if not preference_enabled(user_id, category):
        return None
    notification = Notification(
        user_id=user_id, category=category, title=title[:120],
        message=message[:500], link=link[:200] if link else None,
    )
    db.session.add(notification)
    return notification


def notify_security_admins(title, message):
    recipients = [user.id for user in User.query.all() if has_role(user, Role.SUPER_ADMIN)]
    for user_id in recipients:
        create_notification(user_id, 'security', title, message, '/admin/security')

