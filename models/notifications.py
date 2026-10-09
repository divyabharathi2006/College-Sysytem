from extensions import db


class NotificationPreference(db.Model):
    __tablename__ = 'notification_preferences'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    leave_updates = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    correction_updates = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    security_alerts = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), onupdate=db.func.now())

    user = db.relationship('User', backref=db.backref('notification_preferences', uselist=False))


class Notification(db.Model):
    __tablename__ = 'notifications'
    __table_args__ = (
        db.Index('ix_notifications_user_created', 'user_id', 'created_at'),
        db.Index('ix_notifications_user_dismissed', 'user_id', 'dismissed_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    category = db.Column(db.String(30), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), index=True)
    read_at = db.Column(db.DateTime, nullable=True)
    dismissed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('notifications', lazy=True))