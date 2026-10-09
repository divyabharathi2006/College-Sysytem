from flask_login import UserMixin

from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='student')
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True, index=True)
    affiliated_college_code = db.Column(db.String(30), nullable=True, index=True)
    display_name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    is_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    last_login_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_active(self):
        return bool(self.is_enabled)

    def __repr__(self):
        return f'<User {self.username}>'
