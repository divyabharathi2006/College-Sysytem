from extensions import db


class Attendance(db.Model):
    __tablename__ = 'attendance'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id', ondelete='RESTRICT'), nullable=True, index=True)
    marked_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.Index(
            'uq_attendance_legacy_student_subject_day', 'student_id', 'subject_id', 'date',
            unique=True, sqlite_where=db.text('session_id IS NULL'),
            postgresql_where=db.text('session_id IS NULL'),
        ),
        db.Index(
            'uq_attendance_session_student_subject_day', 'student_id', 'subject_id', 'date', 'session_id',
            unique=True, sqlite_where=db.text('session_id IS NOT NULL'),
            postgresql_where=db.text('session_id IS NOT NULL'),
        ),
    )

    student = db.relationship('Student', backref='attendance_records')
    subject = db.relationship('Subject', backref='attendance_records')
    session = db.relationship('ClassSession', backref='attendance_records')

    def __repr__(self):
        return f'<Attendance {self.student_id} {self.subject_id} {self.date}>'


class AttendanceCorrectionRequest(db.Model):
    __tablename__ = 'attendance_correction_requests'

    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(db.Integer, db.ForeignKey('attendance.id', ondelete='CASCADE'), nullable=False, index=True)
    previous_status = db.Column(db.String(20), nullable=False)
    new_status = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='PENDING', server_default='PENDING', index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    requester_role = db.Column(db.String(30), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    reviewer_role = db.Column(db.String(30), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    attendance = db.relationship('Attendance', backref=db.backref('correction_requests', lazy=True))
    requester = db.relationship('User', foreign_keys=[requested_by])
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    __table_args__ = (
        db.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name='ck_correction_request_status'),
        db.Index(
            'uq_attendance_correction_pending', 'attendance_id', unique=True,
            sqlite_where=db.text("status = 'PENDING'"),
            postgresql_where=db.text("status = 'PENDING'"),
        ),
    )


class LeaveRequest(db.Model):
    __tablename__ = 'leave_requests'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='PENDING', server_default='PENDING', index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    reviewer_role = db.Column(db.String(30), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    student = db.relationship('Student', backref=db.backref('leave_requests', lazy=True))
    requester = db.relationship('User', foreign_keys=[requested_by])
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])

    __table_args__ = (
        db.CheckConstraint('starts_on <= ends_on', name='ck_leave_request_dates'),
        db.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name='ck_leave_request_status'),
    )
