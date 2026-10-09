from datetime import datetime

from extensions import db


class SystemPolicy(db.Model):
    """Database-backed operational switches managed by Super Admins."""

    __tablename__ = 'system_policies'

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(20), nullable=False, default='false', server_default='false')
    updated_by = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, server_default=db.func.now())


class AttendanceImportStage(db.Model):
    """Short-lived, owner-bound validated attendance import preview."""

    __tablename__ = 'attendance_import_stages'
    __table_args__ = (
        db.Index('ix_attendance_import_stages_owner_status', 'owner_id', 'status'),
        db.Index('ix_attendance_import_stages_expires_at', 'expires_at'),
        db.CheckConstraint(
            "status IN ('preview', 'committed', 'cancelled', 'expired', 'failed')",
            name='ck_attendance_import_stage_status',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, server_default=db.func.now())
    expires_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='preview', server_default='preview')
    payload = db.Column(db.JSON, nullable=True)
