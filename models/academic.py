from extensions import db


class Department(db.Model):
    __tablename__ = 'departments'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    def __repr__(self):
        return f'<Department {self.code}>'


class Course(db.Model):
    __tablename__ = 'courses'

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='RESTRICT'), nullable=False, index=True)
    code = db.Column(db.String(30), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    department = db.relationship('Department', backref=db.backref('courses', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('department_id', 'code', name='uq_course_department_code'),
        db.Index('ix_course_department_name', 'department_id', 'name'),
    )

    def __repr__(self):
        return f'<Course {self.code}>'


class AcademicYear(db.Model):
    __tablename__ = 'academic_years'

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(30), nullable=False, unique=True, index=True)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)

    __table_args__ = (db.CheckConstraint('starts_on < ends_on', name='ck_academic_year_dates'),)

    def __repr__(self):
        return f'<AcademicYear {self.label}>'


class Semester(db.Model):
    __tablename__ = 'semesters'

    id = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey('academic_years.id', ondelete='RESTRICT'), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    academic_year = db.relationship('AcademicYear', backref=db.backref('semesters', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('academic_year_id', 'number', name='uq_semester_year_number'),
        db.CheckConstraint('number > 0', name='ck_semester_positive_number'),
        db.CheckConstraint('starts_on < ends_on', name='ck_semester_dates'),
    )


class Batch(db.Model):
    __tablename__ = 'batches'

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('courses.id', ondelete='RESTRICT'), nullable=False, index=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey('academic_years.id', ondelete='RESTRICT'), nullable=False, index=True)
    code = db.Column(db.String(30), nullable=False)
    course = db.relationship('Course', backref=db.backref('batches', lazy=True))
    academic_year = db.relationship('AcademicYear', backref=db.backref('batches', lazy=True))

    __table_args__ = (db.UniqueConstraint('course_id', 'academic_year_id', 'code', name='uq_batch_course_year_code'),)


class Section(db.Model):
    __tablename__ = 'sections'

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('batches.id', ondelete='RESTRICT'), nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)
    batch = db.relationship('Batch', backref=db.backref('sections', lazy=True))

    __table_args__ = (db.UniqueConstraint('batch_id', 'code', name='uq_section_batch_code'),)


class Classroom(db.Model):
    __tablename__ = 'classrooms'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), nullable=False, unique=True, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True, index=True)
    building = db.Column(db.String(80), nullable=True)
    capacity = db.Column(db.Integer, nullable=True)
    department = db.relationship('Department', backref=db.backref('classrooms', lazy=True))

    __table_args__ = (db.CheckConstraint('capacity IS NULL OR capacity > 0', name='ck_classroom_positive_capacity'),)


class ClassSession(db.Model):
    __tablename__ = 'class_sessions'

    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id', ondelete='RESTRICT'), nullable=False, index=True)
    semester_id = db.Column(db.Integer, db.ForeignKey('semesters.id', ondelete='RESTRICT'), nullable=False, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id', ondelete='RESTRICT'), nullable=False, index=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id', ondelete='SET NULL'), nullable=True, index=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey('classrooms.id', ondelete='SET NULL'), nullable=True, index=True)
    weekday = db.Column(db.Integer, nullable=False)
    starts_at = db.Column(db.Time, nullable=False)
    ends_at = db.Column(db.Time, nullable=False)

    section = db.relationship('Section', backref=db.backref('class_sessions', lazy=True))
    semester = db.relationship('Semester', backref=db.backref('class_sessions', lazy=True))
    subject = db.relationship('Subject', backref=db.backref('class_sessions', lazy=True))
    faculty = db.relationship('Faculty', backref=db.backref('class_sessions', lazy=True))
    classroom = db.relationship('Classroom', backref=db.backref('class_sessions', lazy=True))

    __table_args__ = (
        db.CheckConstraint('weekday BETWEEN 0 AND 6', name='ck_class_session_weekday'),
        db.CheckConstraint('starts_at < ends_at', name='ck_class_session_time_range'),
        db.UniqueConstraint('section_id', 'semester_id', 'weekday', 'starts_at', name='uq_class_session_section_time'),
        db.Index('ix_class_session_faculty_time', 'faculty_id', 'weekday', 'starts_at'),
        db.Index('ix_class_session_classroom_time', 'classroom_id', 'weekday', 'starts_at'),
    )
