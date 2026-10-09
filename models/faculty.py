from extensions import db


class Faculty(db.Model):
    __tablename__ = 'faculty'

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref='faculty_profile')
    department_record = db.relationship('Department', backref=db.backref('faculty_members', lazy=True))

    def __repr__(self):
        return f'<Faculty {self.name}>'
