from app import db
from datetime import datetime, timezone
from app.utils.datetime_utils import now_local

class Appointment(db.Model):
    __tablename__ = 'appointment'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('event_slot.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False, unique=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL', onupdate='CASCADE'))
    status = db.Column(db.String(20), nullable=False, default='scheduled')  # scheduled|done|no_show|cancelled
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    # Nota: si quieres acceder a slot/event como objetos, puedes definir relaciones viewonly aquí.
    
    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'event_id': self.event_id,
            'slot_id': self.slot_id,
            'applicant_id': uuid_for(User, self.applicant_id),
            'assigned_by': uuid_for(User, self.assigned_by),
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AppointmentChangeRequest(db.Model):
    __tablename__ = 'appointment_change_request'

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    reason = db.Column(db.Text)
    suggestions = db.Column(db.Text)  # JSON textual o texto libre con propuestas
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending|accepted|rejected|cancelled
    decided_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL', onupdate='CASCADE'))
    decided_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)
    
    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'appointment_id': self.appointment_id,
            'requested_by': uuid_for(User, self.requested_by),
            'reason': self.reason,
            'suggestions': self.suggestions,
            'status': self.status,
            'decided_by': uuid_for(User, self.decided_by),
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
