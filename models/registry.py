from extensions import db


class UniversityStudentRecord(db.Model):
    __bind_key__ = 'university_students'
    __tablename__ = 'university_student_records'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    register_number = db.Column(db.String(50), nullable=False, unique=True)
    roll_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    phone = db.Column(db.String(30), nullable=False, unique=True)
    gender = db.Column(db.String(30), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    father_name = db.Column(db.String(120), nullable=False)
    mother_name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.Text, nullable=False)
    community = db.Column(db.String(80), nullable=False)
    is_differently_abled = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    disability_details = db.Column(db.String(300), nullable=True)
    identification_type = db.Column(db.String(80), nullable=False)
    identification_verified = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    email_verified_at = db.Column(db.DateTime, nullable=True)


class AffiliatedStudentRecord(db.Model):
    __bind_key__ = 'affiliated_students'
    __tablename__ = 'affiliated_student_records'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    college_code = db.Column(db.String(30), nullable=False, index=True)
    registration_prefix = db.Column(db.String(10), nullable=False)
    register_number = db.Column(db.String(50), nullable=False, unique=True)
    roll_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    phone = db.Column(db.String(30), nullable=False, unique=True)
    gender = db.Column(db.String(30), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    father_name = db.Column(db.String(120), nullable=False)
    mother_name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.Text, nullable=False)
    community = db.Column(db.String(80), nullable=False)
    is_differently_abled = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    disability_details = db.Column(db.String(300), nullable=True)
    identification_type = db.Column(db.String(80), nullable=False)
    identification_verified = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    email_verified_at = db.Column(db.DateTime, nullable=True)


class AffiliatedCollegeRecord(db.Model):
    __bind_key__ = 'affiliated_colleges'
    __tablename__ = 'affiliated_college_records'

    id = db.Column(db.Integer, primary_key=True)
    college_code = db.Column(db.String(30), nullable=False, unique=True)
    registration_prefix = db.Column(db.String(10), nullable=False, unique=True)
    name = db.Column(db.String(160), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    address = db.Column(db.Text, nullable=False)
    contact_number = db.Column(db.String(30), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    affiliation_details = db.Column(db.Text, nullable=False)
    staff_details = db.Column(db.Text, nullable=False, default='')
    student_strength = db.Column(db.Integer, nullable=False, default=0)
    attendance_summary = db.Column(db.Text, nullable=False, default='')
    infrastructure_details = db.Column(db.Text, nullable=False, default='')
    courses_offered = db.Column(db.Text, nullable=False, default='')
    has_antiragging_committee = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    counseling_services = db.Column(db.Text, nullable=False, default='')
    scholarship_details = db.Column(db.Text, nullable=False, default='')
    extracurricular_activities = db.Column(db.Text, nullable=False, default='')

    __table_args__ = (
        db.CheckConstraint('student_strength >= 0', name='ck_affiliated_college_student_strength'),
    )


class StudentRegistrationContact(db.Model):
    __tablename__ = 'student_registration_contacts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False, unique=True,
    )
    normalized_email = db.Column(db.String(120), nullable=False, unique=True)
    normalized_phone = db.Column(db.String(15), nullable=False, unique=True)
