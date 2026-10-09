from extensions import db


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True)
    register_number = db.Column(db.String(50), unique=True, nullable=False)
    roll_number = db.Column(db.String(50), nullable=True, index=True)
    dob_verifier = db.Column(db.String(64), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id', ondelete='RESTRICT'), nullable=True, index=True)
    year = db.Column(db.String(20), nullable=False)
    section = db.Column(db.String(20), nullable=False)
    semester = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref='student_profile')
    section_record = db.relationship('Section', backref=db.backref('students', lazy=True))

    def __repr__(self):
        return f'<Student {self.name}>'
