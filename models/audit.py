from extensions import db


class SecurityEvent(db.Model):
    """Append-only record of authentication and sensitive application activity."""

    __tablename__ = 'security_events'
    __table_args__ = (
        db.Index('ix_security_events_created_at', 'created_at'),
        db.Index('ix_security_events_actor_id', 'actor_id'),
        db.Index('ix_security_events_status', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Keep an immutable historical identifier without a cascading FK update/delete.
    actor_id = db.Column(db.Integer, nullable=True)
    actor_role = db.Column(db.String(30), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    resource_type = db.Column(db.String(60), nullable=False)
    resource_id = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    status = db.Column(db.String(30), nullable=False)
    response_status = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(120), nullable=False)
    before_data = db.Column(db.JSON, nullable=True)
    after_data = db.Column(db.JSON, nullable=True)
    ip_hash = db.Column(db.String(64), nullable=False)
    user_agent = db.Column(db.String(255), nullable=False, default='')
