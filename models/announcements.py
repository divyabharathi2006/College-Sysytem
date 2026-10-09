from datetime import datetime

from extensions import db


class Announcement(db.Model):
    __tablename__ = 'announcements'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    target_type = db.Column(db.String(20), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='RESTRICT'), nullable=True, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id', ondelete='RESTRICT'), nullable=True, index=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, server_default=db.func.now(), index=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    department = db.relationship('Department')
    section = db.relationship('Section')
    creator = db.relationship('User')

    __table_args__ = (
        db.CheckConstraint(
            "target_type IN ('institution', 'department', 'section')",
            name='ck_announcement_target_type',
        ),
        db.CheckConstraint(
            "(target_type = 'institution' AND department_id IS NULL AND section_id IS NULL) OR "
            "(target_type = 'department' AND department_id IS NOT NULL AND section_id IS NULL) OR "
            "(target_type = 'section' AND department_id IS NOT NULL AND section_id IS NOT NULL)",
            name='ck_announcement_target_scope',
        ),
        db.CheckConstraint('length(trim(title)) BETWEEN 1 AND 160', name='ck_announcement_title_length'),
        db.CheckConstraint('length(trim(body)) BETWEEN 1 AND 5000', name='ck_announcement_body_length'),
        db.CheckConstraint('expires_at IS NULL OR expires_at > created_at', name='ck_announcement_expiry'),
        db.Index('ix_announcement_active_created', 'is_active', 'created_at'),
    )

    def __repr__(self):
        return f'<Announcement {self.id} {self.target_type}>'
