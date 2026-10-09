from datetime import datetime

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models.notifications import Notification, NotificationPreference

notifications_bp = Blueprint('notifications', __name__)


def _preferences():
    return db.session.get(NotificationPreference, current_user.id)


def _preference_values(preferences):
    return {
        'leave_updates': preferences.leave_updates if preferences else True,
        'correction_updates': preferences.correction_updates if preferences else True,
        'security_alerts': preferences.security_alerts if preferences else True,
    }


def _notification_payload(item):
    return {
        'id': item.id,
        'category': item.category,
        'title': item.title,
        'message': item.message,
        'link': item.link,
        'created_at': item.created_at.isoformat() if item.created_at else None,
        'read_at': item.read_at.isoformat() if item.read_at else None,
        'dismissed_at': item.dismissed_at.isoformat() if item.dismissed_at else None,
    }


@notifications_bp.get('/api/notifications')
@login_required
def list_notifications():
    limit = min(max(request.args.get('limit', 50, type=int), 1), 100)
    items = Notification.query.filter_by(user_id=current_user.id, dismissed_at=None).order_by(
        Notification.created_at.desc(), Notification.id.desc(),
    ).limit(limit).all()
    return jsonify({'notifications': [_notification_payload(item) for item in items]})


@notifications_bp.post('/api/notifications/<int:notification_id>/read')
@login_required
def mark_notification_read(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first()
    if not item:
        abort(404)
    if item.read_at is None:
        item.read_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'message': 'Notification marked as read'})


@notifications_bp.post('/api/notifications/<int:notification_id>/dismiss')
@login_required
def dismiss_notification(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first()
    if not item:
        abort(404)
    if item.dismissed_at is None:
        item.dismissed_at = datetime.utcnow()
        db.session.commit()
    return jsonify({'message': 'Notification dismissed'})


@notifications_bp.route('/api/notification-preferences', methods=['GET', 'POST'])
@login_required
def notification_preferences_api():
    preferences = _preferences()
    if request.method == 'GET':
        return jsonify(_preference_values(preferences))
    payload = request.get_json(silent=True) or request.form
    allowed = ('leave_updates', 'correction_updates', 'security_alerts')
    updates = {}
    for key in allowed:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, bool):
            updates[key] = value
        elif str(value).lower() in {'true', '1', 'on'}:
            updates[key] = True
        elif str(value).lower() in {'false', '0', 'off'}:
            updates[key] = False
        else:
            return jsonify({'error': f'{key} must be a boolean value'}), 400
    if not updates:
        return jsonify({'error': 'Provide at least one notification preference'}), 400
    if preferences is None:
        preferences = NotificationPreference(user_id=current_user.id)
        db.session.add(preferences)
    for key, value in updates.items():
        setattr(preferences, key, value)
    preferences.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(_preference_values(preferences))


@notifications_bp.get('/notifications')
@login_required
def notifications_page():
    items = Notification.query.filter_by(user_id=current_user.id, dismissed_at=None).order_by(
        Notification.created_at.desc(), Notification.id.desc(),
    ).limit(100).all()
    return render_template('notifications.html', notifications=items)


@notifications_bp.post('/notifications/<int:notification_id>/read')
@login_required
def mark_notification_read_page(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first()
    if not item:
        abort(404)
    if item.read_at is None:
        item.read_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for('notifications.notifications_page'))


@notifications_bp.post('/notifications/<int:notification_id>/dismiss')
@login_required
def dismiss_notification_page(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=current_user.id).first()
    if not item:
        abort(404)
    item.dismissed_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('notifications.notifications_page'))


@notifications_bp.route('/notification-preferences', methods=['GET', 'POST'])
@login_required
def notification_preferences_page():
    preferences = _preferences()
    if request.method == 'POST':
        if preferences is None:
            preferences = NotificationPreference(user_id=current_user.id)
            db.session.add(preferences)
        for key in ('leave_updates', 'correction_updates', 'security_alerts'):
            setattr(preferences, key, request.form.get(key) == 'on')
        preferences.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('notifications.notification_preferences_page'))
    return render_template('notification_preferences.html', preferences=_preference_values(preferences))