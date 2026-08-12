from app import db
from app.utils.datetime_utils import now_local


class ProgramAdmissionInterest(db.Model):
    """
    A user asking to be told when a program reopens its admission window.

    The program page is behind @login_required, so the interested party always
    has an account: no email capture is needed, only the link between the user,
    the program and the period they are waiting for.
    """
    __tablename__ = 'program_admission_interest'

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    program_id = db.Column(
        db.Integer,
        db.ForeignKey('program.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # The period whose admission window the user is waiting for. Nullable
    # because there is not always a scheduled next period.
    period_id = db.Column(
        db.Integer,
        db.ForeignKey('academic_period.id', ondelete='SET NULL'),
        nullable=True,
    )

    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    notified_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref='admission_interests')
    program = db.relationship('Program', backref='admission_interests')
    period = db.relationship('AcademicPeriod')

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'program_id', 'period_id',
            name='uq_program_admission_interest',
        ),
    )

    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'program_id': self.program_id,
            'period_id': self.period_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'notified_at': self.notified_at.isoformat() if self.notified_at else None,
        }

    def __repr__(self):
        return f'<ProgramAdmissionInterest user={self.user_id} program={self.program_id}>'
