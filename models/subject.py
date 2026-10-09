from extensions import db


class Subject(db.Model):
    __tablename__ = 'subjects'

    id = db.Column(db.Integer, primary_key=True)
    subject_code = db.Column(db.String(30), unique=True, nullable=False)
    subject_name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    semester = db.Column(db.String(20), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'))
    faculty = db.relationship('Faculty', backref='subjects')

    def __repr__(self):
        return f'<Subject {self.subject_code}>'
