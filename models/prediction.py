from extensions import db


class PredictionHistory(db.Model):
    __tablename__ = 'prediction_history'
    __table_args__ = (
        db.Index('ix_prediction_history_student_created', 'student_id', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), index=True)
    model_version = db.Column(db.String(40), nullable=False)
    feature_snapshot = db.Column(db.JSON, nullable=False)
    current_percentage = db.Column(db.Float, nullable=False)
    predicted_percentage = db.Column(db.Float, nullable=False)
    risk = db.Column(db.String(20), nullable=False)

    student = db.relationship('Student', backref=db.backref('prediction_history', lazy=True))